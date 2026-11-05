"""FastAPI main app — facility issue reporting system."""
import os, uuid
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException, Response, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db, seed_lookups, update_zone_labels, get_conn, next_ticket_code, UPLOAD_DIR
from .ai import classify, suggest_categories, reseed_categories
from .emailer import send
from .analytics import (MAX_BATCH, SESSION_COOKIE, SESSION_MAX_AGE, new_session,
                        prune, record, safe_record, summary, valid_session)
from .dashboard import render_dashboard, render_denied
from .assistant import config as llm_conf
from .assistant import llm as llm_mod
from .assistant.tools import Api
from . import monitor

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

def containment_score(left: str, right: str) -> float:
    """短查询被长描述包含的程度：交集 / 较短词集大小。

    纯 Jaccard 对长度差极敏感——一句 15 字的报修描述 vs 一条 40 字的
    完整工单描述，即使描述的是同一问题，Jaccard 也常低于 0.3。查重预检
    关心的是"这段新描述的内容是否已被某条工单覆盖"，所以用包含度更合适。
    要求至少 3 个共同词，避免"灯坏了"这种过短文本误命中。
    """
    a, b = normalize_issue(left), normalize_issue(right)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter < 3:
        return 0.0
    return inter / min(len(a), len(b))

def zone_match(ticket_zone: str, query_zone: str) -> bool:
    """区域匹配：完全相同，或一方是另一方的父区。

    zone 编码是分层的（YKM-4-CONC 是父区，YKM-4-CONC-COFFEE 是其中的
    咖啡车区）。用户说"4楼咖啡区"时模型可能给出父区也可能给出精确子区，
    只要有一方的层级包含关系就应视为同一位置，否则会漏掉已存在的工单。
    """
    tz, qz = (ticket_zone or "").strip().lower(), (query_zone or "").strip().lower()
    if not qz:
        return True
    return tz == qz or tz.startswith(qz + "-") or qz.startswith(tz + "-")

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
    prune()

# ---------- Session identity for analytics ----------

def session_of(request: Request, response: Response) -> str:
    """First-party, anonymous, 30-day session id.

    It is the join key that makes a funnel possible (view -> start -> submit).
    Stored as a cookie only; no IP address or user id is kept anywhere.
    """
    sid = request.cookies.get(SESSION_COOKIE)
    if not valid_session(sid):
        sid = new_session()
        response.set_cookie(
            SESSION_COOKIE, sid,
            max_age=SESSION_MAX_AGE, httponly=False, samesite="lax",
            path="/", secure=request.url.scheme == "https",
        )
    return sid

# ---------- Reporter pages ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, response: Response):
    session_of(request, response)  # cookie before the first beacon, so visit #1 is attributable
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")

@app.get("/track", response_class=HTMLResponse)
def track_page(request: Request, response: Response):
    session_of(request, response)
    return (BASE / "static" / "track.html").read_text(encoding="utf-8")

@app.get("/officer", response_class=HTMLResponse)
def officer_page(request: Request, response: Response):
    session_of(request, response)
    return (BASE / "static" / "officer.html").read_text(encoding="utf-8")

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, response: Response, days: int = 14, token: str = ""):
    """Operations dashboard — server-rendered so it survives a weak network."""
    session_of(request, response)
    if token != OFFICER_TOKEN:
        return HTMLResponse(render_denied(), status_code=403)
    window = max(1, min(days, 90))
    return HTMLResponse(render_dashboard(summary(window), window, token, monitor.generate(window)))

# ---------- Assistant (formerly a separate Flask service) ----------
# These handlers are deliberately *sync* (def, not async def): they block on
# model calls and on self-requests to /api/reports, and running them in the
# threadpool keeps the event loop free to serve those nested calls.

@app.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request, response: Response):
    """The chat page now lives in this app, not in a second deployment."""
    sid = session_of(request, response)
    safe_record("assistant_opened", session=sid, page="assistant")
    return (BASE / "static" / "assistant.html").read_text(encoding="utf-8")

@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "chat_backend": llm_conf.CHAT_BACKEND,
        "classify_backend": llm_conf.CLASSIFY_BACKEND,
        "llm_ready": llm_conf.llm_ready(),
        "monitor": "llm" if llm_conf.llm_ready() else "rules",
    }

@app.post("/api/chat")
def api_chat(request: Request, response: Response, payload: dict | None = Body(default=None)):
    data = payload or {}
    messages = data.get("messages") or []
    if not messages or not any(m.get("content") for m in messages):
        raise HTTPException(400, "messages 不能为空")
    sid = session_of(request, response)
    safe_record("assistant_message", session=sid, page="assistant",
                props={"turns": len(messages)})
    try:
        return llm_mod.chat(messages)
    except Exception as exc:
        safe_record("assistant_error", session=sid, page="assistant",
                    props={"error_type": type(exc).__name__})
        raise HTTPException(502, f"调用后端失败: {exc}")

