"""Daily digest — a standup-ready summary of a day's Claude Code sessions.

Answers "what did Claude and I actually do today (or any day)?" with a session
roll-up, files touched, tool usage, a few notable highlights, and a pre-rendered
Markdown block you can paste straight into stand-up notes or a journal.

Deterministic, pure read over the local index — reuses :mod:`narrative` for each
session's one-line headline and :mod:`health` for its A–F grade. No model calls.

Usage::

    from claudestudio.digest import generate_digest
    d = generate_digest(conn)                 # today
    print(d["markdown"])
    generate_digest(conn, date="2026-06-20")  # any calendar date
"""

from __future__ import annotations

import datetime as _dt
import time

from .parser import local_datetime

_EDIT_TOOLS = {
    "Edit", "MultiEdit", "Update", "str_replace_based_edit", "str_replace_editor",
    "Write", "create_file", "write_to_file", "NotebookEdit",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")

_QUALITY_EMOJI = {"successful": "✅", "partial": "⚠️",
                  "abandoned": "⛔", "exploratory": "🔍"}


def _today_str() -> str:
    dt = local_datetime(time.time())
    return dt.strftime("%Y-%m-%d") if dt else "1970-01-01"


def _norm_date(date) -> str:
    """Coerce a date arg to YYYY-MM-DD, defaulting to today. Tolerant of junk."""
    if not date:
        return _today_str()
    s = str(date).strip()
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return _today_str()


def generate_digest(conn, date=None, project_id=None) -> dict:
    """Build the digest dict for one calendar date (default today).

    Filters sessions whose *last activity* falls on `date` (local time),
    optionally scoped to one project. An empty day returns a fully-formed digest
    with zero counts and a friendly Markdown body — never raises.
    """
    day = _norm_date(date)

    rows = conn.execute(
        "SELECT session_id, title, project, project_name, duration_s, last_epoch, "
        "       input_tokens, output_tokens, cache_write, cache_read, cost_usd, "
        "       health_score FROM sessions"
    ).fetchall()

    todays = []
    for r in rows:
        dt = local_datetime(r["last_epoch"] or 0.0)
        if dt is None or dt.strftime("%Y-%m-%d") != day:
            continue
        if project_id and project_id not in (r["project"], r["project_name"]):
            continue
        todays.append(r)
    todays.sort(key=lambda r: r["last_epoch"] or 0.0)

    session_ids = [r["session_id"] for r in todays]
    files_touched = _files_touched(conn, session_ids)
    tools_used = _tools_used(conn, session_ids)

    total_tokens = sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0)
                       + (r["cache_write"] or 0) + (r["cache_read"] or 0)
                       for r in todays)
    total_cost = round(sum(r["cost_usd"] or 0.0 for r in todays), 4)
    top_project = _top_project(todays)

    summaries = [_session_summary(conn, r) for r in todays]
    highlights = _highlights(todays, files_touched, tools_used)

    digest = {
        "date": day,
        "session_count": len(todays),
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "top_project": top_project,
        "files_touched": [f["path"] for f in files_touched],
        "tools_used": tools_used,
        "highlights": highlights,
        "session_summaries": summaries,
    }
    digest["markdown"] = _render_markdown(digest)
    return digest


def _session_summary(conn, r) -> dict:
    from . import health, narrative
    grade = health.grade_for(int(r["health_score"] or 0))
    nar = narrative.narrative_for_session(conn, r["session_id"])
    headline = nar.get("headline") if isinstance(nar, dict) and not nar.get("error") \
        else (r["title"] or "Untitled session")
    return {
        "session_id": r["session_id"],
        "title": r["title"] or "Untitled session",
        "duration_sec": int(r["duration_s"] or 0),
        "token_count": ((r["input_tokens"] or 0) + (r["output_tokens"] or 0)
                        + (r["cache_write"] or 0) + (r["cache_read"] or 0)),
        "cost_usd": round(r["cost_usd"] or 0.0, 4),
        "health_grade": grade,
        "narrative_headline": headline,
    }


