"""Persistent search history (Feature 4, v0.6.3).

Users run the same searches again and again. ClaudeStudio remembers the last
:data:`MAX_ROWS` queries (oldest pruned on insert) so the search palette can
offer them back as one-tap suggestions. User-owned state in the ``search_history``
table (schema v7) — it survives reindexing and is never sent anywhere.

Reads tolerate a missing table: an old v6 index that hasn't been touched by a
writer yet has no ``search_history`` table until the next :func:`index.connect`
runs the migration, so every read degrades to an empty result instead of raising.
"""

from __future__ import annotations

import sqlite3
import time

# Hard cap on stored rows. The history is a convenience, not an archive — keeping
# it small means the palette suggestions stay fast and the table never grows
# without bound. The oldest rows are pruned on every insert past this many.
MAX_ROWS = 200


def record_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    kind: str | None = None,
    project: str | None = None,
    result_count: int = 0,
    now: float | None = None,
) -> dict:
    """Append one search to the history, pruning to :data:`MAX_ROWS`.

    Needs a *writable* connection (the read path uses ``connect_ro``). A blank
    query is ignored — only real searches are remembered. ``now`` is injectable
    so the self-test can pin timestamps deterministically.
    """
    q = (query or "").strip()
    if not q:
        return {"recorded": False}
    ts = int(now if now is not None else time.time())
    k = (kind or "").strip().lower() or None
    if k not in ("user", "assistant", "tool", None):
        k = None
    p = (project or "").strip() or None
    try:
        rc = max(0, int(result_count))
    except (TypeError, ValueError):
        rc = 0
    conn.execute(
        "INSERT INTO search_history(query, kind, project, result_count, searched_at) "
        "VALUES(?,?,?,?,?)",
        (q, k, p, rc, ts),
    )
    # Prune anything beyond the most-recent MAX_ROWS by id (monotonic with time).
    conn.execute(
        "DELETE FROM search_history WHERE id NOT IN "
        "(SELECT id FROM search_history ORDER BY id DESC LIMIT ?)",
        (MAX_ROWS,),
    )
    conn.commit()
    return {"recorded": True, "query": q, "searched_at": ts}


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """The most recent searches, newest first. Empty if the table is absent."""
    try:
        lim = max(1, min(int(limit), MAX_ROWS))
    except (TypeError, ValueError):
        lim = 20
    try:
        rows = conn.execute(
            "SELECT id, query, kind, project, result_count, searched_at "
            "FROM search_history ORDER BY searched_at DESC, id DESC LIMIT ?",
            (lim,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def history_payload(conn: sqlite3.Connection, params: dict | None = None) -> dict:
    """API shape: ``{"history": [...], "count": N}``."""
    params = params or {}
    items = recent(conn, _as_int(params.get("limit"), 20))
    return {"history": items, "count": len(items)}


def clear(conn: sqlite3.Connection) -> dict:
    """Delete every stored search. Idempotent; tolerant of a missing table."""
    try:
        conn.execute("DELETE FROM search_history")
        conn.commit()
    except sqlite3.OperationalError:
        return {"cleared": True, "deleted": 0}
    return {"cleared": True}


def delete_one(conn: sqlite3.Connection, entry_id) -> dict:
    """Remove a single history entry by id. Reports whether a row was removed."""
    try:
        eid = int(entry_id)
    except (TypeError, ValueError):
        return {"deleted": False, "id": entry_id}
    try:
        cur = conn.execute("DELETE FROM search_history WHERE id=?", (eid,))
        conn.commit()
    except sqlite3.OperationalError:
        return {"deleted": False, "id": eid}
    return {"deleted": cur.rowcount > 0, "id": eid}


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