@app.post("/api/classify")
def api_classify(payload: dict | None = Body(default=None)):
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    try:
        return llm_mod.classify(text)
    except Exception as exc:
        raise HTTPException(502, f"分类失败: {exc}")

@app.post("/api/assistant")
def api_assistant(request: Request, response: Response, payload: dict | None = Body(default=None)):
    """Ticket assistant — tool calls hit this same server, no public round-trip."""
    data = payload or {}
    messages = data.get("messages") or []
    if not messages or not any(m.get("content") for m in messages):
        raise HTTPException(400, "messages 不能为空")
    sid = session_of(request, response)
    safe_record("assistant_message", session=sid, page="assistant",
                props={"turns": len(messages)})
    api = Api(str(request.base_url), OFFICER_TOKEN)
    try:
        result = llm_mod.assistant(messages, api, image_url=str(data.get("image_url") or "").strip())
    except Exception as exc:
        safe_record("assistant_error", session=sid, page="assistant",
                    props={"error_type": type(exc).__name__})
        raise HTTPException(502, f"工单助手调用失败: {exc}")
    for step in result.get("steps", []):
        safe_record("assistant_tool_call", session=sid, page="assistant",
                    props={"tool": step.get("tool"), "status": step.get("status")})
    return result

# ---------- Watchtower: the model reading the platform ----------

@app.get("/api/monitor/brief")
def api_monitor_brief(request: Request, response: Response, days: int = 7,
                      token: str = "", refresh: int = 0):
    session_of(request, response)
    if token != OFFICER_TOKEN:
        raise HTTPException(403)
    return monitor.generate(max(1, min(days, 90)), force=bool(refresh))

@app.post("/api/monitor/refresh")
def api_monitor_refresh(days: int = 7, token: str = ""):
    """Re-run the analysis from the dashboard's refresh button."""
    if token != OFFICER_TOKEN:
        raise HTTPException(403)
    window = max(1, min(days, 90))
    monitor.generate(window, force=True)
    return RedirectResponse(f"/dashboard?days={window}&token={token}", status_code=303)

# ---------- Lookup endpoints ----------

@app.get("/api/zones")
def api_zones():
    conn = get_conn()
    rows = conn.execute("SELECT code, label FROM zones ORDER BY code").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/events")
async def collect_events(request: Request, response: Response):
    """Batch receiver for the browser tracker.

    The browser buffers events and flushes them as one beacon, so a wall-mounted
    tablet does not fire a request per keystroke. Names are re-checked here, the
    batch is capped, and unknown metadata keys never reach the table.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid event payload")
    items = payload.get("events") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and not isinstance(items, list):
        items = [payload]
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "Expected a non-empty events list")
    if len(items) > MAX_BATCH:
        raise HTTPException(400, f"At most {MAX_BATCH} events per batch")

    sid = session_of(request, response)
    ua = request.headers.get("user-agent", "")
    accepted = 0
    conn = get_conn()
    try:
        for item in items:
            if not isinstance(item, dict):
                continue
            if record(item.get("event", ""), session=sid, page=item.get("page", ""),
                      ticket_code=(item.get("ticket_code") or item.get("code")),
                      props=item.get("props"), ua=ua, conn=conn):
                accepted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise HTTPException(400, "Could not store events")
    finally:
        conn.close()
    if not accepted:
        raise HTTPException(400, "No supported events in batch")
    return {"ok": True, "accepted": accepted}

@app.post("/api/analytics")
async def analytics_event(request: Request, response: Response):
    """Legacy single-event endpoint, kept for the LLM forwards and smoke scripts.

    It now writes through the same tracker as /api/events, so old clients pick up
    session ids and device class without changing their call.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid event payload")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid event payload")
    ok = record(
        payload.get("event", ""),
        session=session_of(request, response),
        page=payload.get("page", ""),
        ticket_code=payload.get("ticket_code"),
        props=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload.get("props"),
        ua=request.headers.get("user-agent", ""),
    )
    if not ok:
        raise HTTPException(400, "Unsupported analytics event")
    return {"ok": True}

@app.get("/api/analytics/summary")
def analytics_summary(days: int = 14, token: str = ""):
    if token != OFFICER_TOKEN:
        raise HTTPException(403, "Bad officer token")
    return summary(days)

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
    response: Response,
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

    # Authoritative: the row is committed, so this counts as a real submission.
    safe_record(
        "report_submitted",
        session=session_of(request, response),
        page="index",
        ticket_code=code,
        props={"priority": priority, "group": group,
               "has_photo": bool(photo_path), "source": "web"},
        ua=request.headers.get("user-agent", ""),
    )

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
def get_report(code: str, request: Request, response: Response):
    conn = get_conn()
    t = conn.execute("SELECT * FROM tickets WHERE code=?", (code,)).fetchone()
    if not t:
        conn.close()
        safe_record("track_lookup", session=session_of(request, response), page="track",
                    ticket_code=code, props={"found": False},
                    ua=request.headers.get("user-agent", ""))
        raise HTTPException(404, "Ticket not found")
    events = conn.execute("SELECT * FROM status_events WHERE ticket_id=? ORDER BY created_at", (t["id"],)).fetchall()
    conn.close()
    safe_record("track_lookup", session=session_of(request, response), page="track",
                ticket_code=code, props={"found": True, "status": t["status"]},
                ua=request.headers.get("user-agent", ""))
    return {"ticket": dict(t), "events": [dict(e) for e in events]}

