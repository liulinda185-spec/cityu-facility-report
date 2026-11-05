# CityU Facility Report — Running Guide

A usable MVP for the YKM 4/F Concourse pilot.

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

## 2. What you NEED to buy (and what's free)

| Item | Cost | Notes |
|------|------|-------|
| **Nothing for local dev** | $0 | SQLite + scikit-learn + FastAPI all free. Run on your laptop. |
| **Email sending** | $0/month for pilot | Use a Gmail account with an **app password** (free). Free tier: ~500 emails/day. |
| **Asset stickers** | ~HK$50 | 50× waterproof vinyl stickers, print at any HK print shop (e.g. Druck, Eslite). Format: `AD-0001` `ACU-0001` etc. |
| **Hosting (when you go live)** | $0–HK$80/month | Option A: run on your laptop + expose with ngrok (free, for testing). Option B: Render free tier (sleeps after 15 min idle). Option C: HK$80/month on a small VPS (DigitalOcean/Hetzner) for real pilot. |
| **Domain (optional)** | ~HK$100/year | e.g. `report.cityu.local` — not needed for pilot. |
| **OCR for asset code scan** | $0 | Tesseract.js runs in the browser, no server cost. |
| **AI model** | $0 | scikit-learn runs locally. No OpenAI/LLM cost needed for MVP. |

**Total pilot budget: ~HK$50** (stickers only).

## 3. What to connect (step by step)

### 3.1 Email (Gmail app password)
1. Go to myaccount.google.com → Security → 2-Step Verification ON.
2. → App passwords → create one named "facility-report".
3. Set env vars before running:
```powershell
$env:SMTP_USER      = "your-email@gmail.com"
$env:SMTP_PASS      = "your-app-password"
$env:FROM_ADDR      = "your-email@gmail.com"
$env:PILOT_CATCHALL = "your-email@gmail.com"   # optional: force ALL mail to you during testing
uvicorn app.main:app --port 8000
```
Without SMTP vars, emails print to console (dev mode) — app still works.

### 3.2 Officer token
There is no default. Set one before starting:
```powershell
$env:OFFICER_TOKEN = "something-strong"
```
Without it, a random token is generated at startup and printed once as
`OFFICER_TOKEN=…`. Type it into the box on `/officer` and press **Sign in**: the
server sets a 12-hour `HttpOnly` cookie (`fr_officer`), so the token stops
appearing in URLs, screenshots and logs. **Sign out** clears it. Scripts and the
assistant's duplicate check can still pass `?token=…`. Set `OFFICER_NAME` too if
you want status notes to say who acted (`Claimed by Linda` instead of `officer`).

### 3.3 Asset register (when you're ready)
Walk YKM 4/F and record stickers in a CSV: `asset_code, building, floor, zone, description`.
Upload into the DB later — for pilot, free-text asset code is enough.

### 3.4 Go live (after pilot testing)
- Deploy to Render/Railway: connect this folder, set build command `pip install -r requirements.txt`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Set the same env vars (SMTP_USER, SMTP_PASS, OFFICER_TOKEN).
- Print a QR code on the concourse wall linking to your live URL.

## 4. What's in the box

```
app/
  main.py          FastAPI routes
  db.py            SQLite schema + seed
  ai.py            Hybrid classifier (TF-IDF model + keyword evidence)
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

## 5. What is in the classification system

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

## 5. Pilot success checklist

- [ ] Submit a report on your phone, see ticket code
- [ ] Officer email arrives (or console log in dev)
- [ ] Officer claims and resolves; reporter sees status update
- [ ] AI suggests correct category for 5 test tickets
- [ ] Asset code `AD-0001` auto-routes to building services
- [ ] QR code printed on YKM 4/F wall
- [ ] FM officer tests for 1 week

## 6. Next upgrades (after pilot)

- Real asset register DB + OCR scan button
- LLM fallback for low-confidence tickets
- Recurrence dashboard ("AD-0142 failed 3×")
- Photo vision model (CLIP zero-shot)
- Migrate SQLite → Postgres
- CityU SSO for officer login

## 7. Operations dashboard and anonymous event tracking

Open `http://localhost:8000/dashboard?token=<OFFICER_TOKEN>` (the officer page has a
button that carries the token across). The page is server-rendered, so it also prints.

What it shows, all from the same database the queue uses:

| Block | Meaning |
|-------|---------|
| open / unclaimed | backlog size, NEW queue depth |
| median time to claim / resolve | computed from `status_events`, with p90 under it |
| aging | open reports older than 24 h / 72 h / 7 days |
| reporter funnel | distinct **sessions**: view → form started → submit pressed → server accepted |
| AI correction rate | officer recategorisations ÷ reports created in the window |
| track lookups + miss rate | how many people could not find their ticket |
| daily volume, event volume, devices | where the traffic is and what stops reporting |

How events get there:

- `app/static/tracker.js` batches browser events (10 per request, flushed on a 1.5 s
  idle or `pagehide` by beacon). Call `frTrack("report_photo_attached")`,
  `frTrackOnce("report_ai_shown", {ai_group: "HVAC"})`.
- `POST /api/events` accepts a batch; `POST /api/analytics` is kept as a single-event
  compatibility shim for the LLM demo and older scripts.
- Server-side events are written where the truth is: `report_submitted` only after the
  row commits, `track_lookup` on both hit and miss, plus queue/claim/resolve/recategorise.

Privacy and robustness:

- One first-party `fr_sid` cookie (30 days, `samesite=lax`). No email, no IP, no free text.
- Event names **and** metadata keys are allowlisted server-side; anything else is dropped
  without being stored. A student with `curl` cannot push a report body into the table.
- `safe_record()` swallows its own errors: analytics can never turn a successful
  submission into a 500.
- Rows older than 180 days are pruned at startup.

Test it without touching your working database:

```powershell
$env:FACILITY_DB_PATH = "$env:TEMP\fr-events.db"
$env:OFFICER_TOKEN    = "testtoken123"
python -m uvicorn app.main:app --port 8013
# in a second shell
$env:FACILITY_DB_PATH = "$env:TEMP\fr-events.db"; $env:OFFICER_TOKEN = "testtoken123"
python test_user_events.py
python test_merged_app.py
```

## 8. One app now, with the model watching in the background

The assistant used to be a second Flask service (`llm_webapp`, port 5050) that
called this app over the public internet with a second copy of the token. It is
now a package inside this app: **one process, one database, one URL, one token**.

| Before | Now |
|--------|-----|
| 2 services, 2 start commands | 1 service: `uvicorn app.main:app` |
| `https://…/` + separate demo host | `/` and `/assistant` on the same host |
| tools did `requests` to `FACILITY_API_BASE` | tools call this same server on localhost (no public hop) |
| `FACILITY_TOKEN` copied into the demo | the app's own `OFFICER_TOKEN` |
| `flask`, `requests`, `python-dotenv` | none of them — stdlib HTTP only |

**What "monitor everything" actually does.** `app/monitor.py` reads the same
`user_events` + `tickets` aggregates the dashboard uses and produces two layers:

1. **Rules** — always on, no key, no network: open reports past 72 h, unclaimed
   aging, a funnel that loses submissions, a tracker that went silent, lookups
   that miss, AI correction rate above 15 %, assistant errors, volume halved
   against the previous window. If the model is unavailable, this is what you
   see, and it is still the truth.
2. **The model** — rewrites those findings into three sentences of "do this
   next". It receives only aggregates and finding text. It never sees an email
   address, a description, a photo, or a ticket code, so merging the LLM in does
   not weaken the anonymous-tracking boundary from §7.

Cost control: the note is cached for 6 h in `monitor_notes`, and at most 8 model
calls are made per 24 h; after that it stays on rules until the window rolls.
The dashboard's **Re-run analysis** button forces one regeneration.

**Configuration** (all optional — the platform works without them):

```powershell
$env:CHAT_BACKEND    = "openai"                 # mock | openai | ollama
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL    = "deepseek-chat"
$env:OPENAI_API_KEY  = "<your key>"             # never commit this
```

With `CHAT_BACKEND=mock` (the default) `/api/chat` and `/api/assistant` return a
"not connected" message, while `/api/classify` and the watchtower keep working
locally. Endpoints: `GET /api/health`, `POST /api/chat`, `POST /api/classify`,
`POST /api/assistant`, `GET /api/monitor/brief?days=&token=`,
`POST /api/monitor/refresh?days=&token=`.

## 9. Hardening pass — what changed so the pilot is answerable to real users

| Was | Is now |
|-----|--------|
| `GET /api/reports/{code}` returned the whole row, **including `reporter_email`**; codes are sequential, so anyone could walk them | Whitelisted payload + two limits (30/10 min per session, 60/10 min per source) → **429** |
| `/uploads` served any file in the folder, name taken from the client extension | Explicit route, generated names, **magic-byte** type check, 5 MB cap, `nosniff` |
| SMTP called inside the request; a slow mail host delayed the report | `BackgroundTasks` + `send_safe()`; `timeout=10` |
| Emails linked to `http://host/track` | Links built from `PUBLIC_BASE_URL` or the request host |
| `COUNT(*)+1` ticket numbers — a deletion caused a collision | Highest code in the year + 1, with insert retries |
| Token in every URL/form, every action logged as "officer" | Cookie sign-in (`/officer/login`), `hmac` compare, `OFFICER_NAME` in status notes |

Tests: `test_security_fixes.py` (24) covers the payload whitelist, 429, upload
rejection/acceptance, cookie sign-in, closed dashboard, monotonic numbering and
actor attribution. Run all four suites before deploying.

Still open: the production token that leaked into `test_online_pg.py` has **not**
been rotated — treat it as burned and issue a new `OFFICER_TOKEN`.


