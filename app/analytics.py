"""User-event tracker: one place that records anonymous interaction events.

Why this exists
---------------
The platform already wrote status changes to `status_events` and a handful of
hand-rolled events to `analytics_events`, but nothing read either one back, and
the reporter side sent nothing at all. A supervisor could not answer the only
question that matters — "how many people opened the form, how many gave up, how
long until someone claimed it?" — so the pilot could not report its own impact.

Design rules
------------
* **Anonymous.** One first-party session cookie (30 days) is the join key. No
  email, no IP address, no free text ever reaches this table.
* **Allowlisted twice.** Event names and metadata keys are re-checked on the
  server, so a client bug (or a curious student with curl) cannot push report
  content into analytics. Unknown keys are dropped, not stored.
* **Never breaks the product.** `safe_record` swallows its own errors: a failed
  analytics insert must not turn a submitted report into a 500.
* **Bounded.** Batches are capped, strings are truncated, and rows older than
  RETENTION_DAYS are pruned on startup.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta

from .db import get_conn

log = logging.getLogger("facility.analytics")

SESSION_COOKIE = "fr_sid"
SESSION_MAX_AGE = 30 * 86400  # seconds
RETENTION_DAYS = 180
MAX_BATCH = 20
MAX_PROP_VALUE = 60
MAX_PROPS = 10

# Event vocabulary. Client-side intent events carry the "report_" prefix; the
# server-side confirmations below are authoritative and are written by the API
# handlers, not by the browser — otherwise a blocked request would still count
# as a success.
ALLOWED_EVENTS = {
    # reporter funnel
    "page_view",
    "report_form_started",
    "report_ai_shown",
    "report_photo_attached",
    "report_submit_attempt",
    "report_submit_error",
    "report_submitted",          # server, after the row is committed
    "report_confirm_view",       # ticket code shown on screen
    # tracking
    "track_lookup",
    "ticket_tracked",
    # officer
    "officer_queue_view",
    "officer_claim",
    "officer_resolve",
    "officer_recategorize",
    "officer_duplicates_check",
    "officer_compilation",
    # LLM demonstrator (forwarded by llm_webapp)
    "assistant_opened",
    "assistant_message",
    "assistant_tool_call",
    "assistant_ticket_created",
    "assistant_duplicates_found",
    "assistant_error",
    # legacy names kept so old clients and the smoke scripts keep working
    "ticket_claimed",
    "ticket_resolved",
    "category_corrected",
}

# Metadata is a small, typed bag of facts — never free text.
PROP_KEYS = {
    "priority", "group", "status", "from_status", "to_status", "step", "count",
    "duration_ms", "zone", "reason", "http_status", "has_photo", "category",
    "result", "found", "source", "mode", "bucket", "days", "page_size",
    "ai_group", "confidence", "queue", "status_code",
    # assistant (merged into this app; values are enum-like, never free text)
    "tool", "turns", "error_type",
}

_PAGE_RE = re.compile(r"^[a-z0-9_]{1,24}$")
_SID_RE = re.compile(r"^[0-9a-f]{32}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9\-]{1,32}$")


# ------------------------------------------------------------------ session

def new_session() -> str:
    return secrets.token_hex(16)


def valid_session(value: str | None) -> bool:
    return bool(value) and bool(_SID_RE.match(value))


def page_slug(value: str | None) -> str:
    """Page names are short lowercase slugs; anything else becomes 'unknown'."""
    text = str(value or "").strip().lower()
    return text if _PAGE_RE.match(text) else "unknown"


def device_of(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    if not ua:
        return "unknown"
    if "ipad" in ua or "tablet" in ua:
        return "tablet"
    if "mobi" in ua or "iphone" in ua or "android" in ua:
        return "mobile"
    return "desktop"


# ------------------------------------------------------------------ writing

def _clean_code(value) -> str | None:
    text = str(value or "").strip()
    return text if text and _CODE_RE.match(text) else None


def _clean_props(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    clean = {}
    for key in list(raw)[:MAX_PROPS + 4]:
        if key not in PROP_KEYS:
            continue
        value = raw[key]
        if isinstance(value, bool) or isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value.strip()[:MAX_PROP_VALUE]
        if len(clean) >= MAX_PROPS:
            break
    return clean


def record(event: str, *, session: str | None = None, page: str = "",
           ticket_code: str | None = None, props=None, ua: str = "",
           conn=None, at: datetime | None = None) -> bool:
    """Insert one event. Returns False when the event is not in the vocabulary."""
    name = str(event or "").strip()
    if name not in ALLOWED_EVENTS:
        return False
    row = (
        session if valid_session(session) else None,
        name,
        page_slug(page),
        _clean_code(ticket_code),
        device_of(ua),
        json.dumps(_clean_props(props), ensure_ascii=False, sort_keys=True),
        (at or datetime.now()).isoformat(timespec="seconds"),
    )
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO user_events (session_id,event,page,ticket_code,device,props,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            row,
        )
        if own_conn:
            conn.commit()
        return True
    finally:
        if own_conn:
            conn.close()


def safe_record(event: str, **kwargs) -> bool:
    """Fire-and-forget variant used inside request handlers."""
    try:
        return record(event, **kwargs)
    except Exception:  # analytics must never break the product
        log.warning("analytics write failed for %s", event, exc_info=True)
        return False


def prune(conn=None) -> int:
    """Drop rows past the retention window. Called once on startup."""
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    try:
        cur = conn.execute("DELETE FROM user_events WHERE created_at < ?", (cutoff,))
        if own_conn:
            conn.commit()
        return getattr(cur, "rowcount", 0) or 0
    finally:
        if own_conn:
            conn.close()


# ------------------------------------------------------------------ reading

def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _percentile(values, q: float):
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * q))))
    return round(ordered[k], 2)


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600.0


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def _ticket_stats() -> dict:
    """Operational numbers computed from tickets and status_events."""
    conn = get_conn()
    try:
        tickets = conn.execute(
            "SELECT id, code, status, priority, created_at FROM tickets", ()
        ).fetchall()
        events = conn.execute(
            "SELECT ticket_id, to_status, created_at FROM status_events", ()
        ).fetchall()
    finally:
        conn.close()

    created_by_id = {t["id"]: _parse_dt(t["created_at"]) for t in tickets}
    open_tickets = [dict(t) for t in tickets if str(t["status"]).upper() != "RESOLVED"]
    counts = {"NEW": 0, "ASSIGNED": 0, "RESOLVED": 0}
    for t in tickets:
        key = str(t["status"]).upper()
        counts[key] = counts.get(key, 0) + 1

    now = datetime.now()
    aging = {"over_24h": 0, "over_72h": 0, "over_7d": 0}
    oldest_hours = 0.0
    for t in open_tickets:
        created = _parse_dt(t.get("created_at"))
        if not created:
            continue
        age = _hours(now - created)
        oldest_hours = max(oldest_hours, age)
        if age >= 168:
            aging["over_7d"] += 1
        elif age >= 72:
            aging["over_72h"] += 1
        elif age >= 24:
            aging["over_24h"] += 1

    first_claim, first_resolve = {}, {}
    for e in events:
        tid = e["ticket_id"]
        status = str(e["to_status"] or "").upper()
        moment = _parse_dt(e["created_at"])
        if not moment:
            continue
        if status in {"ASSIGNED", "RESOLVED"}:
            if tid not in first_claim or moment < first_claim[tid]:
                first_claim[tid] = moment
        if status == "RESOLVED":
            if tid not in first_resolve or moment < first_resolve[tid]:
                first_resolve[tid] = moment

    claim_hours, resolve_hours = [], []
    for tid, moment in first_claim.items():
        created = created_by_id.get(tid)
        if created and moment >= created:
            claim_hours.append(_hours(moment - created))
    for tid, moment in first_resolve.items():
        created = created_by_id.get(tid)
        if created and moment >= created:
            resolve_hours.append(_hours(moment - created))

    return {
        "open": len(open_tickets),
        "new": counts.get("NEW", 0),
        "assigned": counts.get("ASSIGNED", 0),
        "resolved": counts.get("RESOLVED", 0),
        "aging": aging,
        "oldest_open_hours": round(oldest_hours, 1),
        "time_to_claim_p50_h": _percentile(claim_hours, 0.5),
        "time_to_claim_p90_h": _percentile(claim_hours, 0.9),
        "time_to_resolve_p50_h": _percentile(resolve_hours, 0.5),
        "time_to_resolve_p90_h": _percentile(resolve_hours, 0.9),
        "claimed_sample": len(claim_hours),
        "resolved_sample": len(resolve_hours),
    }


def summary(days: int = 14) -> dict:
    """Everything the dashboard shows, from one 14-day slice of events.

    The funnel is counted in **sessions**, not raw events, so a reload cannot
    inflate a step and a single visitor cannot count as three people.
    """
    days = max(1, min(int(days or 14), 90))
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT event, session_id, page, device, props, created_at"
            " FROM user_events WHERE created_at >= ?", (since,)
        ).fetchall()
        tickets_in_window = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE created_at >= ?", (since,)
        ).fetchone()[0]
    finally:
        conn.close()

    by_event: dict[str, int] = {}
    sessions: dict[str, set] = {step: set() for step in
                                ("view", "start", "attempt", "submit")}
    by_day: dict[str, dict[str, int]] = {}
    devices: dict[str, set] = {"mobile": set(), "desktop": set(),
                               "tablet": set(), "unknown": set()}
    lookup_misses = 0
    ai_shown_sessions = set()

    for row in rows:
        name = row["event"]
        sid = row["session_id"] or ""
        by_event[name] = by_event.get(name, 0) + 1
        day = str(row["created_at"])[:10]
        bucket = by_day.setdefault(day, {"views": 0, "starts": 0, "attempts": 0,
                                         "submits": 0, "lookups": 0})
        device = row["device"] if row["device"] in devices else "unknown"
        if sid:
            devices[device].add(sid)

        # Events without a session (server-to-server, or a browser that refuses
        # cookies) still count toward raw totals but cannot enter the funnel —
        # they would all otherwise collapse into one phantom visitor.
        if not sid:
            continue

        if name == "page_view":
            bucket["views"] += 1
            if row["page"] in ("index", "unknown", ""):
                sessions["view"].add(sid)
        elif name == "report_form_started":
            bucket["starts"] += 1
            sessions["start"].add(sid)
        elif name == "report_submit_attempt":
            bucket["attempts"] += 1
            sessions["attempt"].add(sid)
        elif name == "report_submitted":
            bucket["submits"] += 1
            sessions["submit"].add(sid)
        elif name == "track_lookup":
            bucket["lookups"] += 1
            try:
                props = json.loads(row["props"] or "{}")
            except ValueError:
                props = {}
            if props.get("found") is False:
                lookup_misses += 1
        elif name == "report_ai_shown":
            ai_shown_sessions.add(sid)

    views = len(sessions["view"])
    started = len(sessions["start"])
    attempted = len(sessions["attempt"])
    submitted = len(sessions["submit"])

    stats = _ticket_stats()
    corrections = by_event.get("officer_recategorize", 0) + by_event.get("category_corrected", 0)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": days,
        "since": since,
        "funnel": {
            "views": views,
            "started": started,
            "attempted": attempted,
            "submitted": submitted,
            "stepped_rate": _pct(started, views),          # reached the form
            "attempt_rate": _pct(attempted, started),      # pressed submit
            "success_rate": _pct(submitted, attempted),    # server accepted it
            "end_to_end_rate": _pct(submitted, views),
            "ai_shown": len(ai_shown_sessions),
            "ai_shown_rate": _pct(len(ai_shown_sessions), started),
        },
        "tracking": {
            "lookups": by_event.get("track_lookup", 0),
            "misses": lookup_misses,
            "miss_rate": _pct(lookup_misses, by_event.get("track_lookup", 0)),
        },
        "tickets": {**stats, "created_in_window": int(tickets_in_window or 0)},
        "corrections": {
            "count": corrections,
            "rate": _pct(corrections, int(tickets_in_window or 0)),
        },
        "devices": {name: len(seen) for name, seen in devices.items()},
        "by_day": [{"day": d, **by_day[d]} for d in sorted(by_day)],
        "by_event": sorted(by_event.items(), key=lambda kv: -kv[1])[:12],
        "total_events": len(rows),
    }
