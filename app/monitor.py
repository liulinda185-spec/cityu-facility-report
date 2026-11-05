"""Watchtower — the LLM's day job: read the platform's own numbers, flag what
needs attention, and write one short note for the officer.

Two layers, deliberately:

1. **Rules** run always, with no network and no key. They are the source of
   truth about *what* is wrong (aging, stalled funnel, silent tracker, AI
   drift). If the model is down or unaffordable, the dashboard still tells you
   the truth.
2. **The model** only rewrites those findings into prose and adds context. It
   never computes a number and never invents one: every figure in the prompt
   comes from `analytics.summary()`.

Results are cached for six hours and also stored in `monitor_notes`, so a
dashboard refresh costs one SELECT, not one LLM call.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .analytics import summary
from .assistant import config as llm_config
from .assistant import llm as llm_mod
from .db import get_conn

CACHE_HOURS = 6
DAILY_LLM_BUDGET = 8


# ---------------------------------------------------------------- snapshot


def snapshot(days: int = 7) -> dict:
    """Every number the watchtower is allowed to talk about."""
    days = max(1, min(int(days or 7), 90))
    data = summary(days)
    since = datetime.now() - timedelta(days=days)
    prev_since = since - timedelta(days=days)

    conn = get_conn()
    try:
        assistant = {}
        for row in conn.execute(
            "SELECT event, COUNT(*) AS n FROM user_events WHERE event LIKE 'assistant%'"
            " AND created_at >= ? GROUP BY event", (since.isoformat(timespec="seconds"),)
        ).fetchall():
            assistant[row["event"]] = int(row["n"])
        prev_submits = conn.execute(
            "SELECT COUNT(*) FROM user_events WHERE event = 'report_submitted'"
            " AND created_at >= ? AND created_at < ?",
            (prev_since.isoformat(timespec="seconds"), since.isoformat(timespec="seconds")),
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "days": days,
        "funnel": data["funnel"],
        "tickets": data["tickets"],
        "tracking": data["tracking"],
        "corrections": data["corrections"],
        "devices": data["devices"],
        "by_event": dict(data["by_event"]),
        "total_events": data["total_events"],
        "assistant_events": assistant,
        "prev_window_submits": int(prev_submits or 0),
    }


# ---------------------------------------------------------------- rules


def findings(snap: dict) -> list[dict]:
    """Deterministic triage. Severity: high | watch | ok."""
    out: list[dict] = []
    f, t, tr = snap["funnel"], snap["tickets"], snap["tracking"]

    if snap["total_events"] == 0:
        out.append({
            "id": "silent", "severity": "high",
            "title": "The tracker reported nothing",
            "detail": "No events in the window. Either nobody visited, or tracker.js is not loading after this deploy.",
            "action": "Open the form once and check that a page_view lands within a minute.",
        })

    aging = t.get("aging", {})
    if aging.get("over_72h", 0):
        out.append({
            "id": "aging72", "severity": "high",
            "title": f'{aging["over_72h"]} open report(s) past 72 hours',
            "detail": f'Oldest open report is {t.get("oldest_open_hours", 0):.1f} h old; '
                      f'{aging.get("over_7d", 0)} are past a week.',
            "action": "Claim or explicitly defer them today; a report nobody owns is worse than no report.",
        })
    elif t.get("new", 0) and t.get("oldest_open_hours", 0) >= 24:
        out.append({
            "id": "aging24", "severity": "watch",
            "title": f'Unclaimed reports are ageing ({t["new"]} in the NEW queue)',
            "detail": f'Oldest open report is {t.get("oldest_open_hours", 0):.1f} h old.',
            "action": "Send the queue owner a reminder; target is first claim under one hour.",
        })

    if f["attempted"] >= 5 and f["success_rate"] < 70:
        out.append({
            "id": "funnel", "severity": "high",
            "title": f'Only {f["success_rate"]}% of submit attempts are accepted',
            "detail": f'{f["attempted"]} sessions pressed submit, {f["submitted"]} became tickets.',
            "action": "Read the error text users see; it is a server-side rejection, not hesitation.",
        })
    elif f["views"] >= 20 and f["stepped_rate"] < 40:
        out.append({
            "id": "interest", "severity": "watch",
            "title": f'Only {f["stepped_rate"]}% of visitors start the form',
            "detail": f'{f["views"]} sessions arrived, {f["started"]} touched a field.',
            "action": "Shorten the first screen or say up front how long it takes.",
        })

    if f["started"] >= 10 and f["ai_shown_rate"] < 40:
        out.append({
            "id": "ai_panel", "severity": "watch",
            "title": "The AI suggestion panel rarely appears",
            "detail": f'Shown in {f["ai_shown"]} of {f["started"]} starts ({f["ai_shown_rate"]}%).',
            "action": "Likely a script or model-loading failure; check the browser console once.",
        })

    if tr["lookups"] >= 10 and tr["miss_rate"] >= 30:
        out.append({
            "id": "lookup", "severity": "watch",
            "title": f'{tr["miss_rate"]}% of ticket lookups find nothing',
            "detail": f'{tr["lookups"]} lookups, {tr["misses"]} misses.',
            "action": "Check the confirmation screen: the code must be easy to copy, not just visible.",
        })

    if t.get("created_in_window", 0) >= 5 and snap["corrections"]["rate"] >= 15:
        out.append({
            "id": "drift", "severity": "watch",
            "title": f'AI correction rate is {snap["corrections"]["rate"]}%',
            "detail": f'{snap["corrections"]["count"]} recategorisations over {t["created_in_window"]} new reports.',
            "action": "Below 15% is the target; above it, add the misclassified phrases to training_examples.",
        })

    errors = snap["assistant_events"].get("assistant_error", 0)
    if errors:
        out.append({
            "id": "assistant_errors", "severity": "high" if errors >= 3 else "watch",
            "title": f"The assistant failed {errors} time(s)",
            "detail": "Errors are recorded as assistant_error events with the exception text in props.",
            "action": "Read the event row before blaming the model; most failures are a bad key or timeout.",
        })

    prev = snap["prev_window_submits"]
    if prev >= 4 and f["submitted"] <= prev * 0.5:
        out.append({
            "id": "volume_drop", "severity": "watch",
            "title": "Submission volume halved",
            "detail": f'{f["submitted"]} this window versus {prev} in the previous one.',
            "action": "Check whether a deploy or a form change happened at the boundary.",
        })

    if not out:
        detail = f'{t.get("open", 0)} open'
        if t.get("open"):
            detail += f', oldest {_hours(t.get("oldest_open_hours"))}'
        detail += f', funnel {f["end_to_end_rate"]:g}% end-to-end.'
        out.append({
            "id": "ok", "severity": "ok",
            "title": "Nothing needs attention",
            "detail": detail,
            "action": "Nothing to do; keep the daily check.",
        })
    return out


# ---------------------------------------------------------------- prose


def _hours(value):
    if value is None:
        return "—"
    if value < 1:
        return f"{int(round(value * 60))} min"
    if value < 48:
        return f"{value:.1f} h".replace(".0 h", " h")
    return f"{value / 24:.1f} d".replace(".0 d", " d")


def _rules_body(snap: dict, hits: list[dict]) -> str:
    highs = [h for h in hits if h["severity"] == "high"]
    if not hits or (len(hits) == 1 and hits[0]["id"] == "ok"):
        t, f = snap["tickets"], snap["funnel"]
        bits = [f"{t.get('open', 0)} open"]
        if t.get("open"):
            bits.append("oldest " + _hours(t.get("oldest_open_hours")))
        if t.get("claimed_sample"):
            bits.append("claim p50 " + _hours(t.get("time_to_claim_p50_h")))
        bits.append(f"funnel {f['end_to_end_rate']:g}% end-to-end")
        return f"Over {snap['days']} days: " + ", ".join(bits) + "."
    lead = f"{len(highs)} item(s) need action now" if highs else "No urgent item, but watch these"
    return lead + ": " + "; ".join(h["title"].rstrip(".") for h in hits[:4]) + "."


def _llm_body(snap: dict, hits: list[dict]) -> str:
    prompt = (
        "You are the duty engineer reading a campus facility-report dashboard. "
        "Write 3 short sentences (max 90 words) telling the officer what to do next, "
        "most urgent first. Use ONLY the numbers supplied; never invent a figure, a "
        "cause or a ticket code. No greetings, no bullet lists, no markdown.\n\n"
        f"Window: {snap['days']} days\n"
        f"Findings: {json.dumps(hits, ensure_ascii=False)}\n"
        f"Snapshot: {json.dumps({k: snap[k] for k in ('funnel', 'tickets', 'tracking', 'corrections')}, ensure_ascii=False, default=str)}"
    )
    return llm_mod.chat([{"role": "user", "content": prompt}])["reply"].strip()


# ---------------------------------------------------------------- cache


def _read_cache(days: int):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT body, findings, backend, created_at FROM monitor_notes"
            " WHERE days = ? ORDER BY id DESC LIMIT 1", (days,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        hits = json.loads(row["findings"] or "[]")
    except ValueError:
        hits = []
    return {"body": row["body"] or "", "findings": hits,
            "backend": row["backend"], "generated_at": row["created_at"]}


def _llm_calls_today() -> int:
    since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM monitor_notes WHERE backend = 'llm' AND created_at >= ?",
            (since,)).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def _store(days: int, body: str, hits: list[dict], backend: str) -> dict:
    stamp = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO monitor_notes (days, body, findings, backend, created_at) VALUES (?,?,?,?,?)",
            (days, body, json.dumps(hits, ensure_ascii=False), backend, stamp))
        conn.commit()
    finally:
        conn.close()
    return {"body": body, "findings": hits, "backend": backend, "generated_at": stamp}


def age_hours(stamp: str) -> float:
    try:
        then = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return 999
    return max(0.0, (datetime.now() - then).total_seconds() / 3600)


def generate(days: int = 7, force: bool = False) -> dict:
    """Return the current note, regenerating only when stale or forced."""
    days = max(1, min(int(days or 7), 90))
    cached = _read_cache(days)
    if cached and not force:
        cached["age_hours"] = round(age_hours(cached["generated_at"]), 1)
        cached["cached"] = True
        cached["llm_ready"] = llm_config.llm_ready()
        if cached["age_hours"] < CACHE_HOURS:
            return cached

    snap = snapshot(days)
    hits = findings(snap)
    backend = "rules"
    body = ""
    if llm_config.llm_ready() and _llm_calls_today() < DAILY_LLM_BUDGET:
        try:
            body = _llm_body(snap, hits)
            backend = "llm"
        except Exception as exc:
            print(f"[monitor] model narration failed, rules only: {exc!r}", flush=True)
    if not body:
        body = _rules_body(snap, hits)
        backend = "rules"

    result = _store(days, body, hits, backend)
    result.update({"cached": False, "age_hours": 0.0, "llm_ready": llm_config.llm_ready()})
    return result
