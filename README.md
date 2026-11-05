# CityU Facility Report

A usable MVP for the YKM 4/F Concourse pilot.

## Live deployment

- **App**: https://cityu-facility-report.onrender.com (Render free instance, Postgres attached)
- **Reporter**: `/` · **Track**: `/track` · **Officer**: `/officer` · **Dashboard**: `/dashboard` · **Assistant**: `/assistant`

**What it does now**

- Public mobile reporting form: zone + one-line description + photo (magic-byte sniff, 5 MB cap) + self-reported impact → P1/P2/P3.
- AI category routing: TF-IDF char n-grams + logistic regression, blended with weighted keywords; low-confidence tickets (<0.5) are flagged "needs officer review" and routed to the general inbox.
- Officer console: sign in once with the officer token (12 h HttpOnly cookie), then claim / resolve / correct category / merge duplicates.
- Feedback loop: every officer correction is folded back into the training set on the next Render deploy.
- Watchtower monitor: always-on rules (aging, funnel drop, AI correction rate, silent tracker) + optional 3-sentence LLM summary (cached 6 h, ≤8 calls/day).
- Anonymous funnel telemetry: one `fr_sid` cookie, server-side allowlisted events, no identity, no IP, 180-day prune.
- Security: whitelisted public payload, two-layer rate limiting (429), generated upload names with `nosniff`, SMTP off the request path.

**Where you see the telemetry** (funnel, AI correction rate, backlog aging):
`/dashboard` — sign in at `/officer` first, then open `/dashboard`. Testers only *generate* the events; they can't see the numbers.

## 1. Run it (5 minutes)

```powershell
# from project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# train the AI model on the seed data
python ml\train.py

# start the app
uvicorn app.main:app --reload --port 8000
```

Open:
- Reporter: http://localhost:8000/
- Track: http://localhost:8000/track
- Officer: http://localhost:8000/officer
- Assistant: http://localhost:8000/assistant
- Operations dashboard: http://localhost:8000/dashboard (sign in at `/officer` first, or append `?token=<OFFICER_TOKEN>` for scripts)

There is no built-in password. Set `OFFICER_TOKEN` before starting; if you don't,
the app generates a random one and prints `OFFICER_TOKEN=…` in the console — copy
it from there.

## 2. What's in the box

```
app/
  main.py          FastAPI routes
  db.py            SQLite / Postgres schema + seed
  ai.py            Hybrid classifier (TF-IDF + weighted keywords, low-confidence gate)
  storage.py       Photo storage: S3-compatible R2 when configured, local disk fallback
  emailer.py       SMTP
  analytics.py     Anonymous event tracker + summary() aggregates
  dashboard.py     Server-rendered operations dashboard
  monitor.py       Watchtower: rules + model-written triage note (cached 6 h)
  assistant/       LLM layer, merged in (was the separate Flask service)
    config.py      backend selection from env
    llm.py         chat / classify / ticket assistant (stdlib HTTP only)
    tools.py       create_ticket / find_duplicates / track_ticket / get_zones
  static/
    index.html   Reporter form (mobile)
    track.html   Ticket tracking
    officer.html  Officer queue
    assistant.html + assistant.css  Chat page at /assistant
    tracker.js   Browser batch reporter (frTrack / frTrackOnce)
  uploads/         Photos go here
ml/
  train.py        Trains model.pkl from seed CSV
seed_training_data.csv   350 labeled examples, 14 groups x 25
categories.csv           30 reportable cards (booking + parking removed)
facility_report.db       SQLite (auto-created)
test_db_smoke.py         Old smoke test (still passes; uses /api/analytics)
test_user_events.py      End-to-end tracker + dashboard test (33 checks)
test_merged_app.py       One-server check: assistant, watchtower, caching (29 checks)
test_security_fixes.py   Public payload, uploads, sign-in, rate limit, numbering (24 checks)
llm_webapp/              LEGACY — the Flask original, no longer deployed. Kept for
                         reference only; the live code is app/assistant/.
```

## 3. Classification system

14 reporting groups, 30 cards. Booking (co-working, venue) and parking are
deliberately out of scope for this pilot.

| Group | Cards | Routed to |
|-------|-------|-----------|
| SIGN | Signage Problem (Repair), Signage Problem, Add/Change Signage | fm-signage@ |
| CLEAN | Cleaning Services, Additional Cleaning, Broken glass disposal box | fm-cleaning@ |
| SECU | Security and Cleaning Services, Additional Security Guard | fm-security@ |
| AUTO | Automatic Door (asset-code type) | fm-building@ |
| EVENT | Banner, Carpet, Extension Cord, Display Unit, Rope and Stand, Misc Job, Event Support | fm-event@ |
| HVAC | Extension of AC and Lighting Hours | fm-hvac@ |
| SAFE | First Aid Items, Incident report | fm-safety@ |
| MOVE | General Moving, Moving Furniture, Office Relocation | fm-moving@ |
| RENO | Renovation Works, Hanging/Dismantle Painting | fm-reno@ |
| LAND | Landscapes Services | fm-landscape@ |
| LOCK | Locker | fm-lockers@ |
| MAIL | Mailing Services | fm-mail@ |
| KEY | Key Cutting | fm-keys@ |
| OTHR | Others, Confidential document disposal | fm-general@ / fm-records@ |

Model accuracy: 93% on held-out test (14 classes, 350 examples).
Asset-code path (`AD-0142` etc.) bypasses the model entirely: 99% confidence.
