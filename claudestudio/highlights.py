"""Smart Highlights — surface the signal from the noise, deterministically.

Every highlight here is *computed* from the SQLite index with plain heuristics —
no model calls, no network, fully reproducible. The same index always yields the
same highlights, so the feature is honest and the local-first promise holds. The
web Analytics page and the MCP server both render what `generate` returns; each
item carries the `session_id`(s) it came from so the UI can deep-link straight to
the relevant session.
"""

from __future__ import annotations

import sqlite3

from . import parser

# A session is "abandoned" (likely a false start) at or below this many messages.
ABANDONED_MAX_MSGS = 2
# A file is "revisited" once it shows up in at least this many distinct sessions.
REVISITED_MIN_SESSIONS = 3
# Cost spike = a session costing more than this multiple of the mean session cost.
COST_SPIKE_FACTOR = 3.0
# Two prompts "recur" when their word-trigram Jaccard similarity is at least this.
RECURRING_MIN_SIMILARITY = 0.6
# Bound the O(n²) recurring-pattern scan to the most recent N sessions.
RECURRING_SCAN_LIMIT = 200


def generate(conn: sqlite3.Connection) -> dict:
    """Compute every highlight category. Returns a JSON-able dict of lists."""
    return {
        "breakthroughs": breakthroughs(conn),
        "cost_spikes": cost_spikes(conn),
        "marathons": marathons(conn),
        "revisited_files": revisited_files(conn),
        "recurring_prompts": recurring_prompts(conn),
        "abandoned": abandoned(conn),
        "model_migrations": model_migrations(conn),
    }


