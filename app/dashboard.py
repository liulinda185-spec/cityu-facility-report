"""Server-rendered operations dashboard.

Rendered in Python rather than fetched by the browser on purpose: the screen has
to work when the laptop is on a weak campus Wi-Fi, when JavaScript fails, and
when someone prints it for a supervisor. Everything here is computed from
`user_events`, `tickets` and `status_events` via `analytics.summary`.
"""
from __future__ import annotations

from html import escape

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f6f5f2;color:#14161a;font-family:'Segoe UI','Segoe UI Variable',-apple-system,BlinkMacSystemFont,Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px 56px}
.head{background:#8f1725;color:#fff;margin:0 -20px 0;padding:26px 20px 24px}
.head-in{max-width:1040px;margin:0 auto}
.eyebrow{font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;color:rgba(255,255,255,.62)}
h1{margin:6px 0 0;font-size:27px;line-height:1.2;font-weight:700;letter-spacing:-.01em}
.sub{margin:6px 0 0;font-size:14px;color:rgba(255,255,255,.72);max-width:52ch;line-height:1.5}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:18px}
.tab{font-size:12.5px;padding:7px 12px;border:1px solid rgba(255,255,255,.22);color:rgba(255,255,255,.8);text-decoration:none;border-radius:4px}
.tab.on{background:#fff;color:#8f1725;border-color:#fff;font-weight:650}
.tab.link{margin-left:auto;border-style:dashed}
.kpis{display:grid;grid-template-columns:1fr;gap:0;margin-top:26px;border-top:2px solid #14161a}
@media(min-width:640px){.kpis{grid-template-columns:repeat(3,1fr)}}
@media(min-width:980px){.kpis{grid-template-columns:repeat(6,1fr)}}
.kpi{padding:14px 0 16px;border-bottom:1px solid #dcd8d1;min-width:0}
@media(min-width:640px){.kpi{padding:14px 16px 16px;border-right:1px solid #dcd8d1}.kpi:nth-child(3n){border-right:0}}
@media(min-width:980px){.kpi:nth-child(3n){border-right:1px solid #dcd8d1}.kpi:last-child{border-right:0}}
.kpi .n{font-family:ui-serif,Georgia,'Times New Roman',serif;font-size:26px;line-height:1.1;letter-spacing:-.01em}
.kpi .n small{font-size:12px;font-family:inherit;color:#8a8e96;margin-left:3px}
.kpi .l{font-size:11.5px;color:#5a5e66;margin-top:5px;line-height:1.35}
.kpi .d{font-family:Consolas,'Cascadia Mono',monospace;font-size:10.5px;color:#8a8e96;margin-top:3px}
h2{font-size:16px;margin:34px 0 2px;font-weight:700;letter-spacing:-.005em}
.note{font-size:12.5px;color:#5a5e66;margin:2px 0 14px;line-height:1.5}
.two{display:grid;grid-template-columns:1fr;gap:30px}
@media(min-width:860px){.two{grid-template-columns:1.15fr 1fr;gap:40px}}
.fstep{display:grid;grid-template-columns:110px 1fr 92px;gap:12px;align-items:center;padding:9px 0;border-bottom:1px solid #e4e1da}
.fstep .name{font-size:13.5px;color:#14161a;min-width:0}
.fstep .name em{font-style:normal;font-size:11.5px;color:#8a8e96;display:block;margin-top:1px}
.track{height:7px;background:#eae6df;position:relative}
.track i{display:block;height:7px;background:#8f1725}
.fstep .val{font-family:Consolas,'Cascadia Mono',monospace;font-size:12.5px;text-align:right;color:#14161a;min-width:0}
.fstep .val span{display:block;font-size:10px;line-height:1.35;color:#8a8e96}
.aging{margin-top:6px}
.arow{display:grid;grid-template-columns:96px 1fr 40px;gap:10px;align-items:center;padding:7px 0;font-size:13px;border-bottom:1px solid #e4e1da}
.arow i{display:block;height:6px;background:#b8860b}
.arow b{font-family:Consolas,'Cascadia Mono',monospace;font-weight:400;text-align:right;font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:12.5px;table-layout:fixed}
th{text-align:left;font-weight:600;color:#5a5e66;font-size:11px;letter-spacing:.02em;padding:6px 6px 6px 0;border-bottom:1px solid #14161a}
td{padding:6px 6px 6px 0;border-bottom:1px solid #e4e1da;font-family:Consolas,'Cascadia Mono',monospace;color:#14161a;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
td.t{font-family:'Segoe UI',Arial,sans-serif;color:#5a5e66}
.mini{display:inline-block;height:6px;background:#8f1725;min-width:1px;vertical-align:middle}
.empty{border:1px dashed #c9c4bb;padding:22px;color:#5a5e66;font-size:13px;line-height:1.6;background:#fff}
.events{display:grid;grid-template-columns:1fr;gap:0}
.ev{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid #e4e1da;font-size:12.5px}
.ev span:first-child{font-family:Consolas,'Cascadia Mono',monospace;color:#14161a}
.ev span:last-child{font-family:Consolas,'Cascadia Mono',monospace;color:#8a8e96}
.dev{display:flex;flex-wrap:wrap;gap:18px;margin-top:8px;font-size:12.5px;color:#5a5e66}
.dev b{font-family:ui-serif,Georgia,serif;font-size:18px;color:#14161a;font-weight:400;margin-right:4px}
.foot{margin-top:38px;border-top:1px solid #14161a;padding-top:12px;font-size:11.5px;color:#5a5e66;line-height:1.6}
.watch{margin-top:26px;border-top:2px solid #14161a;padding-top:14px}
.wtop{display:grid;grid-template-columns:1fr;gap:10px}
@media(min-width:860px){.wtop{grid-template-columns:1fr 230px;gap:30px}}
.wbody{font-size:14.5px;line-height:1.6;max-width:76ch;margin:8px 0 0}
.wmeta{font-family:Consolas,'Cascadia Mono',monospace;font-size:11px;color:#8a8e96;line-height:1.7;margin:0}
.finding{display:grid;grid-template-columns:9px 1fr;gap:10px;padding:9px 0;border-bottom:1px solid #e4e1da}
.dot{width:9px;height:9px;margin-top:5px;border-radius:50%}
.finding .t{font-size:13.5px;font-weight:650;line-height:1.35}
.finding .x{font-size:12.5px;color:#5a5e66;line-height:1.5;margin-top:2px}
.btn{font:inherit;font-size:12.5px;padding:8px 14px;border:1px solid #14161a;background:#fff;color:#14161a;cursor:pointer}
.btn:hover{background:#14161a;color:#fff}
.denied{max-width:520px;margin:80px auto;background:#fff;border:1px solid #dcd8d1;padding:28px}
@media(max-width:640px){
  .bar{gap:6px}
  .tab.link{margin-left:0;flex-basis:100%;text-align:center}
  table{display:block;table-layout:auto}
  thead{display:none}
  tbody{display:block}
  tr{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px 12px;padding:12px 0;border-bottom:1px solid #e4e1da}
  td{border:0;padding:0;white-space:normal;font-size:12.5px}
  td::before{content:attr(data-label);display:block;font-size:10px;color:#8a8e96;margin-bottom:2px}
  td.t{grid-column:1/-1;font-size:14px;font-weight:600}
  td.t::before{display:none}
  td.bar{grid-column:1/-1;display:none}
  td.bar .mini{width:100%}
}
"""


def _h(value) -> str:
    """Hours, rendered the way a supervisor reads them."""
    if value is None:
        return "—"
    if value < 1:
        return f"{int(round(value * 60))} min"
    if value < 48:
        return f"{value:.1f} h".replace(".0 h", " h")
    return f"{value / 24:.1f} d".replace(".0 d", " d")


def _bar(part: int, whole: int, colour: str = "#8f1725") -> str:
    if not part:
        return '<div class="track"></div>'
    width = 0 if not whole else max(2, min(100, round(part / whole * 100)))
    return f'<div class="track"><i style="width:{width}%;background:{colour}"></i></div>'


def _plural(count: int, singular: str, plural: str = "") -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _pct(value) -> str:
    """Percent without a trailing .0 — a supervisor should not read '100.0%'."""
    number = float(value or 0)
    return str(int(number)) if number == int(number) else f"{number:.1f}"


def _funnel_row(label: str, hint: str, count: int, of_views: int, of_previous: int, previous_label: str) -> str:
    rate_of_views = 0 if not of_views else round(count / of_views * 100)
    rate_of_prev = 0 if not of_previous else round(count / of_previous * 100)
    detail = f"{rate_of_views}% of visits"
    if previous_label:
        detail = f"{rate_of_prev}% of {previous_label}"
    return (
        '<div class="fstep">'
        f'<div class="name">{escape(label)}<em>{escape(hint)}</em></div>'
        f'{_bar(count, of_views)}'
        f'<div class="val">{count}<span>{detail}</span></div>'
        '</div>'
    )


def _watch_block(brief: dict | None, days: int, token: str) -> str:
    """The model's triage note, or the deterministic rule summary when it is off."""
    if not brief:
        return ""
    tones = {"high": "#8f1725", "watch": "#b8860b", "ok": "#3f6b4a"}
    rows = "".join(
        f'<div class="finding"><span class="dot" style="background:{tones.get(h.get("severity"), "#8a8e96")}"></span>'
        f'<div><div class="t">{escape(str(h.get("title", "")))}</div>'
        f'<div class="x">{escape(str(h.get("detail", "")))} {escape(str(h.get("action", "")))}</div></div></div>'
        for h in (brief.get("findings") or [])
    )
    backend = brief.get("backend") or "rules"
    label = "written by the model from these numbers" if backend == "llm" else "rule-based (no model key, or budget used)"
    age = brief.get("age_hours", 0)
    stamp = "just now" if age < 0.05 else (f"{age:.0f} h ago" if age >= 1 else f"{int(age * 60)} min ago")
    return f"""
  <section class="watch">
    <div class="wtop">
      <div>
        <p class="eyebrow" style="color:#8a8e96">Watchtower · what needs action</p>
        <p class="wbody">{escape(brief.get("body") or "")}</p>
        <form method="post" action="/api/monitor/refresh?days={days}&token={escape(token)}" style="margin-top:12px">
          <button class="btn" type="submit">Re-run analysis</button>
        </form>
      </div>
      <div class="wmeta">
        {escape(label)}<br>
        analysis {escape(stamp)} · cached {int(6)} h<br>
        model sees the aggregates above<br>
        — never an email, a description or a code
      </div>
    </div>
    {rows}
  </section>"""


def render_dashboard(data: dict, days: int, token: str, brief: dict | None = None) -> str:
    funnel = data["funnel"]
    tickets = data["tickets"]
    tracking = data["tracking"]
    corrections = data["corrections"]
    views = funnel["views"]

    period = "".join(
        f'<a class="tab{" on" if days == n else ""}" href="/dashboard?days={n}&token={escape(token)}">{n} days</a>'
        for n in (7, 14, 30)
    )

    kpis = [
        (str(tickets["open"]), "open reports", "not yet resolved"),
        (f'{tickets["new"]}', "unclaimed", "in the NEW queue"),
        (_h(tickets["time_to_claim_p50_h"]), "median time to claim", f'n={tickets["claimed_sample"]}'),
        (_h(tickets["time_to_resolve_p50_h"]), "median time to resolve", f'n={tickets["resolved_sample"]}'),
        (f'{_pct(corrections["rate"])}<small>%</small>', "AI correction rate", _plural(corrections["count"], "correction")),
        (f'{funnel["submitted"]}', "reports submitted", f'over {days} days'),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="n">{n}</div><div class="l">{escape(label)}</div>'
        f'<div class="d">{escape(detail)}</div></div>'
        for n, label, detail in kpis
    )

    funnel_rows = (
        _funnel_row("Opened the form", "page_view on /", funnel["views"], views, 0, "")
        + _funnel_row("Started filling it", "first field focused", funnel["started"], views, funnel["views"], "visits")
        + _funnel_row("Pressed submit", "attempt sent", funnel["attempted"], views, funnel["started"], "starts")
        + _funnel_row("Server accepted", "ticket committed", funnel["submitted"], views, funnel["attempted"], "attempts")
    )

    aging = tickets["aging"]
    aging_rows = "".join(
        f'<div class="arow"><span>{escape(label)}</span>'
        f'{_bar(count, max(1, tickets["open"]), "#b8860b")}<b>{count}</b></div>'
        for label, count in (
            ("over 24 h", aging["over_24h"]),
            ("over 72 h", aging["over_72h"]),
            ("over 7 days", aging["over_7d"]),
        )
    )

    days_rows = ""
    peak = max([d["submits"] for d in data["by_day"]] + [1])
    for d in data["by_day"]:
        days_rows += (
            "<tr>"
            f'<td class="t" data-label="Day">{escape(d["day"])}</td>'
            f'<td data-label="Views">{d["views"]}</td>'
            f'<td data-label="Started">{d["starts"]}</td>'
            f'<td data-label="Attempts">{d["attempts"]}</td>'
            f'<td data-label="Accepted">{d["submits"]}</td>'
            f'<td class="bar" data-label="Accepted / day">'
            f'<span class="mini" style="width:{max(1, round(d["submits"] / peak * 100))}%"></span></td>'
            f'<td data-label="Lookups">{d["lookups"]}</td>'
            "</tr>"
        )
    if not days_rows:
        days_rows = ('<tr><td class="t" data-label="Day" colspan="7">'
                     'No events recorded in this window yet.</td></tr>')

    events_rows = "".join(
        f'<div class="ev"><span>{escape(name)}</span><span>{count}</span></div>'
        for name, count in data["by_event"]
    ) or '<div class="ev"><span>none yet</span><span>0</span></div>'

    devices = data["devices"]
    device_html = "".join(
        f'<span><b>{devices[k]}</b>{escape(label)}</span>'
        for k, label in (("mobile", "mobile"), ("desktop", "desktop"),
                         ("tablet", "tablet"), ("unknown", "unknown"))
    )

    empty_note = ""
    watch_html = _watch_block(brief, days, token)
    if data["total_events"] == 0:
        empty_note = (
            '<div class="empty">Nothing recorded in this window yet. Open '
            '<span style="font-family:Consolas,monospace">/</span> or '
            '<span style="font-family:Consolas,monospace">/track</span> once and the funnel will fill in. '
            'Server-side events (submitted, claimed, resolved) are written even when the browser never loads the tracker.</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Operations dashboard · CityU Facility Reports</title>
<style>{CSS}</style>
</head>
<body>
<div class="head"><div class="head-in">
  <a href="/officer?token={escape(token)}" style="color:rgba(255,255,255,.7);font-size:11.5px;text-decoration:none">CityU Facilities · officer workspace →</a>
  <p class="eyebrow" style="margin-top:14px">YKM 4/F Concourse · live service</p>
  <h1>Operations dashboard</h1>
  <p class="sub">Backlog, aging and the reporter funnel, computed from the same database the queue uses. Sessions, not page loads — a reload cannot inflate a number.</p>
  <div class="bar">{period}<a class="tab link" href="/dashboard?days={days}&token={escape(token)}">↻ refresh</a></div>
</div></div>
<div class="wrap">
  {watch_html}
  <div class="kpis">{kpi_html}</div>
  {empty_note}

  <div class="two">
    <section>
      <h2>Reporter funnel</h2>
      <p class="note">Each step counts distinct sessions in the last {days} days. The gap between "pressed submit" and "accepted" is where the server rejected the report.</p>
      {funnel_rows}
      <p class="note" style="margin-top:10px">AI suggestion shown to {_plural(funnel["ai_shown"], "session")} ({_pct(funnel["ai_shown_rate"])}% of starts).</p>
    </section>

    <section>
      <h2>Aging of open reports</h2>
      <p class="note">Open = NEW or ASSIGNED. Oldest open report: {_h(tickets["oldest_open_hours"])}.</p>
      {aging_rows}
      <h2 style="margin-top:26px">Time to claim / resolve</h2>
      <div class="ev"><span>claim p50 · p90</span><span>{_h(tickets["time_to_claim_p50_h"])} · {_h(tickets["time_to_claim_p90_h"])}</span></div>
      <div class="ev"><span>resolve p50 · p90</span><span>{_h(tickets["time_to_resolve_p50_h"])} · {_h(tickets["time_to_resolve_p90_h"])}</span></div>
      <div class="ev"><span>track lookups · miss rate</span><span>{tracking["lookups"]} · {tracking["miss_rate"]}%</span></div>
    </section>
  </div>

  <h2>Daily volume</h2>
  <p class="note">Bars show accepted reports per day. A day with views but no starts is a form people did not trust enough to fill in.</p>
  <table>
    <colgroup><col style="width:18%"><col style="width:11%"><col style="width:11%"><col style="width:12%"><col style="width:12%"><col style="width:26%"><col style="width:12%"></colgroup>
    <thead><tr><th>Day</th><th>Views</th><th>Started</th><th>Attempts</th><th>Accepted</th><th>Accepted / day</th><th>Lookups</th></tr></thead>
    <tbody>{days_rows}</tbody>
  </table>

  <div class="two" style="margin-top:34px">
    <section>
      <h2>Event volume</h2>
      <p class="note">Raw counts, newest window. Use them to spot a page that suddenly stops reporting.</p>
      <div class="events">{events_rows}</div>
    </section>
    <section>
      <h2>Where reporters are</h2>
      <p class="note">Device class from the user agent, not a fingerprint.</p>
      <div class="dev">{device_html}</div>
      <p class="note" style="margin-top:18px">A mobile share above 70% means the three-tap form and the QR flow matter more than any desktop feature.</p>
    </section>
  </div>

  <div class="foot">
    Window: last {days} days · events {data["total_events"]} · generated {escape(data["generated_at"].replace("T", " "))}.
    Only allowlisted event names and a fixed set of metadata keys are stored; no email address, description text or IP address is ever recorded. Rows older than 180 days are pruned on startup.
  </div>
</div>
</body>
</html>"""


def render_denied() -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard access · CityU Facility Reports</title><style>{CSS}</style></head>
<body><div class="denied">
<p class="eyebrow" style="color:#8a8e96">Officer access</p>
<h1 style="color:#14161a;font-size:22px;margin-top:8px">This dashboard needs the officer token</h1>
<p class="sub" style="color:#5a5e66;margin-top:10px">Append <span style="font-family:Consolas,monospace;font-size:12.5px">?token=…</span> to the URL, or open it from the officer workspace. The queue page links here once you are signed in.</p>
<p style="margin-top:18px;font-size:13px"><a href="/officer" style="color:#8f1725">Go to the officer workspace →</a></p>
</div></body></html>"""