def _files_touched(conn, session_ids: list) -> list[dict]:
    """Unique files edited across the day's sessions, by touch count desc."""
    if not session_ids:
        return []
    import json
    placeholders = ",".join("?" * len(session_ids))
    rows = conn.execute(
        f"SELECT name, input_json FROM tool_calls "
        f"WHERE session_id IN ({placeholders})",
        session_ids,
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        if r["name"] not in _EDIT_TOOLS:
            continue
        try:
            inp = json.loads(r["input_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(inp, dict):
            continue
        for k in _PATH_KEYS:
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                base = v.strip().replace("\\", "/").rsplit("/", 1)[-1] or v.strip()
                counts[base] = counts.get(base, 0) + 1
                break
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"path": p, "count": c} for p, c in ordered]


def _tools_used(conn, session_ids: list) -> dict:
    if not session_ids:
        return {}
    placeholders = ",".join("?" * len(session_ids))
    rows = conn.execute(
        f"SELECT name, COUNT(*) n FROM tool_calls "
        f"WHERE session_id IN ({placeholders}) GROUP BY name ORDER BY n DESC, name",
        session_ids,
    ).fetchall()
    return {r["name"]: r["n"] for r in rows}


def _top_project(todays) -> str | None:
    counts: dict[str, int] = {}
    for r in todays:
        p = r["project_name"] or r["project"]
        if p:
            counts[p] = counts.get(p, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: (counts[k], k))


def _highlights(todays, files_touched, tools_used) -> list[str]:
    out: list[str] = []
    if not todays:
        return out
    first = todays[0]
    dt = local_datetime(first["last_epoch"] or 0.0)
    if dt:
        out.append(f"First session wrapped at {dt.strftime('%H:%M')}.")
    costs = [float(r["cost_usd"] or 0.0) for r in todays]
    if len(costs) >= 2 and sum(costs) > 0:
        avg = sum(costs) / len(costs)
        peak = max(todays, key=lambda r: r["cost_usd"] or 0.0)
        if (peak["cost_usd"] or 0.0) > 2 * avg and avg > 0:
            out.append(f"Cost spike: “{peak['title']}” cost "
                       f"${peak['cost_usd']:.2f} — well above the day's average.")
    if files_touched:
        top = files_touched[0]
        out.append(f"Most-edited file: `{top['path']}` "
                   f"({top['count']} edit{'s' if top['count'] != 1 else ''}).")
    if tools_used:
        busiest = max(tools_used, key=lambda k: tools_used[k])
        out.append(f"Busiest tool: `{busiest}` ({tools_used[busiest]} calls).")
    return out


def _fmt_duration(sec: int) -> str:
    sec = int(sec or 0)
    if sec < 60:
        return f"{sec} sec"
    if sec < 3600:
        return f"{sec // 60} min"
    h, rem = divmod(sec, 3600)
    return f"{h}h {rem // 60:02d}m"


def _render_markdown(d: dict) -> str:
    lines = [f"## 📅 Claude Code Digest — {d['date']}", ""]
    if not d["session_count"]:
        lines.append("_No Claude Code sessions on this day._")
        return "\n".join(lines) + "\n"
    lines.append(f"**{d['session_count']} session"
                 f"{'s' if d['session_count'] != 1 else ''} · "
                 f"{d['total_tokens']:,} tokens · ${d['total_cost_usd']:.2f}**")
    if d["top_project"]:
        lines.append(f"\nTop project: **{d['top_project']}**")
    lines += ["", "### Sessions"]
    for s in d["session_summaries"]:
        nar = s["narrative_headline"] or s["title"]
        emoji = nar.split(" ", 1)[0] if nar[:1] in "✅⚠⛔🔍" else "•"
        title = s["title"]
        lines.append(f"- {emoji} {title} — {_fmt_duration(s['duration_sec'])}, "
                     f"{s['health_grade']}, ${s['cost_usd']:.2f}")
    if d["files_touched"]:
        shown = d["files_touched"][:3]
        extra = len(d["files_touched"]) - len(shown)
        suffix = f" (+{extra} more)" if extra > 0 else ""
        lines += ["", "### Files Touched",
                  ", ".join(f"`{p}`" for p in shown) + suffix]
    if d["tools_used"]:
        top = sorted(d["tools_used"].items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        lines += ["", "### Tools",
                  " · ".join(f"`{name}`: {n}" for name, n in top)]
    if d["highlights"]:
        lines += ["", "### Highlights"] + [f"- {h}" for h in d["highlights"]]
    return "\n".join(lines) + "\n"


def digest_html(conn, date=None, project_id=None) -> str:
    """A standalone HTML page wrapping the digest Markdown (CLI --html export)."""
    d = generate_digest(conn, date, project_id)
    body = _md_to_html(d["markdown"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code Digest — {d['date']}</title>
<style>
  body {{ background:#0d0d14; color:#e7e9f3; font:15px/1.6 -apple-system,
    BlinkMacSystemFont,'Segoe UI',sans-serif; max-width:720px; margin:40px auto;
    padding:0 20px; }}
  h2 {{ color:#9a8cff; }} h3 {{ color:#c7c2f0; margin-top:1.6em; }}
  code {{ background:#1a1a28; padding:2px 6px; border-radius:4px; font-size:.9em; }}
  a {{ color:#9a8cff; }}
</style></head><body>
{body}
</body></html>
"""


def _md_to_html(md: str) -> str:
    """Minimal Markdown → HTML (headings, list items, bold, inline code). stdlib."""
    import html as _html
    import re
    out = []
    for line in md.splitlines():
        esc = _html.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
        if line.startswith("## "):
            out.append(f"<h2>{esc[3:]}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{esc[4:]}</h3>")
        elif line.startswith("- "):
            out.append(f"<li>{esc[2:]}</li>")
        elif line.strip():
            out.append(f"<p>{esc}</p>")
    return "\n".join(out)
