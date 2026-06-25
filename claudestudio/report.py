"""Activity reports — a shareable, self-contained summary of a span of work.

``generate_report`` turns a date range into a single-file HTML (or Markdown)
document you'd paste into a standup or a retro: hero stats, top projects & tools,
a tiny ASCII bar chart of your most active days, and the notable sessions
(longest, costliest, most tool calls). Pure read over the index, inline CSS, no
JS, no external assets — open it anywhere, attach it to anything.
"""

from __future__ import annotations

import datetime as dt
import html
import sqlite3

from . import parser


def _fmt_int(n) -> str:
    return f"{int(n or 0):,}"


def _fmt_cost(c) -> str:
    c = float(c or 0)
    if c == 0:
        return "$0.00"
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:,.2f}"


def report_data(conn: sqlite3.Connection, since_epoch: float, until_epoch: float,
                title: str = "Claude Code Activity") -> dict:
    """Compute every figure a report needs for ``[since_epoch, until_epoch)``.

    Pure aggregation over the index — deterministic, no I/O beyond the query — so
    the HTML/Markdown renderers and the self-test all read the same numbers.
    """
    where = "WHERE last_epoch >= ? AND last_epoch < ?"
    rng = (since_epoch, until_epoch)

    totals = conn.execute(
        f"""SELECT COUNT(*) sessions,
                   COALESCE(SUM(msg_count),0) messages,
                   COALESCE(SUM(tool_calls),0) tool_calls,
                   COALESCE(SUM(input_tokens+output_tokens+cache_write+cache_read),0) tokens,
                   COALESCE(SUM(cost_usd),0) cost_usd,
                   COALESCE(SUM(duration_s),0) duration_s,
                   COUNT(DISTINCT project) projects
            FROM sessions {where}""",
        rng,
    ).fetchone()

    top_projects = [dict(r) for r in conn.execute(
        f"""SELECT project_name, COUNT(*) sessions,
                   COALESCE(SUM(cost_usd),0) cost_usd,
                   COALESCE(SUM(msg_count),0) messages
            FROM sessions {where}
            GROUP BY project ORDER BY sessions DESC, cost_usd DESC LIMIT 5""",
        rng,
    )]

    top_tools = [dict(r) for r in conn.execute(
        f"""SELECT t.name, COUNT(*) calls, COALESCE(SUM(t.is_error),0) errors
            FROM tool_calls t JOIN sessions s USING(session_id) {where}
            GROUP BY t.name ORDER BY calls DESC LIMIT 5""",
        rng,
    )]

    # most-active days (by message volume)
    day_buckets: dict[str, dict] = {}
    for r in conn.execute(
        f"SELECT last_epoch, msg_count FROM sessions {where}", rng
    ):
        d = parser.local_datetime(r["last_epoch"])
        if d is None:
            continue
        key = d.strftime("%Y-%m-%d")
        b = day_buckets.setdefault(key, {"date": key, "sessions": 0, "messages": 0})
        b["sessions"] += 1
        b["messages"] += r["msg_count"] or 0
    active_days = sorted(day_buckets.values(), key=lambda d: (-d["messages"], d["date"]))[:7]

    def _notable(order_col):
        r = conn.execute(
            f"""SELECT session_id, title, msg_count, tool_calls, cost_usd
                FROM sessions {where} ORDER BY {order_col} DESC LIMIT 1""",
            rng,
        ).fetchone()
        return dict(r) if r else None

    notable = {
        "longest": _notable("msg_count"),
        "costliest": _notable("cost_usd"),
        "most_tools": _notable("tool_calls"),
    }

    return {
        "title": title,
        "since_epoch": since_epoch,
        "until_epoch": until_epoch,
        "since": _day(since_epoch),
        "until": _day(until_epoch),
        "totals": dict(totals),
        "top_projects": top_projects,
        "top_tools": top_tools,
        "active_days": active_days,
        "notable": notable,
    }


def _day(epoch) -> str:
    d = parser.local_datetime(epoch)
    return d.strftime("%Y-%m-%d") if d else "—"


