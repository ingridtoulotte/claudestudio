"""Claude Wrapped — a shareable, year/all-time summary of your Claude Code life.

Pure read over the index. Returns a JSON-able dict the frontend renders into
swipeable cards (and the CLI prints as text).
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from . import parser


def _fmt_int(n: float) -> str:
    return f"{int(n):,}"


def _year_bounds(year: int) -> tuple[float, float] | None:
    """Epoch ``[start, end)`` for a calendar year, or None if it can't be built.

    The exclusive upper bound needs ``year + 1``, so ``year >= datetime.MAXYEAR``
    (9999) overflows the calendar; and on some platforms ``.timestamp()`` raises
    ``OSError``/``OverflowError`` for a year far outside the local epoch range
    (Windows mktime tops out near 9999). Either way we return None so the caller
    falls back to the all-time view instead of surfacing an HTTP 500 (with a
    leaked Python message) or a raw ``claudestudio wrapped --year`` traceback.
    """
    try:
        start = dt.datetime(year, 1, 1).timestamp()
        end = dt.datetime(year + 1, 1, 1).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    return start, end


def generate(conn: sqlite3.Connection, year: int | None = None) -> dict:
    where = ""
    params: tuple = ()
    label = "All time"
    bounds = _year_bounds(year) if year else None
    if bounds:
        start, end = bounds
        where = "WHERE last_epoch >= ? AND last_epoch < ?"
        params = (start, end)
        label = str(year)
    else:
        # absent, zero, or an unrepresentable year -> all-time (year=None) so the
        # returned `year` field and behaviour match the documented `?year=abc` case.
        year = None

    totals = conn.execute(
        f"""SELECT COUNT(*) sessions,
                   COALESCE(SUM(msg_count),0) messages,
                   COALESCE(SUM(tool_calls),0) tool_calls,
                   COALESCE(SUM(input_tokens+output_tokens+cache_write+cache_read),0) tokens,
                   COALESCE(SUM(cost_usd),0) cost_usd,
                   COALESCE(SUM(duration_s),0) duration_s,
                   COUNT(DISTINCT project) projects
            FROM sessions {where}""",
        params,
    ).fetchone()

    busiest = conn.execute(
        f"""SELECT project_name, COUNT(*) c FROM sessions {where}
            GROUP BY project ORDER BY c DESC LIMIT 1""",
        params,
    ).fetchone()

    longest = conn.execute(
        f"""SELECT title, session_id, msg_count, duration_s, cost_usd
            FROM sessions {where} ORDER BY msg_count DESC LIMIT 1""",
        params,
    ).fetchone()

    fav_tool = conn.execute(
        f"""SELECT name, COUNT(*) c FROM tool_calls t
            {"WHERE t.session_id IN (SELECT session_id FROM sessions " + where + ")" if where else ""}
            GROUP BY name ORDER BY c DESC LIMIT 1""",
        params,
    ).fetchone()

    fav_model = conn.execute(
        f"""SELECT model, COUNT(*) c FROM messages
            {"WHERE session_id IN (SELECT session_id FROM sessions " + where + ")" if where else ""}
            AND model IS NOT NULL AND model<>'' GROUP BY model ORDER BY c DESC LIMIT 1"""
        if where else
        """SELECT model, COUNT(*) c FROM messages
           WHERE model IS NOT NULL AND model<>'' GROUP BY model ORDER BY c DESC LIMIT 1""",
        params,
    ).fetchone()

    # busiest day-of-week / hour
    hours = [0] * 24
    weekdays = [0] * 7
    for r in conn.execute(
        f"SELECT last_epoch FROM sessions {where}" if where
        else "SELECT last_epoch FROM sessions", params if where else ()
    ):
        d = parser.local_datetime(r["last_epoch"])
        if d is not None:  # skip corrupt / far-future epochs (else OSError on Windows)
            hours[d.hour] += 1
            weekdays[d.weekday()] += 1
    peak_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else 0
    wd_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    peak_day = wd_names[max(range(7), key=lambda d: weekdays[d])] if any(weekdays) else "—"

    hrs = (totals["duration_s"] or 0) / 3600.0
    cards = [
        {"icon": "🗂", "label": "Sessions", "value": _fmt_int(totals["sessions"]),
         "sub": f"across {totals['projects']} projects"},
        {"icon": "💬", "label": "Messages exchanged", "value": _fmt_int(totals["messages"]),
         "sub": "prompts + responses"},
        {"icon": "🛠", "label": "Tool calls", "value": _fmt_int(totals["tool_calls"]),
         "sub": "reads, edits, commands & more"},
        {"icon": "🎟", "label": "Tokens processed", "value": _fmt_int(totals["tokens"]),
         "sub": "input + output + cache"},
        {"icon": "💵", "label": "Estimated spend", "value": f"${totals['cost_usd']:,.2f}",
         "sub": "at public model prices"},
        {"icon": "⏱", "label": "Time with Claude", "value": f"{hrs:,.0f} h",
         "sub": "summed session spans"},
    ]
    if fav_model and fav_model["model"]:
        cards.append({"icon": "🤖", "label": "Go-to model",
                      "value": fav_model["model"].replace("claude-", ""),
                      "sub": f"{_fmt_int(fav_model['c'])} responses"})
    if fav_tool and fav_tool["name"]:
        cards.append({"icon": "⚡", "label": "Favourite tool", "value": fav_tool["name"],
                      "sub": f"{_fmt_int(fav_tool['c'])} calls"})
    if busiest and busiest["project_name"]:
        cards.append({"icon": "📍", "label": "Home base", "value": busiest["project_name"],
                      "sub": f"{_fmt_int(busiest['c'])} sessions"})
    cards.append({"icon": "📅", "label": "Peak time",
                  "value": f"{peak_day}s", "sub": f"around {peak_hour:02d}:00"})
    if longest and longest["title"]:
        cards.append({"icon": "🏔", "label": "Epic session",
                      "value": (longest["title"][:42] + "…") if len(longest["title"] or "") > 42 else longest["title"],
                      "sub": f"{_fmt_int(longest['msg_count'])} messages",
                      "session_id": longest["session_id"]})

    return {
        "label": label,
        "year": year,
        "totals": dict(totals),
        "cards": cards,
        "available_years": available_years(conn),
    }


def available_years(conn) -> list[int]:
    years = set()
    for r in conn.execute("SELECT last_epoch FROM sessions WHERE last_epoch>0"):
        d = parser.local_datetime(r["last_epoch"])
        if d is not None:  # ignore corrupt / far-future epochs
            years.add(d.year)
    return sorted(years, reverse=True)


def print_text(data: dict) -> None:
    print(f"\n  ✦ Claude Wrapped — {data['label']} ✦\n")
    for c in data["cards"]:
        print(f"   {c['icon']}  {c['value']:<24}  {c['label']} — {c['sub']}")
    print()
