"""Security regression checks for the public endpoints and officer sign-in.

Run against a live instance on :8013 (same as the other suites):

    .venv\\Scripts\\python.exe test_security_fixes.py
"""
import json
import os
import uuid

import requests

BASE = os.getenv("TEST_BASE", "http://127.0.0.1:8013")
TOKEN = os.getenv("OFFICER_TOKEN", "testtoken123")

s = requests.Session()
passed = failed = 0


def check(name, condition, extra=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {extra}")


def submit(description="Water on the floor by the coffee cart, slippery",
           email="student@cityu.edu.hk", photo=None):
    data = {
        "zone_code": "YKM-4-CONC", "asset_code": "", "symptom": "leak",
        "impact": "MINOR", "description": description, "reporter_email": email,
    }
    files = {"photo": (photo[0], photo[1], "application/octet-stream")} if photo else None
    return s.post(f"{BASE}/api/reports", data=data, files=files, timeout=30)


# --- 1. public tracking must not expose the reporter ------------------------
r = submit()
check("submission accepted", r.status_code == 200 and r.json()["code"].startswith("FM-"),
      f"{r.status_code} {r.text[:80]}")
code = r.json()["code"]
check("submission returns a group", "group" in r.json())

r = s.get(f"{BASE}/api/reports/{code}", timeout=20)
ticket = r.json()["ticket"]
check("tracking lookup is 200", r.status_code == 200, r.status_code)
check("tracking payload has no reporter_email", "reporter_email" not in ticket, list(ticket))
check("tracking payload has no photo path", "photo_path" not in ticket)
check("tracking payload still answers the reporter",
      ticket.get("status") == "NEW" and "description" in ticket)
events = r.json()["events"]
check("timeline rows are trimmed",
      all(set(e) <= {"to_status", "note", "created_at"} for e in events), events)

# --- 2. uploads: sniffed, bounded, not executable -------------------------
r = submit(photo=("evil.php", b"<?php system($_GET['c']); ?>"))
check("disguised PHP upload is refused", r.status_code == 400, f"{r.status_code} {r.text[:80]}")

png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + b"IHDR" + b"\x00" * 40
r = submit(description="Cracked tile by the lift lobby", photo=("shot.png", png))
check("real PNG upload is accepted", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
new_code = r.json()["code"]
r = s.get(f"{BASE}/api/reports/{new_code}", timeout=20)
check("upload did not leak the photo path", "photo_path" not in r.json()["ticket"])

r = s.get(f"{BASE}/uploads/../app/main.py", timeout=20)
check("path traversal on /uploads is refused", r.status_code in (400, 404), r.status_code)
r = s.get(f"{BASE}/uploads/does-not-exist.png", timeout=20)
check("unknown upload name is 404", r.status_code == 404, r.status_code)

# The upload we just created must be served with a sniffing guard.
from app.db import get_conn  # local read of the same test database
conn = get_conn()
row = conn.execute("SELECT photo_path FROM tickets WHERE code=?", (new_code,)).fetchone()
conn.close()
if row and row["photo_path"]:
    r = s.get(f"{BASE}/uploads/{row['photo_path']}", timeout=20)
    check("photo is served", r.status_code == 200, r.status_code)
    check("photo response carries nosniff",
          r.headers.get("x-content-type-options") == "nosniff", dict(r.headers))
else:
    check("photo was stored under a generated name", False, "no photo_path row")

# --- 3. officer sign-in via cookie ----------------------------------------
r = s.post(f"{BASE}/officer/login", data={"token": TOKEN, "next": "/dashboard"},
           allow_redirects=False, timeout=20)
check("officer login redirects", r.status_code in (302, 303), r.status_code)
set_cookie = r.headers.get("set-cookie", "")
check("officer login sets an HttpOnly cookie",
      "fr_officer=" in set_cookie and "httponly" in set_cookie.lower(), set_cookie[:120])

r = s.get(f"{BASE}/dashboard", timeout=30)
check("dashboard opens with the cookie and no token in the URL", r.status_code == 200, r.status_code)
r = s.get(f"{BASE}/api/queue", params={"status": "NEW"}, timeout=20)
check("queue opens with the cookie and no token parameter", r.status_code == 200, r.text[:80])
check("queue returns the submitted ticket", any(t["code"] == code for t in r.json()))

# --- 4. bad credentials stay closed --------------------------------------
anon = requests.Session()
r = anon.post(f"{BASE}/officer/login", data={"token": "wrong"}, allow_redirects=False, timeout=20)
check("wrong token cannot sign in", r.status_code == 403, r.status_code)
r = anon.get(f"{BASE}/dashboard", timeout=20)
check("dashboard is closed without a session", r.status_code == 403, r.status_code)

# --- 5. ticket numbers keep moving, never repeat -------------------------
prior = int(code.split("-")[-1])
after = int(new_code.split("-")[-1])
check("ticket numbers strictly increase", after > prior, f"{prior} -> {after}")

# --- 6. status timeline records who acted --------------------------------
s.post(f"{BASE}/api/queue/{new_code}/claim", timeout=20)
r = s.get(f"{BASE}/api/reports/{new_code}", timeout=20)
notes = [e["note"] for e in r.json()["events"]]
check("claim records an actor", any("Claimed by" in n for n in notes), notes)

# --- 7. enumeration is rate limited (last: it spends the session budget) --
limited = False
for i in range(40):
    r = s.get(f"{BASE}/api/reports/FM-2099-{i:06d}", timeout=20)
    if r.status_code == 429:
        limited = True
        break
check("sequential code walking hits a rate limit", limited, f"stopped at {i} (last {r.status_code})")

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