# ---------- Officer endpoints ----------

@app.get("/api/queue")
def queue(request: Request, response: Response, status: str = "NEW", token: str = ""):
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
    safe_record("officer_queue_view", session=session_of(request, response), page="officer",
                props={"status": status, "count": len(rows)},
                ua=request.headers.get("user-agent", ""))
    return [dict(r) for r in rows]

@app.get("/api/duplicates")
def detect_duplicates(request: Request, response: Response, token: str = "", threshold: float = 0.34, days: int = 30,
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
            if not zone_match(t.get("zone_code"), zone):
                continue
            t_text = f"{(t.get('description') or '')} {(t.get('symptom') or '')}"
            # 两个分位：Jaccard 判"整体像不像"，containment 判"新描述是否已被
            # 这条工单覆盖"。取较大者，短描述才不会被长工单描述稀释掉。
            score = max(similarity_score(text, t_text), containment_score(text, t_text))
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
        safe_record("officer_duplicates_check", session=session_of(request, response),
                    page="officer", props={"mode": "precheck", "count": len(matches)},
                    ua=request.headers.get("user-agent", ""))
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
    safe_record("officer_duplicates_check", session=session_of(request, response),
                page="officer", props={"mode": "clusters", "count": len(clusters)},
                ua=request.headers.get("user-agent", ""))
    return clusters

@app.post("/api/compilations")
async def compile_tickets(request: Request, response: Response, token: str = Form(...)):
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
    cur = conn.execute("INSERT INTO ticket_compilations (title,zone_code,group_code,created_at) VALUES (?,?,?,?) RETURNING id",
                       (f"{tickets[0]['group_code']} reports · {tickets[0]['zone_code']}", tickets[0]["zone_code"], tickets[0]["group_code"], datetime.now().isoformat()))
    compilation_id = cur.fetchone()[0]
    for t in tickets:
        score = max((similarity_score((t.get("description") or "") + " " + (t.get("symptom") or ""), (other.get("description") or "") + " " + (other.get("symptom") or "")) for other in tickets if other["id"] != t["id"]), default=1.0)
        conn.execute("INSERT INTO compilation_members (compilation_id,ticket_id,similarity) VALUES (?,?,?)", (compilation_id,t["id"],score))
    conn.commit()
    conn.close()
    safe_record("officer_compilation", session=session_of(request, response), page="officer",
                props={"count": len(tickets), "group": tickets[0]["group_code"], "zone": tickets[0]["zone_code"]},
                ua=request.headers.get("user-agent", ""))
    return {"ok": True, "compilation_id": compilation_id, "ticket_count": len(tickets)}

@app.post("/api/queue/{code}/claim")
def claim(code: str, request: Request, response: Response, token: str = Form(...)):
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
    conn.commit(); conn.close()
    safe_record("officer_claim", session=session_of(request, response), page="officer",
                ticket_code=code, props={"priority": t["priority"], "group": t["group_code"]},
                ua=request.headers.get("user-agent", ""))
    if email:
        send(email, f"Your report {code} is being worked on",
             f"Ticket {code}\nAn officer has claimed your report.")
    return {"ok": True}

@app.post("/api/queue/{code}/resolve")
def resolve(code: str, request: Request, response: Response, note: str = Form(""), token: str = Form(...)):
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
    conn.commit(); conn.close()
    safe_record("officer_resolve", session=session_of(request, response), page="officer",
                ticket_code=code, props={"from_status": "ASSIGNED", "to_status": "RESOLVED",
                                         "has_note": bool(note)},
                ua=request.headers.get("user-agent", ""))
    if email:
        send(email, f"Your report {code} is resolved",
             f"Ticket {code} has been marked resolved.\nNote: {note}")
    return {"ok": True}

@app.post("/api/queue/{code}/recategorize")
def recategorize(code: str, request: Request, response: Response, new_category: str = Form(...), token: str = Form(...)):
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
    conn.commit(); conn.close()
    safe_record("officer_recategorize", session=session_of(request, response), page="officer",
                ticket_code=code, props={"group": t["group_code"], "category": new_category},
                ua=request.headers.get("user-agent", ""))
    return {"ok": True}