def _ascii_bars(active_days: list[dict], width: int = 24) -> list[str]:
    """`█`-bar lines for the most active days — works in HTML <pre> or Markdown."""
    if not active_days:
        return ["(no activity in range)"]
    mx = max((d["messages"] for d in active_days), default=1) or 1
    out = []
    for d in active_days:
        bar = "█" * max(1, round((d["messages"] / mx) * width)) if d["messages"] else ""
        out.append(f"{d['date']}  {bar:<{width}}  {d['messages']} msgs · {d['sessions']} sess")
    return out


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def to_markdown(data: dict) -> str:
    t = data["totals"]
    out = [
        f"# {data['title']}",
        "",
        f"**{data['since']} → {data['until']}**",
        "",
        "## Overview",
        "",
        f"- **{_fmt_int(t['sessions'])}** sessions across **{_fmt_int(t['projects'])}** projects",
        f"- **{_fmt_int(t['messages'])}** messages · **{_fmt_int(t['tool_calls'])}** tool calls",
        f"- **{_fmt_int(t['tokens'])}** tokens · **{_fmt_cost(t['cost_usd'])}** estimated cost",
        "",
        "## Top projects",
        "",
    ]
    for p in data["top_projects"]:
        out.append(f"- **{p['project_name'] or '(unknown)'}** — "
                   f"{_fmt_int(p['sessions'])} sessions, {_fmt_cost(p['cost_usd'])}")
    out += ["", "## Top tools", ""]
    for tl in data["top_tools"]:
        out.append(f"- `{tl['name']}` — {_fmt_int(tl['calls'])} calls"
                   + (f" ({tl['errors']} errors)" if tl["errors"] else ""))
    out += ["", "## Most active days", "", "```"]
    out += _ascii_bars(data["active_days"])
    out += ["```", "", "## Notable sessions", ""]
    for label, key in (("Longest", "longest"), ("Costliest", "costliest"),
                       ("Most tool calls", "most_tools")):
        n = data["notable"].get(key)
        if n:
            out.append(f"- **{label}:** {n['title'] or 'Untitled'} "
                       f"(`{n['session_id']}`) — {_fmt_int(n['msg_count'])} msgs, "
                       f"{_fmt_int(n['tool_calls'])} tools, {_fmt_cost(n['cost_usd'])}")
    out += ["", "<sub>Generated by ClaudeStudio · `claudestudio report`</sub>", ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HTML (single self-contained, print-friendly file)
# ---------------------------------------------------------------------------

_CSS = """
:root{--bg:#0e0f13;--panel:#15171d;--line:#23262f;--text:#e7e9ee;--text-2:#aab0bd;
--text-3:#727887;--accent:#ff8a5b;--violet:#9a8cff;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:40px 22px 80px}
h1{font-size:26px;margin:0 0 4px}.range{color:var(--text-2);font-size:13px;margin-bottom:18px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-3);
margin:30px 0 12px;border-bottom:1px solid var(--line);padding-bottom:6px}
.hero{display:flex;flex-wrap:wrap;gap:16px}
.hero .s{flex:1;min-width:130px;background:var(--panel);border:1px solid var(--line);
border-radius:12px;padding:14px 16px}
.hero .v{font-size:24px;font-weight:700}.hero .v.accent{color:var(--accent)}
.hero .k{font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
th{color:var(--text-3);font-weight:600;font-size:12px;text-transform:uppercase}
code{font-family:var(--mono);font-size:12px;color:var(--violet)}
pre.bars{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;overflow-x:auto;font-family:var(--mono);font-size:12px;color:var(--text-2);line-height:1.5}
.foot{margin-top:36px;color:var(--text-3);font-size:12px;text-align:center}
.print-btn{position:fixed;top:16px;right:16px;background:var(--accent);color:#1a1206;
border:0;border-radius:8px;padding:8px 14px;font-weight:600;cursor:pointer}
@media print{
  body{background:#fff;color:#000}
  .print-btn{display:none}
  .hero .s,pre.bars{background:#fff;border-color:#ccc}
  h2{color:#333;border-color:#ccc}.hero .v.accent{color:#c2410c}
  code{color:#6d28d9}.range,.hero .k,th{color:#555}
}
""".strip()


def to_html(data: dict) -> str:
    t = data["totals"]
    hero = "".join(
        f'<div class="s"><div class="v {cls}">{html.escape(v)}</div>'
        f'<div class="k">{html.escape(k)}</div></div>'
        for v, k, cls in [
            (_fmt_int(t["sessions"]), "sessions", ""),
            (_fmt_int(t["messages"]), "messages", ""),
            (_fmt_int(t["tool_calls"]), "tool calls", ""),
            (_fmt_int(t["tokens"]), "tokens", ""),
            (_fmt_cost(t["cost_usd"]), "est. cost", "accent"),
        ]
    )
    proj_rows = "".join(
        f"<tr><td>{html.escape(p['project_name'] or '(unknown)')}</td>"
        f"<td>{_fmt_int(p['sessions'])}</td><td>{_fmt_int(p['messages'])}</td>"
        f"<td>{_fmt_cost(p['cost_usd'])}</td></tr>"
        for p in data["top_projects"]
    ) or '<tr><td colspan="4">No projects in range</td></tr>'
    tool_rows = "".join(
        f"<tr><td><code>{html.escape(tl['name'])}</code></td>"
        f"<td>{_fmt_int(tl['calls'])}</td><td>{_fmt_int(tl['errors'])}</td></tr>"
        for tl in data["top_tools"]
    ) or '<tr><td colspan="3">No tool calls in range</td></tr>'
    bars = html.escape("\n".join(_ascii_bars(data["active_days"])))
    notable_rows = ""
    for label, key in (("Longest", "longest"), ("Costliest", "costliest"),
                       ("Most tool calls", "most_tools")):
        n = data["notable"].get(key)
        if n:
            notable_rows += (
                f"<tr><td>{html.escape(label)}</td>"
                f"<td>{html.escape(n['title'] or 'Untitled')}</td>"
                f"<td>{_fmt_int(n['msg_count'])}</td><td>{_fmt_int(n['tool_calls'])}</td>"
                f"<td>{_fmt_cost(n['cost_usd'])}</td></tr>"
            )
    notable_rows = notable_rows or '<tr><td colspan="5">No sessions in range</td></tr>'
    title = html.escape(data["title"])
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} · ClaudeStudio</title>
<style>{_CSS}</style>
</head>
<body>
<button class="print-btn" onclick="window.print()">Print / Save as PDF</button>
<div class="wrap">
<h1>{title}</h1>
<div class="range">{html.escape(data['since'])} → {html.escape(data['until'])}</div>
<div class="hero">{hero}</div>
<h2>Top projects</h2>
<table><thead><tr><th>Project</th><th>Sessions</th><th>Messages</th><th>Cost</th></tr></thead>
<tbody>{proj_rows}</tbody></table>
<h2>Top tools</h2>
<table><thead><tr><th>Tool</th><th>Calls</th><th>Errors</th></tr></thead>
<tbody>{tool_rows}</tbody></table>
<h2>Most active days</h2>
<pre class="bars">{bars}</pre>
<h2>Notable sessions</h2>
<table><thead><tr><th></th><th>Title</th><th>Messages</th><th>Tools</th><th>Cost</th></tr></thead>
<tbody>{notable_rows}</tbody></table>
<div class="foot">Generated by <a href="https://github.com/ingridtoulotte/claudestudio">ClaudeStudio</a> · <code>claudestudio report</code></div>
</div>
</body>
</html>
"""


def generate_report(conn, since_epoch: float, until_epoch: float,
                    title: str = "Claude Code Activity", fmt: str = "html") -> str:
    """Render a report for the range. ``fmt`` in {'html','md','markdown'}."""
    data = report_data(conn, since_epoch, until_epoch, title)
    if (fmt or "html").lower() in ("md", "markdown"):
        return to_markdown(data)
    return to_html(data)


def week_bounds(now: dt.datetime) -> tuple[float, float]:
    """[Monday 00:00, next Monday 00:00) epoch bounds for ``now``'s calendar week."""
    start_day = (now - dt.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    end_day = start_day + dt.timedelta(days=7)
    return start_day.timestamp(), end_day.timestamp()
