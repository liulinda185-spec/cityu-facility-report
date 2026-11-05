"""FastAPI main app — facility issue reporting system."""
import os, uuid
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db, seed_lookups, update_zone_labels, get_conn, next_ticket_code, UPLOAD_DIR
from .ai import classify, suggest_categories, reseed_categories
from .emailer import send

app = FastAPI(title="CityU Facility Report")
BASE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

def normalize_issue(value: str) -> set[str]:
    import re
    text = (value or "").lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    stop = {"the", "a", "an", "at", "near", "in", "on", "of", "for", "is", "are", "was", "with", "and", "to", "please", "there", "this", "that", "issue", "problem", "area", "4f", "4"}
    tokens = words - stop
    # 中文：连续汉字段做 bigram（去掉虚词后），让中文描述也能参与相似度
    cjk_stop = set("的了在是有就没都又很多少一下也还跟和与及或吗呢吧啊哦嗯请")
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        seg = "".join(ch for ch in seg if ch not in cjk_stop)
        if len(seg) >= 2:
            tokens.update(seg[i:i + 2] for i in range(len(seg) - 1))
        elif seg:
            tokens.add(seg)
    return tokens

def similarity_score(left: str, right: str) -> float:
    a, b = normalize_issue(left), normalize_issue(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

# Security: no hardcoded default. Without OFFICER_TOKEN we generate a random
# token and print it once, so an unconfigured public deployment cannot be
# accessed with a known password.
OFFICER_TOKEN = os.getenv("OFFICER_TOKEN")
if not OFFICER_TOKEN:
    OFFICER_TOKEN = uuid.uuid4().hex
    print("WARNING: OFFICER_TOKEN not set - generated a random token.", flush=True)
    print(f"OFFICER_TOKEN={OFFICER_TOKEN}", flush=True)

@app.on_event("startup")
def startup():
    init_db()
    seed_lookups()
    update_zone_labels()
    reseed_categories()

# ---------- Reporter pages ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")

@app.get("/track", response_class=HTMLResponse)
def track_page():
    return (BASE / "static" / "track.html").read_text(encoding="utf-8")

@app.get("/officer", response_class=HTMLResponse)
def officer_page():
    return (BASE / "static" / "officer.html").read_text(encoding="utf-8")

# ---------- Lookup endpoints ----------

@app.get("/api/zones")
def api_zones():
    conn = get_conn()
    rows = conn.execute("SELECT code, label FROM zones ORDER BY code").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/analytics")
async def analytics_event(request: Request):
    """Record a small allowlisted set of events, without report content or identity."""
    import json
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid event payload")
    event_name = str(payload.get("event", ""))
    allowed = {"page_view", "report_submitted", "ticket_claimed", "ticket_resolved", "category_corrected", "ticket_tracked",
               "assistant_opened", "assistant_message", "assistant_tool_call", "assistant_ticket_created",
               "assistant_duplicates_found", "assistant_error"}
    if event_name not in allowed:
        raise HTTPException(400, "Unsupported analytics event")
    page = str(payload.get("page", ""))[:32]
    ticket_code = str(payload.get("ticket_code", ""))[:32] or None
    metadata = payload.get("metadata", {})
    safe_metadata = {k: metadata[k] for k in ("priority", "group", "status") if isinstance(metadata, dict) and k in metadata}
    conn = get_conn()
    conn.execute("INSERT INTO analytics_events (event_name,page,ticket_code,metadata,created_at) VALUES (?,?,?,?,?)",
                 (event_name, page, ticket_code, json.dumps(safe_metadata), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/category-options")
def category_options(token: str = ""):
    if token != OFFICER_TOKEN:
        raise HTTPException(403, "Bad officer token")
    conn = get_conn()
    rows = conn.execute("SELECT code, portal_name, group_code FROM categories ORDER BY group_code, portal_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/suggest")
def api_suggest(text: str = "", symptom: str = "", asset_code: str = ""):
    group, conf, probs = classify(text, symptom, asset_code)
    cats = suggest_categories(group)
    return {"group": group, "confidence": conf, "categories": cats}

# ---------- Submit ticket ----------

@app.post("/api/reports")
async def create_report(
    request: Request,
    zone_code: str = Form(...),
    asset_code: str = Form(""),
    symptom: str = Form(""),
    impact: str = Form("P3"),
    description: str = Form(...),
    reporter_email: str = Form(""),
    category_code: str = Form(""),
    photo: UploadFile = File(None),
):
    group, conf, _ = classify(description, symptom, asset_code)

    # If reporter confirmed a specific portal category, use it; else use group default
    final_category = category_code or None

    # Priority from impact
    priority = {"BLOCKED": "P1", "SAFETY": "P1", "EMERGENCY": "P1",
                "MINOR": "P2", "INCONVENIENCE": "P3"}.get(impact, "P3")

    code = next_ticket_code()
    photo_path = None
    if photo and photo.filename:
        ext = os.path.splitext(photo.filename)[1] or ".jpg"
        photo_path = f"{code}{ext}"
        with open(UPLOAD_DIR / photo_path, "wb") as f:
            f.write(await photo.read())

    now = datetime.now().isoformat()
    conn = get_conn()
    conn.execute("""
        INSERT INTO tickets (code, zone_code, asset_code, category_code, group_code,
                             symptom, impact, priority, status, description, photo_path,
                             reporter_email, ai_confidence, ai_predicted, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (code, zone_code, asset_code, final_category, group, symptom, impact, priority,
          "NEW", description, photo_path, reporter_email, conf, group, now, now))
    ticket_id = conn.execute("SELECT id FROM tickets WHERE code=?", (code,)).fetchone()["id"]
    conn.execute("INSERT INTO status_events (ticket_id, from_status, to_status, note, created_at) VALUES (?,?,?,?,?)",
                 (ticket_id, None, "NEW", "Ticket submitted", now))
    import json
    conn.execute("INSERT INTO analytics_events (event_name,page,ticket_code,metadata,created_at) VALUES (?,?,?,?,?)",
                 ("report_submitted", "reporter", code, json.dumps({"priority": priority, "group": group}), now))
    conn.commit()

    # Route email: if specific category chosen, use its team_email; else group inbox
    team_email = "fm-general@cityu.local"
    if final_category:
        row = conn.execute("SELECT team_email, portal_name FROM categories WHERE code=?", (final_category,)).fetchone()
        if row: team_email = row["team_email"] or team_email
    else:
        # fallback inbox per group
        group_inbox = {
            "SIGN": "fm-signage@cityu.local", "CLEAN": "fm-cleaning@cityu.local",
            "HVAC": "fm-hvac@cityu.local", "SAFE": "fm-safety@cityu.local",
            "EVENT": "fm-event@cityu.local", "MOVE": "fm-moving@cityu.local",
            "RENO": "fm-reno@cityu.local", "SECU": "fm-security@cityu.local",
            "AUTO": "fm-building@cityu.local", "KEY": "fm-keys@cityu.local",
            "LAND": "fm-landscape@cityu.local", "LOCK": "fm-lockers@cityu.local",
            "MAIL": "fm-mail@cityu.local",
            "OTHR": "fm-general@cityu.local",
        }
        team_email = group_inbox.get(group, "fm-general@cityu.local")
    conn.close()

    body = f"""New facility report {code}
Zone: {zone_code}
Asset: {asset_code or '(none)'}
Category (AI): {group} (confidence {conf:.2f})
Category (confirmed): {final_category or '(pending officer)'}
Symptom: {symptom}
Impact: {impact} → Priority {priority}
Description: {description}
Photo: {'uploaded' if photo_path else 'none'}
Track: http://host/track?code={code}
Claim: http://host/officer
"""
    send(team_email, f"[{priority}] {code} — {description[:60]}", body)
    if reporter_email:
        send(reporter_email, f"Your report {code} has been submitted",
             f"Ticket {code}\nWe'll email you when status changes.\nTrack: http://host/track?code={code}")

    return {"code": code, "group": group, "confidence": conf}

# ---------- Track ticket ----------

@app.get("/api/reports/{code}")
def get_report(code: str):
    conn = get_conn()
    t = conn.execute("SELECT * FROM tickets WHERE code=?", (code,)).fetchone()
    if not t:
        raise HTTPException(404, "Ticket not found")
    events = conn.execute("SELECT * FROM status_events WHERE ticket_id=? ORDER BY created_at", (t["id"],)).fetchall()
    conn.close()
    return {"ticket": dict(t), "events": [dict(e) for e in events]}

# ---------- Officer endpoints ----------

@app.get("/api/queue")
def queue(status: str = "NEW", token: str = ""):
    if token != OFFICER_TOKEN:
        raise HTTPException(403, "Bad officer token")
    conn = get_conn()
    rows = conn.execute("""
        SELECT t.*,
          (SELECT c.id FROM compilation_members m JOIN ticket_compilations c ON c.id=m.compilation_id
           WHERE m.ticket_id=t.id AND c.status='OPEN' LIMIT 1) AS compilation_id,
          (SELECT COUNT(*) FROM compilation_members m WHERE m.compilation_id=(
           SELECT c.id FROM compilation_members m2 JOIN ticket_compilations c ON c.id=m2.compilation_id
           WHERE m2.ticket_id=t.id AND c.status='OPEN' LIMIT 1)) AS compiled_count
        FROM tickets t WHERE status=? ORDER BY
        CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4 END,
        created_at
    """, (status,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/duplicates")
def detect_duplicates(token: str = "", threshold: float = 0.34, days: int = 30,
                      query_text: str = "", query_zone: str = ""):
    """重复检测。

    - 无 query_text：聚类库内已有工单（返回簇列表，向后兼容）。
    - 有 query_text：预检模式——把这段新描述与库内工单逐条比对，
      返回相似工单（用于「建单前查重」，由 LLM 助手调用）。
    """
    if token != OFFICER_TOKEN:
        raise HTTPException(403, "Bad officer token")
    threshold = min(max(threshold, 0.1), 1.0)
    conn = get_conn()
    rows = conn.execute("SELECT * FROM tickets WHERE created_at >= datetime('now', ?) ORDER BY created_at DESC", (f"-{max(1,min(days,365))} days",)).fetchall()
    tickets = [dict(r) for r in rows]

    if (query_text or "").strip():
        # 预检模式：新描述 vs 已有工单
        text = query_text.strip()
        zone = (query_zone or "").strip().lower()
        matches = []
        for t in tickets:
            t_zone = (t.get("zone_code") or "").strip().lower()
            if zone and t_zone != zone:
                continue
            t_text = f"{(t.get('description') or '')} {(t.get('symptom') or '')}"
            score = similarity_score(text, t_text)
            if score >= threshold:
                matches.append({
                    "ticket": {"code": t["code"], "status": t["status"],
                               "group_code": t["group_code"], "priority": t["priority"],
                               "zone_code": t["zone_code"],
                               "description": (t["description"] or "")[:120],
                               "created_at": t["created_at"]},
                    "similarity": round(score, 3),
                })
        matches.sort(key=lambda m: -m["similarity"])
        conn.close()
        return {"query": query_text, "matches": matches}

    # 聚类模式：仅在完全相同 zone 与分类组内找重复
    clusters = []
    assigned = set()
    for i, first in enumerate(tickets):
        if first["id"] in assigned:
            continue
        cluster = [{"ticket": first, "similarity": 1.0}]
        for other in tickets[i + 1:]:
            if other["id"] in assigned:
                continue
            same_asset = (first.get("asset_code") or "").strip().lower() == (other.get("asset_code") or "").strip().lower()
            same_zone = first.get("zone_code") == other.get("zone_code")
            same_group = first.get("group_code") == other.get("group_code")
            if same_zone and same_group and (same_asset or not first.get("asset_code") or not other.get("asset_code")):
                score = similarity_score((first.get("description") or "") + " " + (first.get("symptom") or ""), (other.get("description") or "") + " " + (other.get("symptom") or ""))
                if score >= threshold:
                    cluster.append({"ticket": other, "similarity": round(score, 3)})
        if len(cluster) > 1:
            assigned.update(x["ticket"]["id"] for x in cluster)
            clusters.append({"zone_code": first.get("zone_code"), "group_code": first.get("group_code"), "tickets": cluster})
    conn.close()
    return clusters

@app.post("/api/compilations")
async def compile_tickets(request: Request, token: str = Form(...)):
    if token != OFFICER_TOKEN:
        raise HTTPException(403, "Bad officer token")
    form = await request.form()
    ticket_codes = form.getlist("ticket_codes")
    if len(set(ticket_codes)) < 2:
        raise HTTPException(400, "Select at least two tickets")
    conn = get_conn()
    placeholders = ",".join("?" for _ in set(ticket_codes))
    rows = conn.execute(f"SELECT * FROM tickets WHERE code IN ({placeholders})", tuple(set(ticket_codes))).fetchall()
    tickets = [dict(r) for r in rows]
    if len(tickets) != len(set(ticket_codes)):
        conn.close()
        raise HTTPException(404, "One or more tickets were not found")
    if len({t["zone_code"] for t in tickets}) != 1 or len({t["group_code"] for t in tickets}) != 1:
        conn.close()
        raise HTTPException(400, "Tickets must share the same area and issue group")
    cur = conn.execute("INSERT INTO ticket_compilations (title,zone_code,group_code,created_at) VALUES (?,?,?,?)",
                       (f"{tickets[0]['group_code']} reports · {tickets[0]['zone_code']}", tickets[0]["zone_code"], tickets[0]["group_code"], datetime.now().isoformat()))
    compilation_id = cur.lastrowid
    for t in tickets:
        score = max((similarity_score((t.get("description") or "") + " " + (t.get("symptom") or ""), (other.get("description") or "") + " " + (other.get("symptom") or "")) for other in tickets if other["id"] != t["id"]), default=1.0)
        conn.execute("INSERT INTO compilation_members (compilation_id,ticket_id,similarity) VALUES (?,?,?)", (compilation_id,t["id"],score))
    conn.commit()
    conn.close()
    return {"ok": True, "compilation_id": compilation_id, "ticket_count": len(tickets)}

@app.post("/api/queue/{code}/claim")
def claim(code: str, token: str = Form(...)):
    if token != OFFICER_TOKEN: raise HTTPException(403)
    conn = get_conn()
    t = conn.execute("SELECT * FROM tickets WHERE code=?", (code,)).fetchone()
    if not t:
        conn.close()
        raise HTTPException(404, "Ticket not found")
    tid = t["id"]; email = t["reporter_email"] or ""
    conn.execute("UPDATE tickets SET status='ASSIGNED', updated_at=? WHERE id=?",
                 (datetime.now().isoformat(), tid))
    conn.execute("INSERT INTO status_events (ticket_id, from_status, to_status, note, created_at) VALUES (?,?,?,?,?)",
                 (tid, "NEW", "ASSIGNED", "Claimed by officer", datetime.now().isoformat()))
    conn.execute("INSERT INTO analytics_events (event_name,page,ticket_code,metadata,created_at) VALUES (?,?,?,?,?)",
                 ("ticket_claimed", "officer", code, "{}", datetime.now().isoformat()))
    conn.commit(); conn.close()
    if email:
        send(email, f"Your report {code} is being worked on",
             f"Ticket {code}\nAn officer has claimed your report.")
    return {"ok": True}

@app.post("/api/queue/{code}/resolve")
def resolve(code: str, note: str = Form(""), token: str = Form(...)):
    if token != OFFICER_TOKEN: raise HTTPException(403)
    conn = get_conn()
    t = conn.execute("SELECT * FROM tickets WHERE code=?", (code,)).fetchone()
    if not t:
        conn.close()
        raise HTTPException(404, "Ticket not found")
    tid = t["id"]; email = t["reporter_email"] or ""
    conn.execute("UPDATE tickets SET status='RESOLVED', updated_at=? WHERE id=?",
                 (datetime.now().isoformat(), tid))
    conn.execute("INSERT INTO status_events (ticket_id, from_status, to_status, note, created_at) VALUES (?,?,?,?,?)",
                 (tid, "ASSIGNED", "RESOLVED", note, datetime.now().isoformat()))
    conn.execute("INSERT INTO analytics_events (event_name,page,ticket_code,metadata,created_at) VALUES (?,?,?,?,?)",
                 ("ticket_resolved", "officer", code, "{}", datetime.now().isoformat()))
    conn.commit(); conn.close()
    if email:
        send(email, f"Your report {code} is resolved",
             f"Ticket {code} has been marked resolved.\nNote: {note}")
    return {"ok": True}

@app.post("/api/queue/{code}/recategorize")
def recategorize(code: str, new_category: str = Form(...), token: str = Form(...)):
    """Officer corrects category — logs training data."""
    if token != OFFICER_TOKEN: raise HTTPException(403)
    conn = get_conn()
    t = conn.execute("SELECT * FROM tickets WHERE code=?", (code,)).fetchone()
    if not t:
        conn.close()
        raise HTTPException(404, "Ticket not found")
    conn.execute("UPDATE tickets SET category_code=?, updated_at=? WHERE id=?",
                 (new_category, datetime.now().isoformat(), t["id"]))
    conn.execute("INSERT INTO training_examples (text, label, source) VALUES (?,?,?)",
                 (t["description"], new_category, "officer_correction"))
    conn.execute("INSERT INTO analytics_events (event_name,page,ticket_code,metadata,created_at) VALUES (?,?,?,?,?)",
                 ("category_corrected", "officer", code, "{}", datetime.now().isoformat()))
    conn.commit(); conn.close()
    return {"ok": True}