def marathons(conn, limit: int = 5) -> list[dict]:
    """Top sessions by duration and by message count."""
    rows = conn.execute(
        """SELECT session_id, title, project_name, duration_s, msg_count,
                  tool_calls, cost_usd, last_epoch
           FROM sessions ORDER BY duration_s DESC, msg_count DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        if not (r["duration_s"] or r["msg_count"]):
            continue
        mins = (r["duration_s"] or 0) / 60.0
        out.append({
            "session_id": r["session_id"], "title": r["title"],
            "project_name": r["project_name"], "msg_count": r["msg_count"],
            "duration_s": r["duration_s"],
            "reason": f"{mins:.0f} min · {r['msg_count']} messages",
        })
    return out


def cost_spikes(conn) -> list[dict]:
    """Sessions whose cost is >COST_SPIKE_FACTOR× the mean (non-zero) session cost."""
    rows = conn.execute(
        "SELECT session_id, title, project_name, cost_usd, last_epoch "
        "FROM sessions WHERE cost_usd > 0"
    ).fetchall()
    if not rows:
        return []
    mean = sum(r["cost_usd"] for r in rows) / len(rows)
    threshold = mean * COST_SPIKE_FACTOR
    spikes = [r for r in rows if r["cost_usd"] >= threshold and r["cost_usd"] > mean]
    spikes.sort(key=lambda r: -r["cost_usd"])
    return [{
        "session_id": r["session_id"], "title": r["title"],
        "project_name": r["project_name"], "cost_usd": r["cost_usd"],
        "reason": f"${r['cost_usd']:.2f} · {r['cost_usd'] / mean:.1f}× average",
    } for r in spikes[:10]]


def revisited_files(conn) -> list[dict]:
    """Files that appear (read or edited) across many distinct sessions."""
    from . import ask as ask_engine

    seen: dict[str, set] = {}
    for r in conn.execute(
        "SELECT session_id, name, input_json FROM tool_calls "
        "WHERE name IN ('Edit','Write','MultiEdit','NotebookEdit','Update','Read','NotebookRead')"
    ):
        import json
        try:
            inp = json.loads(r["input_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        for p in ask_engine.paths_in_tool(r["name"], inp):
            base = p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if base:
                seen.setdefault(base, set()).add(r["session_id"])
    ranked = sorted(
        ((f, len(sids)) for f, sids in seen.items() if len(sids) >= REVISITED_MIN_SESSIONS),
        key=lambda fs: (-fs[1], fs[0]),
    )
    return [
        {"file": f, "sessions": n, "reason": f"touched in {n} sessions"}
        for f, n in ranked
    ][:15]


def abandoned(conn) -> list[dict]:
    """Sessions with only a message or two — likely false starts."""
    rows = conn.execute(
        "SELECT session_id, title, project_name, msg_count, last_epoch "
        "FROM sessions WHERE msg_count <= ? ORDER BY last_epoch DESC LIMIT 30",
        (ABANDONED_MAX_MSGS,),
    ).fetchall()
    return [{
        "session_id": r["session_id"], "title": r["title"],
        "project_name": r["project_name"], "msg_count": r["msg_count"],
        "reason": f"only {r['msg_count']} message(s)",
    } for r in rows]


def model_migrations(conn) -> list[dict]:
    """Days on which more than one primary model was used across sessions."""
    by_day: dict[str, set] = {}
    for r in conn.execute(
        "SELECT primary_model, last_epoch FROM sessions "
        "WHERE primary_model <> '' AND last_epoch > 0"
    ):
        d = parser.local_datetime(r["last_epoch"])
        if d is None:
            continue
        by_day.setdefault(d.strftime("%Y-%m-%d"), set()).add(r["primary_model"])
    out = [
        {"date": day, "models": sorted(models),
         "reason": f"{len(models)} models: {', '.join(sorted(models))}"}
        for day, models in by_day.items() if len(models) >= 2
    ]
    out.sort(key=lambda d: str(d["date"]), reverse=True)
    return out[:15]


def breakthroughs(conn) -> list[dict]:
    """Sessions where a run of tool errors resolves into a clean final tool result.

    The classic "stuck, then unstuck" arc: several failing tool calls followed by
    a successful one. Heuristic and deterministic — ordered by error count so the
    hardest-won wins surface first.
    """
    err_by_session: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT session_id, is_error FROM tool_calls ORDER BY session_id, seq, id"
    ):
        st = err_by_session.setdefault(
            r["session_id"], {"errors": 0, "last_error": False, "calls": 0}
        )
        st["calls"] += 1
        if r["is_error"]:
            st["errors"] += 1
        st["last_error"] = bool(r["is_error"])
    candidates = [
        sid for sid, st in err_by_session.items()
        if st["errors"] >= 2 and not st["last_error"] and st["calls"] > st["errors"]
    ]
    if not candidates:
        return []
    ph = ",".join("?" * len(candidates))
    meta = {
        r["session_id"]: dict(r) for r in conn.execute(
            f"SELECT session_id, title, project_name, last_epoch FROM sessions "
            f"WHERE session_id IN ({ph})", candidates
        )
    }
    out = []
    for sid in candidates:
        st = err_by_session[sid]
        m = meta.get(sid, {"session_id": sid, "title": None, "project_name": None})
        out.append({
            "session_id": sid, "title": m.get("title"),
            "project_name": m.get("project_name"), "errors": st["errors"],
            "reason": f"recovered after {st['errors']} tool errors",
        })
    out.sort(key=lambda d: -d["errors"])
    return out[:10]


def _trigrams(text: str) -> set:
    words = [w for w in text.lower().split() if w]
    if len(words) < 3:
        return set(words)
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def recurring_prompts(conn) -> list[dict]:
    """Pairs of sessions whose opening prompts overlap heavily (trigram Jaccard).

    Surfaces the prompts you keep repeating ("fix the tests", "write docs for…").
    Bounded to the most recent sessions so the pairwise scan stays cheap.
    """
    rows = conn.execute(
        "SELECT session_id, title, preview FROM sessions "
        "WHERE preview <> '' ORDER BY last_epoch DESC LIMIT ?",
        (RECURRING_SCAN_LIMIT,),
    ).fetchall()
    grams = [(r["session_id"], r["title"], r["preview"], _trigrams(r["preview"])) for r in rows]
    pairs = []
    for i in range(len(grams)):
        sid_a, title_a, prev_a, ga = grams[i]
        if not ga:
            continue
        for j in range(i + 1, len(grams)):
            sid_b, _title_b, _prev_b, gb = grams[j]
            if not gb:
                continue
            inter = len(ga & gb)
            if not inter:
                continue
            jac = inter / len(ga | gb)
            if jac >= RECURRING_MIN_SIMILARITY:
                pairs.append({
                    "a": sid_a, "b": sid_b, "similarity": round(jac, 3),
                    "sample": (prev_a or title_a or "")[:80],
                    "reason": f"{jac * 100:.0f}% prompt overlap",
                })
    pairs.sort(key=lambda d: -d["similarity"])
    return pairs[:15]
