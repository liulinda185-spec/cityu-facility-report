# -*- coding: utf-8 -*-
"""End-to-end check for the user-event tracker (local server on 8013).

Start the app against a throwaway database first, e.g.:

    $env:FACILITY_DB_PATH="$env:TEMP\\fr-events-test.db"
    $env:OFFICER_TOKEN="testtoken123"
    python -m uvicorn app.main:app --port 8013

then run this file. It drives the real HTTP surface with a cookie jar, so the
session key that makes the funnel work is actually exercised — not mocked.
"""
import http.cookiejar
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv("EVENTS_TEST_BASE", "http://127.0.0.1:8013")
TOKEN = os.getenv("OFFICER_TOKEN", "testtoken123")
DB_FILE = os.getenv("FACILITY_DB_PATH", "")

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def call(open_obj, path, data=None, json_body=None, method=None):
    url = BASE + path
    body = None
    headers = {}
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"), headers=headers)
    try:
        with open_obj.open(req, timeout=25) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = None
    return status, payload, raw


def cookie_value(jar, name):
    for c in jar:
        if c.name == name:
            return c.value
    return None


# ---------------------------------------------------------------- 1. tracker shipped
op, jar = opener()
status, _, html = call(op, "/")
check("GET / 200", status == 200, status)
check("form page loads the tracker", b"/static/tracker.js" in html)
sid = cookie_value(jar, "fr_sid")
check("session cookie issued on first visit", bool(sid and len(sid) == 32), sid)

status, _, _ = call(op, "/static/tracker.js")
check("tracker.js served from /static", status == 200, status)

# ---------------------------------------------------------------- 2. batch intake
events = [
    {"event": "page_view", "page": "index"},
    {"event": "report_form_started", "page": "index"},
    {"event": "report_submit_attempt", "page": "index"},
    {"event": "report_ai_shown", "page": "index", "props": {"ai_group": "HVAC", "confidence": 71}},
]
status, body, _ = call(op, "/api/events", json_body={"events": events})
check("POST /api/events accepts a batch", status == 200 and (body or {}).get("accepted") == 4, body)

status, body, _ = call(op, "/api/analytics", json_body={"event": "page_view", "page": "index"})
check("legacy single-event endpoint still works", status == 200 and (body or {}).get("ok"), body)
check("same session across requests", cookie_value(jar, "fr_sid") == sid)

# ---------------------------------------------------------------- 3. guard rails
status, _, _ = call(op, "/api/events", json_body={"events": [{"event": "definitely_not_real"}]})
check("unknown event rejected", status == 400, status)

status, _, _ = call(op, "/api/events", json_body={"events": [{"event": "page_view"}] * 21})
check("batch larger than 20 rejected", status == 400, status)

status, _, _ = call(op, "/api/analytics", json_body={"event": "not_allowed"})
check("legacy endpoint rejects unknown event", status == 400, status)

# props must not carry report content
status, body, _ = call(op, "/api/events", json_body={"events": [{
    "event": "report_submit_attempt", "page": "index",
    "props": {"priority": "P1", "reporter_email": "leak@test.local",
              "description": "secret text", "zone": "YKM-4-CONC-MAIN"},
}]})
check("over-posted props accepted (allowlisted part only)", status == 200, body)

if DB_FILE and os.path.exists(DB_FILE):
    conn = sqlite3.connect(DB_FILE)
    rows = [r[0] for r in conn.execute("SELECT props FROM user_events ORDER BY id DESC LIMIT 5")]
    conn.close()
    joined = " ".join(rows)
    check("reporter_email never stored", "leak@test.local" not in joined, joined[:60])
    check("description never stored", "secret text" not in joined)

# ---------------------------------------------------------------- 4. server-side events
status, body, _ = call(op, "/api/reports", data={
    "zone_code": "YKM-4-CONC-COFFEE", "asset_code": "", "symptom": "leak",
    "impact": "SAFETY", "description": "Water pooling near the coffee cart",
    "reporter_email": "", "category_code": "",
})
code = (body or {}).get("code", "")
check("POST /api/reports 200", status == 200 and bool(code), body)

status, body, _ = call(op, f"/api/reports/{code}")
check("GET /api/reports/{code}", status == 200 and body["ticket"]["status"] == "NEW", status)

status, _, _ = call(op, "/api/reports/FM-2026-999999")
check("missing ticket returns 404", status == 404, status)

status, _, _ = call(op, f"/api/queue?status=NEW&token={TOKEN}")
check("GET /api/queue", status == 200, status)
status, _, _ = call(op, f"/api/queue/{code}/claim", data={"token": TOKEN})
check("claim", status == 200, status)
status, _, _ = call(op, f"/api/queue/{code}/recategorize", data={"token": TOKEN, "new_category": "HVAC-AHU"})
check("recategorize", status == 200, status)
status, _, _ = call(op, f"/api/queue/{code}/resolve", data={"token": TOKEN, "note": "fixed"})
check("resolve", status == 200, status)

# ---------------------------------------------------------------- 5. a second visitor
op2, jar2 = opener()
call(op2, "/api/events", json_body={"events": [{"event": "page_view", "page": "index"}]})
check("second visitor gets its own session", cookie_value(jar2, "fr_sid") != sid)

# ---------------------------------------------------------------- 6. read side
status, _, _ = call(op, "/api/analytics/summary?token=wrong")
check("summary refuses a bad token", status == 403, status)
status, data, _ = call(op, f"/api/analytics/summary?days=14&token={TOKEN}")
check("summary returns JSON", status == 200 and isinstance(data, dict), status)
funnel = (data or {}).get("funnel", {})
check("funnel counted in sessions", funnel.get("views", 0) >= 2, funnel)
check("funnel sees the started step", funnel.get("started", 0) >= 1, funnel)
check("funnel sees an accepted submission", funnel.get("submitted", 0) >= 1, funnel)
tickets = (data or {}).get("tickets", {})
check("ticket ops computed", tickets.get("resolved", 0) >= 1 and tickets.get("time_to_claim_p50_h") is not None, tickets.get("resolved"))
check("correction rate computed", (data or {}).get("corrections", {}).get("count", 0) >= 1, (data or {}).get("corrections"))
check("lookups recorded with misses", (data or {}).get("tracking", {}).get("lookups", 0) >= 2, (data or {}).get("tracking"))
check("device class present", "desktop" in (data or {}).get("devices", {}))

status, _, html = call(op, "/dashboard")
check("dashboard needs the token", status == 403 and b"officer token" in html, status)
status, _, html = call(op, f"/dashboard?days=14&token={TOKEN}")
check("dashboard renders", status == 200 and b"Operations dashboard" in html and b"Reporter funnel" in html, status)
check("dashboard has no template leak", b"{funnel" not in html and b"{{" not in html)

print("\n=== SUMMARY ===")
print(f"PASS={len(passed)}  FAIL={len(failed)}")
for name in failed:
    print("  FAILED:", name)
