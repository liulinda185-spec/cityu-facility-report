"""Merged-app checks: one server must serve the form, the assistant and the
watchtower. Run against a live instance:

    .venv\\Scripts\\python.exe test_merged_app.py   (BASE defaults to :8013)
"""
import http.cookiejar
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv("TEST_BASE", "http://127.0.0.1:8013")
TOKEN = os.getenv("OFFICER_TOKEN", "testtoken123")

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

passed, failed = 0, 0


def check(name, condition, extra=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {extra}")


def get(path):
    try:
        with opener.open(BASE + path, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def post(path, data):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(data).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# --- pages ---------------------------------------------------------------
status, body, _ = get("/assistant")
check("GET /assistant is 200", status == 200, status)
check("/assistant loads the chat shell", "chatBackend" in body or "AI 演示台" in body)
check("/assistant does not leak the Jinja placeholder", "{{" not in body)
cookie = [c.name for c in jar]
check("assistant page issues a session cookie", "fr_sid" in cookie, cookie)

status, body, _ = get("/static/assistant.css")
check("assistant stylesheet is served", status == 200 and len(body) > 500, status)

# --- assistant API ------------------------------------------------------
status, body = get("/api/health")[0:2]
health = json.loads(body)
check("/api/health ok", status == 200 and health.get("ok") is True)
check("/api/health reports the merged monitor", "monitor" in health, health)
check("no key configured locally → rules", health.get("llm_ready") is False, health)

status, body = post("/api/classify", {"text": "the aircon is leaking water near the entrance"})
cls = json.loads(body)
check("/api/classify returns a group", status == 200 and cls.get("group") in {
    "SIGN", "CLEAN", "SECU", "AUTO", "EVENT", "HVAC", "SAFE", "MOVE",
    "RENO", "LAND", "LOCK", "MAIL", "KEY", "OTHR"}, cls)
check("/api/classify uses the trained model", "local_model" in str(cls.get("backend")), cls)

status, body = post("/api/chat", {"messages": [{"role": "user", "content": "hello"}]})
chat = json.loads(body)
check("/api/chat answers (mock backend)", status == 200 and chat.get("backend") == "mock", chat)
check("/api/chat echoes in mock mode", "模拟模式" in chat.get("reply", ""))

status, body = post("/api/chat", {"messages": []})
check("/api/chat rejects an empty payload", status == 400, status)

status, body = post("/api/assistant", {"messages": [{"role": "user", "content": "电梯坏了"}]})
ans = json.loads(body)
check("/api/assistant explains the missing key", status == 200 and "OpenAI" in ans.get("reply", ""), ans)

# --- watchtower ---------------------------------------------------------
status, body, _ = get(f"/dashboard?days=14&token={TOKEN}")
check("/dashboard still renders", status == 200, status)
check("dashboard carries the watchtower", "Watchtower" in body)
check("watchtower names its backend", "rule-based" in body or "written by the model" in body)
check("dashboard keeps the funnel", "Reporter funnel" in body)
check("watchtower refresh posts a form", 'action="/api/monitor/refresh' in body)

status, body, _ = get(f"/api/monitor/brief?days=14&token={TOKEN}")
brief = json.loads(body)
check("brief is served with a token", status == 200, status)
check("brief carries findings", isinstance(brief.get("findings"), list) and brief["findings"], brief)
check("brief body is non-empty", len(brief.get("body") or "") > 20, brief.get("body"))
check("brief is rule-backed without a key", brief.get("backend") == "rules", brief.get("backend"))
first_stamp = brief.get("generated_at")

status, body, _ = get(f"/api/monitor/brief?days=14&token=wrong")
check("brief rejects a bad token", status == 403, status)

status, body, _ = get(f"/api/monitor/brief?days=14&token={TOKEN}")
second = json.loads(body)
check("second read is cached, not regenerated", second.get("cached") is True and second.get("generated_at") == first_stamp,
      (second.get("cached"), second.get("generated_at"), first_stamp))

# force regeneration through the form endpoint
status, body, _ = get(f"/api/monitor/brief?days=14&token={TOKEN}&refresh=1")
forced = json.loads(body)
check("refresh regenerates", status == 200 and forced.get("cached") is False, forced.get("cached"))

status, body = post("/api/monitor/refresh?days=14&token=wrong", {})
check("POST refresh rejects a bad token", status == 403, status)

# --- assistant events are recorded --------------------------------------
status, body, _ = get(f"/api/analytics/summary?days=14&token={TOKEN}")
summary = json.loads(body)
names = dict(summary["by_event"])
check("assistant_opened is recorded", names.get("assistant_opened", 0) >= 1, names)
check("assistant_message is recorded", names.get("assistant_message", 0) >= 1, names)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
