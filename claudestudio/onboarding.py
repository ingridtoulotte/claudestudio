"""First-run guided tour & onboarding status (Feature 1, v0.6.3).

A brand-new user lands in an empty workspace and has to guess what to do. The
guided tour (rendered by ``web/tour.js``) walks them through the five things that
matter on the very first launch. This module owns the *content* of that tour
(one source of truth shared by the web overlay and the ``claudestudio tour``
terminal version) and the *status* signals the UI and MCP use to decide whether
to show it.

Everything here is a pure read over the local index plus a filesystem check for
the hook — no model calls, no network. Tour completion is a single preference
(`tour_completed` = `1`) in the existing `preferences` table.
"""

from __future__ import annotations

import sqlite3

from . import index

# The preference key that records that the tour has been seen/dismissed. The
# frontend writes it via POST /api/preferences; `?tour=1` replays regardless.
TOUR_PREF_KEY = "tour_completed"

# The five-step tour. Each step optionally points at a DOM `target` (a CSS
# selector in the SPA) that `web/tour.js` spotlights. Pure data so the terminal
# tour and the web overlay never drift.
TOUR_STEPS: list[dict] = [
    {
        "id": "welcome",
        "title": "Welcome to ClaudeStudio",
        "body": "The local-first workspace for Claude Code. It indexes, searches, "
                "replays and analyses every session on your machine — nothing "
                "leaves it.",
        "target": None,
        "cta": "Take the tour",
    },
    {
        "id": "search",
        "title": "Search everything",
        "body": "Press ⌘K (Ctrl+K) to search across every prompt, response, tool "
                "call and project — instantly, with BM25 ranking.",
        "target": "#cmdk-trigger",
        "cta": "Next",
    },
    {
        "id": "sessions",
        "title": "Every session you've ever run",
        "body": "The session list is your whole Claude Code history. Open one to "
                "replay it message by message, with inline diffs and bookmarks.",
        "target": "[data-route='sessions']",
        "cta": "Next",
    },
    {
        "id": "analytics",
        "title": "See your real numbers",
        "body": "Analytics shows your true spend, tool success rates and "
                "efficiency — computed deterministically from your own sessions.",
        "target": "[data-route='analytics']",
        "cta": "Next",
    },
    {
        "id": "live",
        "title": "Keep it live",
        "body": "Run `claudestudio hook install` so the index refreshes itself "
                "every time you finish a session. Then ClaudeStudio is always "
                "current.",
        "target": "#btn-reindex",
        "cta": "Start exploring",
    },
]


def tour_completed(conn: sqlite3.Connection) -> bool:
    """True once the user has seen/dismissed the tour (preference `tour_completed`)."""
    return index.get_preference(conn, TOUR_PREF_KEY, "0") == "1"


def session_count(conn: sqlite3.Connection) -> int:
    """How many sessions are indexed. Tolerant of a brand-new/empty index."""
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["n"] if row else 0)


def _budget_set(conn: sqlite3.Connection) -> bool:
    try:
        from . import budget
        b = budget.get_budget(conn)
        return bool(b and (b.get("ceiling_usd") or 0) > 0)
    except Exception:  # noqa: BLE001 — onboarding status must never crash
        return False


def _hook_installed(db_path: str | None) -> bool:
    try:
        from . import hook
        return bool(hook.hook_status(db_path=db_path).get("installed"))
    except Exception:  # noqa: BLE001
        return False


def onboarding_status(conn: sqlite3.Connection, *, db_path: str | None = None) -> dict:
    """The four signals the UI and MCP use to gauge a fresh setup.

    ``{tour_completed, hook_installed, sessions_indexed, budget_set}`` — every
    field a plain bool/int so the answer is trivially serialisable and stable.
    """
    return {
        "tour_completed": tour_completed(conn),
        "hook_installed": _hook_installed(db_path),
        "sessions_indexed": session_count(conn),
        "budget_set": _budget_set(conn),
    }


def tour_payload(conn: sqlite3.Connection, *, db_path: str | None = None) -> dict:
    """Everything the web overlay needs in one call: the steps + the status."""
    return {"steps": TOUR_STEPS, "status": onboarding_status(conn, db_path=db_path)}


def terminal_tour() -> str:
    """A plain-text rendering of the tour for ``claudestudio tour`` (no curses)."""
    lines = ["", "  ClaudeStudio — guided tour", "  " + "─" * 30, ""]
    for i, step in enumerate(TOUR_STEPS, 1):
        lines.append(f"  {i}. {step['title']}")
        for chunk in _wrap(step["body"], 70):
            lines.append(f"     {chunk}")
        lines.append("")
    lines.append("  Replay this any time in the app with ?tour=1, or run "
                 "`claudestudio tour` again.")
    lines.append("")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]
