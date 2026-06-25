"""HTTP-agnostic API layer.

Each function takes a SQLite connection plus parsed query params and returns a
JSON-able dict/list. `server.py` is the only thing that knows about HTTP; this
module is what the tests exercise directly.
"""

from __future__ import annotations

import csv
import difflib
import io
import json
import re
import sqlite3
import time

from . import analytics, export, index, wrapped
from . import ask as ask_engine

SORT_COLUMNS = {
    "recent": "last_epoch",
    "oldest": "first_epoch",
    "messages": "msg_count",
    "tools": "tool_calls",
    "cost": "cost_usd",
    "tokens": "(input_tokens+output_tokens+cache_write+cache_read)",
    "duration": "duration_s",
    "title": "title",
    "health": "health_score",
}


def _row_to_session(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["models"] = json.loads(d.get("models") or "[]")
    except (TypeError, json.JSONDecodeError):
        d["models"] = []
    try:
        d["tags"] = json.loads(d.get("tags") or "[]")
    except (TypeError, json.JSONDecodeError):
        d["tags"] = []
    d["favorite"] = bool(d.get("favorite"))
    d["archived"] = bool(d.get("archived"))
    return d


def list_sessions(conn, params: dict) -> dict:
    q = (params.get("q") or "").strip()
    project = params.get("project")
    model = params.get("model")
    sort = params.get("sort", "recent")
    order = "ASC" if params.get("order") == "asc" else "DESC"
    if sort in ("oldest",):
        order = "ASC"
    favorite = params.get("favorite")
    archived = params.get("archived", "exclude")
    limit = _int_param(params.get("limit"), 60, lo=1, hi=500)
    offset = _int_param(params.get("offset"), 0, lo=0)

    sort_col = SORT_COLUMNS.get(sort, "last_epoch")
    where = ["1=1"]
    args: list = []

    matched_ids = None
    if q:
        try:
            rows = conn.execute(
                "SELECT DISTINCT session_id FROM search_fts WHERE search_fts MATCH ? LIMIT 5000",
                (_fts_query(q),),
            ).fetchall()
            matched_ids = [r["session_id"] for r in rows]
        except sqlite3.OperationalError:
            matched_ids = []
        # also match on title/project text
        like = f"%{q}%"
        title_rows = conn.execute(
            "SELECT session_id FROM sessions WHERE title LIKE ? OR project LIKE ?",
            (like, like),
        ).fetchall()
        matched_ids = list({*(matched_ids or []), *(r["session_id"] for r in title_rows)})
        if not matched_ids:
            return {"sessions": [], "total": 0, "limit": limit, "offset": offset}
        placeholders = ",".join("?" * len(matched_ids))
        where.append(f"s.session_id IN ({placeholders})")
        args.extend(matched_ids)

    if project:
        where.append("s.project = ?")
        args.append(project)
    if model:
        where.append("s.models LIKE ?")
        args.append(f"%{model}%")
    root = params.get("root")
    if root:
        # restrict to sessions indexed under one projects root (multi-root)
        where.append("s.session_id IN (SELECT session_id FROM sources WHERE root=?)")
        args.append(root)
    # date-range filter on session activity, mirroring search()'s since/until.
    # Overlap semantics: `since` keeps sessions still active on/after the bound
    # (last_epoch >= since); `until` keeps sessions started on/before it
    # (first_epoch <= until). Accepts epoch or YYYY-MM-DD via _as_epoch; a bare
    # `until` date is inclusive of the whole day (end_of_day) so picking a date
    # in the UI keeps that day's sessions instead of excluding them.
    since = _as_epoch(params.get("since"))
    until = _as_epoch(params.get("until"), end_of_day=True)
    if since is not None:
        where.append("s.last_epoch >= ?")
        args.append(since)
    if until is not None:
        where.append("s.first_epoch <= ?")
        args.append(until)
    if favorite == "1":
        where.append("u.favorite = 1")
    if archived == "only":
        where.append("u.archived = 1")
    elif archived != "all":
        where.append("u.archived = 0")

    clause = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) n FROM sessions s JOIN user_state u USING(session_id) WHERE {clause}",
        args,
    ).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT s.*, u.favorite, u.archived, u.tags, u.notes
            FROM sessions s JOIN user_state u USING(session_id)
            WHERE {clause}
            ORDER BY {sort_col} {order}, s.session_id ASC
            LIMIT ? OFFSET ?""",
        (*args, limit, offset),
    ).fetchall()
    return {
        "sessions": [_row_to_session(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


def get_session(conn, session_id: str) -> dict | None:
    srow = conn.execute(
        """SELECT s.*, u.favorite, u.archived, u.tags, u.notes
           FROM sessions s JOIN user_state u USING(session_id)
           WHERE s.session_id = ?""",
        (session_id,),
    ).fetchone()
    if not srow:
        return None
    session = _row_to_session(srow)

    msgs = conn.execute(
        """SELECT uuid, role, ts, epoch, seq, model, text, thinking,
                  input_tokens, output_tokens, cache_write, cache_read,
                  cost_usd, tool_count
           FROM messages WHERE session_id=? ORDER BY seq""",
        (session_id,),
    ).fetchall()
    tools = conn.execute(
        """SELECT message_uuid, seq, name, ts, is_error, input_json, result_preview
           FROM tool_calls WHERE session_id=? ORDER BY id""",
        (session_id,),
    ).fetchall()
    tools_by_msg: dict[str, list] = {}
    for t in tools:
        td = dict(t)
        try:
            td["input"] = json.loads(td.pop("input_json") or "{}")
        except json.JSONDecodeError:
            td["input"] = {}
        td["is_error"] = bool(td["is_error"])
        diff, truncated = tool_diff(td.get("name", ""), td["input"])
        if diff is not None:
            td["diff"] = diff
            td["diff_truncated"] = truncated
        tools_by_msg.setdefault(t["message_uuid"], []).append(td)

    timeline = []
    prev_epoch = None
    for m in msgs:
        md = dict(m)
        md["tools"] = tools_by_msg.get(m["uuid"], [])
        gap = 0.0
        if prev_epoch and m["epoch"]:
            gap = max(0.0, m["epoch"] - prev_epoch)
        md["gap_s"] = gap
        if m["epoch"]:
            prev_epoch = m["epoch"]
        timeline.append(md)

    session["timeline"] = timeline
    session["health"] = _session_health(session, timeline)
    session["git"] = _session_git(session)
    return session


def _session_health(session: dict, timeline: list) -> dict:
    """Recompute the full health breakdown for the detail view from already
    -fetched data (no extra queries). Mirrors the cached `health_score` column."""
    from . import health
    tool_errors = sum(
        1 for m in timeline for t in m.get("tools", []) if t.get("is_error")
    )
    if timeline:
        last = timeline[-1]
        last_tools = last.get("tools", [])
        completion = health.completion_signal_for(
            last.get("role"), bool(last_tools),
            any(t.get("is_error") for t in last_tools),
        )
    else:
        completion = 0.0
    return health.compute(
        tool_calls=session.get("tool_calls", 0) or 0,
        tool_errors=tool_errors,
        input_tokens=session.get("input_tokens", 0) or 0,
        output_tokens=session.get("output_tokens", 0) or 0,
        msg_count=session.get("msg_count", 0) or 0,
        completion_signal=completion,
    )


def _session_git(session: dict) -> dict | None:
    """Best-effort git context for the session's project. Never raises."""
    from . import git_context
    project = session.get("project") or ""
    last_epoch = session.get("last_epoch") or session.get("first_epoch") or 0.0
    return git_context.get_git_context(project, last_epoch)


def search(conn, params: dict) -> dict:
    """Full-text search over prompts, responses, thinking and tool calls.

    Ranked by BM25 (lower = more relevant) with a deterministic tiebreak so the
    same query always returns the same order. Optional local filters, all
    expressible from the query string so the CLI and UI share one path:
      * kind     — restrict to user | assistant | tool messages
      * project  — exact project path or project name
      * session  — scope to a single session_id
      * since/until — message time window (epoch seconds or YYYY-MM-DD)
    """
    q = (params.get("q") or "").strip()
    limit = _int_param(params.get("limit"), 40, lo=1, hi=200)
    if not q:
        return {"results": [], "query": q}

    kind = (params.get("kind") or "").strip().lower()
    project = (params.get("project") or "").strip()
    session = (params.get("session") or "").strip()
    since = _as_epoch(params.get("since"))
    until = _as_epoch(params.get("until"), end_of_day=True)  # inclusive end day

    where = ["search_fts MATCH ?"]
    args: list = [_fts_query(q)]
    join = ""
    if kind in ("user", "assistant", "tool"):
        where.append("f.kind = ?")
        args.append(kind)
    if session:
        where.append("f.session_id = ?")
        args.append(session)
    if project:
        where.append("(s.project = ? OR s.project_name = ?)")
        args.extend([project, project])
    root = (params.get("root") or "").strip()
    if root:
        where.append("f.session_id IN (SELECT session_id FROM sources WHERE root=?)")
        args.append(root)
    if since is not None or until is not None:
        # message epoch lives on `messages`, not the FTS shadow table
        join = "JOIN messages mm ON mm.uuid = f.message_uuid"
        if since is not None:
            where.append("mm.epoch >= ?")
            args.append(since)
        if until is not None:
            where.append("mm.epoch <= ?")
            args.append(until)

    clause = " AND ".join(where)
    try:
        rows = conn.execute(
            f"""SELECT f.session_id, f.message_uuid, f.seq, f.kind,
                      snippet(search_fts, 0, '⟦', '⟧', ' … ', 14) AS snip,
                      bm25(search_fts) AS score,
                      s.title, s.project_name, s.last_epoch
               FROM search_fts f JOIN sessions s USING(session_id) {join}
               WHERE {clause}
               ORDER BY score, s.last_epoch DESC, f.session_id, f.seq
               LIMIT ?""",
            (*args, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"results": [], "query": q, "error": "bad query"}
    applied = {k: v for k, v in (
        ("kind", kind or None), ("project", project or None),
        ("session", session or None), ("since", since), ("until", until),
    ) if v is not None}
    return {"query": q, "results": [dict(r) for r in rows], "filters": applied}


def set_state(conn, session_id: str, body: dict) -> dict:
    conn.execute("INSERT OR IGNORE INTO user_state(session_id) VALUES(?)", (session_id,))
    fields: list[str] = []
    args: list = []
    if "favorite" in body:
        fields.append("favorite=?")
        args.append(1 if body["favorite"] else 0)
    if "archived" in body:
        fields.append("archived=?")
        args.append(1 if body["archived"] else 0)
    if "tags" in body and isinstance(body["tags"], list):
        fields.append("tags=?")
        args.append(json.dumps(body["tags"]))
    if "notes" in body:
        fields.append("notes=?")
        args.append(str(body["notes"]))
    if fields:
        conn.execute(
            f"UPDATE user_state SET {','.join(fields)} WHERE session_id=?",
            (*args, session_id),
        )
        conn.commit()
    row = conn.execute(
        "SELECT favorite,archived,tags,notes FROM user_state WHERE session_id=?",
        (session_id,),
    ).fetchone()
    d = dict(row)
    d["favorite"] = bool(d["favorite"])
    d["archived"] = bool(d["archived"])
    d["tags"] = json.loads(d["tags"] or "[]")
    return d


def compare(conn, a: str, b: str) -> dict:
    return {"a": get_session_summary(conn, a), "b": get_session_summary(conn, b)}


def get_session_summary(conn, session_id: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not r:
        return None
    d = _row_to_session(r)
    d["by_tool"] = [
        dict(x) for x in conn.execute(
            "SELECT name, COUNT(*) calls FROM tool_calls WHERE session_id=? "
            "GROUP BY name ORDER BY calls DESC",
            (session_id,),
        )
    ]
    return d


# ---------------------------------------------------------------------------
# saved searches / smart collections
# ---------------------------------------------------------------------------

def _row_to_saved(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["filters"] = json.loads(d.get("filters") or "{}")
    except (TypeError, json.JSONDecodeError):
        d["filters"] = {}
    return d


def list_saved(conn) -> list:
    rows = conn.execute(
        "SELECT id,name,query,sort,filters,created_at FROM saved_searches "
        "ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return [_row_to_saved(r) for r in rows]


def add_saved(conn, body: dict) -> dict:
    name = (str(body.get("name") or "")).strip() or "Untitled search"
    query = str(body.get("query") or "")
    sort = str(body.get("sort") or "recent")
    filters = body.get("filters") if isinstance(body.get("filters"), dict) else {}
    cur = conn.execute(
        "INSERT INTO saved_searches(name,query,sort,filters,created_at) VALUES(?,?,?,?,?)",
        (name, query, sort, json.dumps(filters), time.time()),
    )
    conn.commit()
    return {"id": cur.lastrowid, "name": name, "query": query,
            "sort": sort, "filters": filters}


def delete_saved(conn, saved_id) -> dict:
    try:
        sid = int(saved_id)
    except (TypeError, ValueError):
        return {"deleted": False, "id": saved_id}
    conn.execute("DELETE FROM saved_searches WHERE id=?", (sid,))
    conn.commit()
    return {"deleted": True, "id": sid}


# ---------------------------------------------------------------------------
# per-message bookmarks  (deep-linkable, survive reindex)
# ---------------------------------------------------------------------------

def add_bookmark(conn, session_id: str, body: dict) -> dict:
    """Create a bookmark on a message. Body: ``{seq:int, note:str}``."""
    seq = body.get("seq", 0)
    note = body.get("note", "")
    return index.add_bookmark(conn, session_id, seq, note)


def delete_bookmark(conn, bookmark_id) -> dict:
    return index.delete_bookmark(conn, bookmark_id)


def list_bookmarks(conn, session: str | None = None) -> dict:
    """All bookmarks, or one session's when ``session`` is given."""
    return {"bookmarks": index.list_bookmarks(conn, session or None)}


def _slug(text: str | None, fallback: str = "session") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return (s[:60] or fallback)


def export_session(conn, session_id: str, fmt: str) -> dict | None:
    """Render a full session as Markdown or standalone HTML.

    Returns {text, content_type, filename} or None if the session is unknown.
    """
    detail = get_session(conn, session_id)
    if detail is None:
        return None
    text, content_type = export.render(detail, fmt)
    if content_type.startswith("text/html"):
        ext = "html"
    elif content_type.startswith("application/json"):
        ext = "json"
    else:
        ext = "md"
    filename = f"{_slug(detail.get('title'), session_id[:8] or 'session')}.{ext}"
    return {"text": text, "content_type": content_type, "filename": filename}


# ---------------------------------------------------------------------------
# inline diffs  (computed from edit-tool inputs, shown in the replay view)
# ---------------------------------------------------------------------------

# How many diff lines we keep before truncating — a guard against a giant file
# rewrite ballooning the /api/session payload.
DIFF_MAX_LINES = 200
# Edit-style tools carry a before/after pair. We accept the current Claude Code
# names and the older str_replace wire names so a diff renders either way.
_REPLACE_TOOLS = {"Edit", "MultiEdit", "Update", "str_replace_based_edit",
                  "str_replace_editor"}
_CREATE_TOOLS = {"Write", "create_file", "write_to_file"}


def _diff_path(inp: dict) -> str:
    for k in ("file_path", "path", "notebook_path", "filename", "file"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().replace("\\", "/").rsplit("/", 1)[-1] or v.strip()
    return "file"


def _unified(old: str, new: str, label: str) -> tuple[str | None, bool]:
    if old == new:
        return None, False
    lines = list(difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(),
        fromfile=f"a/{label}", tofile=f"b/{label}", lineterm="",
    ))
    if not lines:
        return None, False
    truncated = len(lines) > DIFF_MAX_LINES
    if truncated:
        lines = lines[:DIFF_MAX_LINES]
    return "\n".join(lines), truncated


def tool_diff(name: str, inp: dict | None) -> tuple[str | None, bool]:
    """Unified-diff string for one edit/create tool call, or (None, False).

    * Replace-style edits (``Edit`` / ``MultiEdit`` / ``str_replace_*``) diff
      ``old_string`` → ``new_string``. ``MultiEdit`` concatenates each edit.
    * Create/write tools diff empty → file content.

    Pure ``difflib`` (stdlib), capped at :data:`DIFF_MAX_LINES`; the second tuple
    element flags truncation so the UI can say "diff truncated".
    """
    inp = inp or {}
    label = _diff_path(inp)
    if name in _REPLACE_TOOLS:
        edits = inp.get("edits")
        if isinstance(edits, list) and edits:  # MultiEdit: one diff per edit, joined
            chunks, trunc = [], False
            for e in edits:
                if not isinstance(e, dict):
                    continue
                d, t = _unified(str(e.get("old_string", e.get("old_str", "")) or ""),
                                str(e.get("new_string", e.get("new_str", "")) or ""), label)
                if d:
                    chunks.append(d)
                trunc = trunc or t
            if not chunks:
                return None, False
            joined = "\n".join(chunks)
            lines = joined.splitlines()
            if len(lines) > DIFF_MAX_LINES:
                return "\n".join(lines[:DIFF_MAX_LINES]), True
            return joined, trunc
        old = inp.get("old_string", inp.get("old_str"))
        new = inp.get("new_string", inp.get("new_str"))
        if old is None and new is None:
            return None, False
        return _unified(str(old or ""), str(new or ""), label)
    if name in _CREATE_TOOLS:
        content = inp.get("content", inp.get("file_text", inp.get("new_string")))
        if content is None:
            return None, False
        return _unified("", str(content or ""), label)
    return None, False


def _fts_query(q: str) -> str:
    """Make a forgiving FTS5 query: quote terms, prefix-match the last word."""
    terms = [t for t in q.replace('"', " ").split() if t]
    if not terms:
        return '""'
    quoted = [f'"{t}"' for t in terms[:-1]]
    quoted.append(f'"{terms[-1]}"*')
    return " ".join(quoted)


def _int_param(v, default, *, lo=0, hi=None):
    """Coerce an HTTP query param to a bounded int, never raising.

    Query-string values arrive as raw text straight from ``parse_qs``, so a
    stray ``?limit=abc`` (or even an empty ``?limit=``) must not reach ``int()``
    unguarded — it would raise ``ValueError`` and surface as an HTTP 500 with a
    leaked Python message instead of a clean result. Missing or non-numeric
    values fall back to ``default``; a numeric value is clamped to ``[lo, hi]``
    so a negative page size (``?limit=-1``) can't bypass the cap — SQLite treats
    a negative ``LIMIT`` as unbounded and would dump the whole table.
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _as_epoch(v, *, end_of_day=False):
    """Coerce a filter value to epoch seconds. Accepts a float/epoch string or a
    plain date (YYYY-MM-DD[ HH:MM]). Returns None when absent or unparseable.

    A *date-only* value normally resolves to that day's midnight, which is right
    for an inclusive lower bound (``since`` keeps the whole start day). For an
    inclusive *upper* bound (``until``) pass ``end_of_day=True`` so the value
    stretches to the day's last instant — otherwise ``<= until`` would drop
    everything that happened after 00:00 on the selected day. A value that
    already carries a time (or is a raw epoch) is used as-is.
    """
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    import datetime as _dt
    s = str(v)
    # `.timestamp()` on a naive datetime goes through the platform's local-time
    # conversion, which rejects instants outside the C library's range: on
    # Windows a pre-epoch date (e.g. `1900-01-01`) or a far-future one raises
    # OSError, and years beyond datetime's own range raise OverflowError. A
    # parseable-but-unrepresentable bound is treated as "unparseable" and yields
    # None (filter simply not applied) rather than escaping as an HTTP 500.
    try:  # date-only: optionally stretch to end of day for inclusive upper bounds
        d = _dt.datetime.strptime(s, "%Y-%m-%d")
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59, microsecond=999_999)
        return d.timestamp()
    except (ValueError, OSError, OverflowError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(s, fmt).timestamp()
        except (ValueError, OSError, OverflowError):
            continue
    return None


# ---------------------------------------------------------------------------
# tool-usage intelligence  (backs the Tools dashboard + the MCP server)
# ---------------------------------------------------------------------------

def tools_stats(conn) -> dict:
    """Aggregate tool-call intelligence, computed entirely from the index.

    Everything here is a pure read — no new storage. Returns a dict the Tools
    dashboard renders as hand-drawn SVG and the MCP server can hand back to a
    client verbatim.
    """
    from . import ask as ask_engine

    leaderboard = []
    for r in conn.execute(
        # SAFE: parameterized (no user input in this query)
        """SELECT name, COUNT(*) calls, COALESCE(SUM(is_error),0) errors
           FROM tool_calls GROUP BY name ORDER BY calls DESC"""
    ):
        calls = r["calls"] or 0
        errors = r["errors"] or 0
        leaderboard.append({
            "name": r["name"], "calls": calls, "errors": errors,
            "success_rate": round((calls - errors) / calls, 4) if calls else 1.0,
        })

    by_project = [
        dict(r) for r in conn.execute(
            # SAFE: parameterized
            """SELECT s.project_name AS project, t.name AS tool, COUNT(*) calls
               FROM tool_calls t JOIN sessions s USING(session_id)
               GROUP BY s.project_name, t.name
               ORDER BY calls DESC LIMIT 400"""
        )
    ]

    # Most-edited files: parse the file path out of each edit-tool call's input.
    file_counts: dict[str, dict] = {}
    for r in conn.execute(
        # SAFE: parameterized
        "SELECT name, input_json FROM tool_calls WHERE name IN "
        "('Edit','Write','MultiEdit','NotebookEdit','Update')"
    ):
        try:
            inp = json.loads(r["input_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        for p in ask_engine.paths_in_tool(r["name"], inp):
            base = p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            ent = file_counts.setdefault(base, {"file": base, "edits": 0})
            ent["edits"] += 1
    most_edited = sorted(file_counts.values(), key=lambda d: (-d["edits"], d["file"]))[:20]

    # Co-occurrence: how often a pair of tools shows up in the same session.
    pair_counts: dict[tuple, int] = {}
    sess_tools: dict[str, set] = {}
    for r in conn.execute("SELECT session_id, name FROM tool_calls"):
        sess_tools.setdefault(r["session_id"], set()).add(r["name"])
    for names in sess_tools.values():
        ordered = sorted(names)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                pair_counts[(ordered[i], ordered[j])] = pair_counts.get((ordered[i], ordered[j]), 0) + 1
    co_occurrence = [
        {"a": a, "b": b, "sessions": n}
        for (a, b), n in sorted(pair_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    ]

    totals = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(is_error),0) errors FROM tool_calls"
    ).fetchone()
    return {
        "leaderboard": leaderboard,
        "by_project": by_project,
        "most_edited_files": most_edited,
        "co_occurrence": co_occurrence,
        "total_calls": totals["calls"] or 0,
        "total_errors": totals["errors"] or 0,
        "distinct_tools": len(leaderboard),
    }


def sessions_by_file(conn, file_path: str, limit=20) -> dict:
    """Sessions whose tool calls referenced a file path (by basename, forgiving).

    Used by the MCP `find_sessions_by_file` tool and the knowledge graph. Matches
    on the file's basename so an absolute path and a relative one to the same file
    both hit. Returns lightweight session summaries.
    """
    raw = (file_path or "").strip()
    if not raw:
        return {"file": raw, "sessions": []}
    base = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    limit = _int_param(limit, 20, lo=1, hi=200)
    rows = conn.execute(
        # SAFE: parameterized — the LIKE needle is bound, never interpolated
        """SELECT DISTINCT s.session_id, s.title, s.project_name, s.last_epoch,
                  s.msg_count, s.cost_usd
           FROM tool_calls t JOIN sessions s USING(session_id)
           WHERE t.input_json LIKE ? ESCAPE '\\'
           ORDER BY s.last_epoch DESC, s.session_id ASC LIMIT ?""",
        (f"%{_like_escape(base)}%", limit),
    ).fetchall()
    return {"file": base, "sessions": [dict(r) for r in rows]}


def _like_escape(s: str) -> str:
    """Escape LIKE wildcards so a user-supplied needle is matched literally."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# session similarity  (TF-IDF cosine, pure stdlib)
# ---------------------------------------------------------------------------

_STOPWORD_TEXT = (
    "the a an and or but if then else for to of in on at by with from is are was "
    "were be been being this that these those it its as i you we they he she them "
    "do does did doing have has had not no yes can could should would will just "
    "what which who when where why how all any some more most other into out up "
    "down over under again than too very s t can't don't"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())
_TOK_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}")


def _tokenize(text: str) -> list[str]:
    return [w for w in (t.lower() for t in _TOK_RE.findall(text or ""))
            if w not in _STOPWORDS and len(w) > 2]


def _session_bags(conn) -> dict:
    """Per-session token frequency bag, from title + user-prompt text."""
    import collections
    bags: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in conn.execute("SELECT session_id, title FROM sessions"):
        bags[r["session_id"]].update(_tokenize(r["title"] or ""))
    for r in conn.execute(
        "SELECT session_id, text FROM messages WHERE role='user' AND text<>''"
    ):
        bags[r["session_id"]].update(_tokenize(r["text"] or ""))
    return bags


def similar_sessions(conn, session_id: str, limit=5) -> dict:
    """Find sessions most similar to `session_id` by prompt content (TF-IDF cosine).

    Pure Python over the index — no model, deterministic. Returns ranked sessions
    with a 0..1 similarity score; an unknown id or an empty corpus yields [].
    """
    import math

    limit = _int_param(limit, 5, lo=1, hi=50)
    bags = _session_bags(conn)
    if session_id not in bags or not bags[session_id]:
        return {"session_id": session_id, "similar": []}

    n = len(bags)
    df: dict[str, int] = {}
    for bag in bags.values():
        for term in bag:
            df[term] = df.get(term, 0) + 1

    def vec(bag):
        out = {}
        for term, tf in bag.items():
            idf = math.log((n + 1) / (df[term] + 1)) + 1.0
            out[term] = tf * idf
        return out

    def cosine(a, b):
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    target = vec(bags[session_id])
    scored = []
    for sid, bag in bags.items():
        if sid == session_id:
            continue
        score = cosine(target, vec(bag))
        if score > 0:
            scored.append((score, sid))
    scored.sort(key=lambda kv: (-kv[0], kv[1]))
    top = scored[:limit]
    meta = {}
    if top:
        ids = [sid for _, sid in top]
        ph = ",".join("?" * len(ids))
        for r in conn.execute(
            # SAFE: parameterized — placeholders bound to the ranked ids
            f"SELECT session_id, title, project_name, last_epoch, msg_count, cost_usd "
            f"FROM sessions WHERE session_id IN ({ph})", ids
        ):
            meta[r["session_id"]] = dict(r)
    return {
        "session_id": session_id,
        "similar": [
            {**meta.get(sid, {"session_id": sid}), "score": round(score, 4)}
            for score, sid in top
        ],
    }


# ---------------------------------------------------------------------------
# knowledge graph  (session × project × file)
# ---------------------------------------------------------------------------

def graph(conn, params: dict | None = None) -> dict:
    """Nodes + edges for the knowledge graph: sessions, projects, files.

    Bounded for renderability: the most-recent `max_sessions` sessions, each
    contributing up to a handful of file edges. Node ids are namespaced
    (`s:`/`p:`/`f:`) so the force-directed layout can key on them directly.
    """
    from . import ask as ask_engine

    params = params or {}
    max_sessions = _int_param(params.get("max_sessions"), 120, lo=1, hi=500)
    project = (params.get("project") or "").strip()

    where = ""
    args: list = [max_sessions]
    if project:
        where = "WHERE project = ? OR project_name = ?"
        args = [project, project, max_sessions]
    srows = conn.execute(
        # SAFE: parameterized
        f"""SELECT session_id, title, project, project_name, last_epoch,
                   msg_count, tool_calls, cost_usd
            FROM sessions {where}
            ORDER BY last_epoch DESC, session_id ASC LIMIT ?""",
        args,
    ).fetchall()
    session_ids = [r["session_id"] for r in srows]

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for r in srows:
        sid = r["session_id"]
        nodes[f"s:{sid}"] = {
            "id": f"s:{sid}", "type": "session", "label": (r["title"] or sid)[:48],
            "session_id": sid, "msg_count": r["msg_count"], "cost_usd": r["cost_usd"],
        }
        pname = r["project_name"] or r["project"] or "(unknown)"
        pid = f"p:{r['project'] or pname}"
        if pid not in nodes:
            nodes[pid] = {"id": pid, "type": "project", "label": pname[:32],
                          "project": r["project"]}
        edges.append({"source": f"s:{sid}", "target": pid, "rel": "in"})

    # file edges from edit/read tool calls on the selected sessions
    if session_ids:
        ph = ",".join("?" * len(session_ids))
        per_session: dict[str, set] = {}
        for r in conn.execute(
            # SAFE: parameterized
            f"SELECT session_id, name, input_json FROM tool_calls "
            f"WHERE session_id IN ({ph}) AND name IN "
            f"('Edit','Write','MultiEdit','NotebookEdit','Update','Read','NotebookRead')",
            session_ids,
        ):
            try:
                inp = json.loads(r["input_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            files = per_session.setdefault(r["session_id"], set())
            if len(files) >= 6:
                continue
            for p in ask_engine.paths_in_tool(r["name"], inp):
                base = p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if not base:
                    continue
                fid = f"f:{base}"
                if fid not in nodes:
                    nodes[fid] = {"id": fid, "type": "file", "label": base[:40], "file": base}
                files.add(base)
                edges.append({"source": f"s:{r['session_id']}", "target": fid, "rel": "touched"})
                if len(files) >= 6:
                    break

    # de-duplicate edges (a session can touch the same file via several calls)
    seen = set()
    uniq_edges = []
    for e in edges:
        key = (e["source"], e["target"], e["rel"])
        if key not in seen:
            seen.add(key)
            uniq_edges.append(e)
    counts = {"session": 0, "project": 0, "file": 0}
    for nd in nodes.values():
        counts[nd["type"]] += 1
    return {
        "nodes": list(nodes.values()),
        "edges": uniq_edges,
        "stats": {"nodes": len(nodes), "edges": len(uniq_edges), **counts},
    }


# convenience wrappers used by both server and CLI
def summary(conn): return index.session_summary(conn)
def analytics_payload(conn): return analytics.overview(conn)
def projects_payload(conn): return {"projects": analytics.projects(conn)}
def wrapped_payload(conn, year=None): return wrapped.generate(conn, _int_param(year, None))
def tools_payload(conn): return tools_stats(conn)
def tool_latency_payload(conn): return {"latency": analytics.tool_latency(conn)}
def graph_payload(conn, params=None): return graph(conn, params or {})
def highlights_payload(conn):
    from . import highlights
    return highlights.generate(conn)


# ---------------------------------------------------------------------------
# CSV exports  (analytics + session list, spreadsheet-ready)
# ---------------------------------------------------------------------------

_SESSION_CSV_COLS = [
    "session_id", "title", "project_name", "first_ts", "last_ts", "msg_count",
    "user_msgs", "tool_calls", "input_tokens", "output_tokens", "cache_write",
    "cache_read", "cost_usd", "primary_model",
]


def sessions_csv(conn) -> str:
    """The full session list as CSV — one header row plus one row per session."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_SESSION_CSV_COLS)
    for r in conn.execute(
        f"SELECT {','.join(_SESSION_CSV_COLS)} FROM sessions "
        "ORDER BY last_epoch DESC, session_id ASC"
    ):
        w.writerow([r[c] for c in _SESSION_CSV_COLS])
    return buf.getvalue()


def analytics_csv(conn) -> str:
    """Analytics overview flattened into a multi-section CSV.

    Sections (``# Overview``, ``# By model``, ``# By tool``, ``# Daily activity``,
    ``# Top projects``) are separated by a blank line, each with its own header
    row — readable as one sheet or split per section.
    """
    ov = analytics.overview(conn)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# Overview"])
    w.writerow(["metric", "value"])
    for k in ("sessions", "messages", "tool_calls", "tokens", "cost_usd", "projects"):
        w.writerow([k, ov.get(k)])
    w.writerow([])
    w.writerow(["# By model"])
    w.writerow(["model", "family", "messages", "tokens", "cost_usd"])
    for m in ov.get("by_model", []):
        w.writerow([m["model"], m["family"], m["messages"], m["tokens"], round(m["cost_usd"], 6)])
    w.writerow([])
    w.writerow(["# By tool"])
    w.writerow(["tool", "calls", "errors"])
    for t in ov.get("by_tool", []):
        w.writerow([t["name"], t["calls"], t["errors"]])
    w.writerow([])
    w.writerow(["# Daily activity"])
    w.writerow(["date", "sessions", "messages", "cost_usd", "tool_calls"])
    for d in ov.get("daily", []):
        w.writerow([d["date"], d["sessions"], d["messages"], round(d["cost_usd"], 6), d["tool_calls"]])
    w.writerow([])
    w.writerow(["# Top projects"])
    w.writerow(["project", "sessions", "messages", "cost_usd"])
    for p in ov.get("top_projects", []):
        w.writerow([p["project_name"], p["sessions"], p["messages"], round(p["cost_usd"], 6)])
    return buf.getvalue()


def report_range(params: dict) -> tuple[float, float, str]:
    """Resolve (since_epoch, until_epoch, title) from params, default = this week."""
    import datetime as _dt

    from . import report
    since = _as_epoch(params.get("since"))
    until = _as_epoch(params.get("until"), end_of_day=True)
    if since is None or until is None:
        ws, wu = report.week_bounds(_dt.datetime.now())
        since = ws if since is None else since
        until = wu if until is None else until
    title = (params.get("title") or "Claude Code Activity").strip() or "Claude Code Activity"
    return since, until, title


def report_html(conn, params: dict) -> dict:
    """Rendered report as a self-contained HTML (or Markdown) string + headers."""
    from . import report
    since, until, title = report_range(params)
    fmt = (params.get("format") or "html").lower()
    text = report.generate_report(conn, since, until, title, fmt)
    if fmt in ("md", "markdown"):
        return {"text": text, "content_type": "text/markdown; charset=utf-8"}
    return {"text": text, "content_type": "text/html; charset=utf-8"}


def report_json(conn, params: dict) -> dict:
    """Machine-readable report data for the same range."""
    from . import report
    since, until, title = report_range(params)
    return report.report_data(conn, since, until, title)


def prompt_patterns(conn, params: dict | None = None) -> dict:
    """Recurring prompt clusters for the Patterns view / MCP. Pure read."""
    from . import patterns
    params = params or {}
    min_count = _int_param(params.get("min_count"), 3, lo=2, hi=1000)
    return {"patterns": patterns.extract_patterns(conn, min_count=min_count)}


def ask(conn, question, session=None) -> dict:
    """Grounded, local Q&A over the indexed history (see `ask.py`). No model calls."""
    payload = ask_engine.answer(conn, question, session)
    payload["suggestions"] = ask_engine.suggestions(bool(session))
    return payload


# ---------------------------------------------------------------------------
# v0.5.2: budget tracker  (F3)
# ---------------------------------------------------------------------------

def budget_status(conn) -> dict:
    from . import budget
    return budget.budget_status(conn)


def set_budget(conn, body: dict) -> dict:
    from . import budget
    return budget.set_budget(conn, body.get("period"), body.get("ceiling_usd"))


def clear_budget(conn) -> dict:
    from . import budget
    return budget.clear_budget(conn)


# ---------------------------------------------------------------------------
# v0.5.2: session annotations  (F5)
# ---------------------------------------------------------------------------

def get_annotations(conn, session_id: str) -> dict:
    return {"session_id": session_id, "annotations": index.list_annotations(conn, session_id)}


def upsert_annotation(conn, session_id: str, body: dict) -> dict:
    return index.upsert_annotation(
        conn, session_id, body.get("message_idx", -1), body.get("note", "")
    )


def delete_annotation(conn, annotation_id) -> dict:
    return index.delete_annotation(conn, annotation_id)


def search_annotations(conn, params: dict) -> dict:
    q = (params.get("q") or "").strip()
    limit = _int_param(params.get("limit"), 50, lo=1, hi=200)
    return {"query": q, "results": index.search_annotations(conn, q, limit)}


# ---------------------------------------------------------------------------
# v0.5.2: prompt library  (F8)
# ---------------------------------------------------------------------------

def list_prompts(conn, params: dict) -> dict:
    q = (params.get("q") or "").strip() or None
    starred = params.get("starred") in ("1", "true", "yes", True)
    limit = _int_param(params.get("limit"), 200, lo=1, hi=1000)
    return {"prompts": index.list_prompts(conn, q, starred, limit)}


def add_prompt(conn, body: dict) -> dict:
    return index.upsert_prompt(
        conn,
        prompt_id=body.get("id"),
        text=body.get("text", ""),
        source=str(body.get("source") or "manual"),
        frequency=_int_param(body.get("frequency"), 1, lo=0),
        starred=body.get("starred"),
    )


def delete_prompt(conn, prompt_id) -> dict:
    return index.delete_prompt(conn, prompt_id)


def extract_prompts(conn, body: dict | None = None) -> dict:
    """Run extraction over history and upsert the results into the library.

    Returns how many prompts were added/updated and the new total — the UI shows
    "found N reusable prompts".
    """
    from . import prompt_library
    body = body or {}
    top_n = _int_param(body.get("top_n"), 50, lo=1, hi=500)
    min_count = _int_param(body.get("min_count"), 3, lo=2, hi=1000)
    found = prompt_library.extract(conn, top_n, min_count=min_count)
    for p in found:
        index.upsert_prompt(
            conn, prompt_id=p["id"], text=p["text"],
            source="extracted", frequency=p["frequency"],
        )
    return {"extracted": len(found), "total": len(index.list_prompts(conn, limit=1000))}


# ---------------------------------------------------------------------------
# v0.5.2: token-efficiency dashboard  (F6)  — pure read, no new storage
# ---------------------------------------------------------------------------

def _iso_week(epoch: float) -> str | None:
    from . import parser as _p
    dt = _p.local_datetime(epoch)
    if dt is None:
        return None
    iso = dt.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def efficiency(conn) -> dict:
    """How *effective* sessions are, not just how much they cost.

    Output-per-dollar, tool-success rate, messages-per-session, and a per-project
    efficiency ranking — all computed from the existing tables. Deterministic.
    """
    import statistics

    ov = conn.execute(
        "SELECT COUNT(*) sessions, COALESCE(SUM(cost_usd),0) cost, "
        "       COALESCE(SUM(output_tokens),0) out, COALESCE(SUM(msg_count),0) msgs "
        "FROM sessions"
    ).fetchone()
    tov = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(is_error),0) errors FROM tool_calls"
    ).fetchone()
    durations = [r["duration_s"] or 0.0 for r in conn.execute(
        "SELECT duration_s FROM sessions WHERE duration_s IS NOT NULL")]
    sessions = ov["sessions"] or 0
    cost = ov["cost"] or 0.0
    calls = tov["calls"] or 0
    errors = tov["errors"] or 0
    overall = {
        "output_tokens_per_dollar": round((ov["out"] or 0) / cost, 2) if cost > 0 else 0.0,
        "tool_success_rate": round((calls - errors) / calls, 4) if calls else 1.0,
        "avg_messages_per_session": round((ov["msgs"] or 0) / sessions, 2) if sessions else 0.0,
        "median_session_duration_s": int(statistics.median(durations)) if durations else 0,
    }

    # Per-project aggregates, then a composite efficiency rank.
    proj: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT COALESCE(project_name,'(unknown)') p, COUNT(*) sessions, "
        "       COALESCE(SUM(cost_usd),0) cost, COALESCE(SUM(output_tokens),0) out "
        "FROM sessions GROUP BY project_name"
    ):
        proj[r["p"]] = {"project": r["p"], "sessions": r["sessions"],
                        "cost_usd": round(r["cost"], 4), "_out": r["out"] or 0,
                        "calls": 0, "errors": 0}
    for r in conn.execute(
        "SELECT COALESCE(s.project_name,'(unknown)') p, COUNT(*) calls, "
        "       COALESCE(SUM(t.is_error),0) errors "
        "FROM tool_calls t JOIN sessions s USING(session_id) GROUP BY s.project_name"
    ):
        if r["p"] in proj:
            proj[r["p"]]["calls"] = r["calls"]
            proj[r["p"]]["errors"] = r["errors"]
    by_project = []
    for p in proj.values():
        c, e = p["calls"], p["errors"]
        opd = round(p["_out"] / p["cost_usd"], 2) if p["cost_usd"] > 0 else 0.0
        by_project.append({
            "project": p["project"], "sessions": p["sessions"],
            "cost_usd": p["cost_usd"],
            "tool_success_rate": round((c - e) / c, 4) if c else 1.0,
            "output_per_dollar": opd,
        })
    # Composite score: half tool-success, half normalised output-per-dollar.
    max_opd = max((p["output_per_dollar"] for p in by_project), default=0.0) or 1.0
    for p in by_project:
        p["_score"] = 0.5 * p["tool_success_rate"] + 0.5 * (p["output_per_dollar"] / max_opd)
    by_project.sort(key=lambda p: (-p["_score"], p["project"]))
    for rank, p in enumerate(by_project, start=1):
        p["efficiency_rank"] = rank
        p.pop("_score", None)

    # 12-week trend.
    weekly: dict[str, dict] = {}
    for r in conn.execute("SELECT last_epoch, output_tokens, cost_usd FROM sessions"):
        wk = _iso_week(r["last_epoch"] or 0.0)
        if wk is None:
            continue
        w = weekly.setdefault(wk, {"out": 0, "cost": 0.0, "calls": 0, "errors": 0})
        w["out"] += r["output_tokens"] or 0
        w["cost"] += r["cost_usd"] or 0.0
    for r in conn.execute(
        "SELECT s.last_epoch, t.is_error FROM tool_calls t JOIN sessions s USING(session_id)"
    ):
        wk = _iso_week(r["last_epoch"] or 0.0)
        if wk is None or wk not in weekly:
            continue
        weekly[wk]["calls"] += 1
        weekly[wk]["errors"] += 1 if r["is_error"] else 0
    trend = []
    for wk in sorted(weekly.keys())[-12:]:
        w = weekly[wk]
        trend.append({
            "week": wk,
            "tool_success_rate": round((w["calls"] - w["errors"]) / w["calls"], 4) if w["calls"] else 1.0,
            "output_per_dollar": round(w["out"] / w["cost"], 2) if w["cost"] > 0 else 0.0,
        })

    return {"overall": overall, "by_project": by_project, "trend": trend}


# ---------------------------------------------------------------------------
# v0.5.2: CLAUDE.md generator  (F4)  + project brief (used by MCP, F9)
# ---------------------------------------------------------------------------

def project_claude_md(conn, project: str) -> dict:
    from . import generate_claude_md
    profile = generate_claude_md.analyse_project(conn, project)
    markdown = generate_claude_md.render_claude_md(profile)
    return {"markdown": markdown, "profile": profile}


def project_brief(conn, project: str) -> dict:
    """A full onboarding brief for a project: stats + the CLAUDE.md profile."""
    from . import generate_claude_md
    profile = generate_claude_md.analyse_project(conn, project)
    return {
        "project": profile.get("project"),
        "project_name": profile.get("project_name"),
        "found": profile.get("found", False),
        "sessions": profile.get("sessions", 0),
        "cost_usd": profile.get("cost_usd", 0.0),
        "total_tokens": profile.get("total_tokens", 0),
        "last_activity": (profile.get("date_range") or {}).get("last"),
        "top_files": profile.get("top_files", []),
        "top_tools": profile.get("top_tools", []),
        "tech_stack": profile.get("tech_stack", []),
        "profile": profile,
    }


# ---------------------------------------------------------------------------
# v0.5.2: cost-by-period  (used by MCP get_cost_by_period, F9)
# ---------------------------------------------------------------------------

def cost_by_period(conn, period: str = "monthly", n: int = 6) -> dict:
    """Spend / tokens / session counts for the last `n` calendar periods."""
    from . import parser as _p
    period = (period or "monthly").strip().lower()
    if period not in ("daily", "weekly", "monthly"):
        period = "monthly"
    n = _int_param(n, 6, lo=1, hi=120)

    def key(epoch):
        dt = _p.local_datetime(epoch)
        if dt is None:
            return None
        if period == "daily":
            return dt.strftime("%Y-%m-%d")
        if period == "weekly":
            iso = dt.isocalendar()
            return f"{iso[0]:04d}-W{iso[1]:02d}"
        return dt.strftime("%Y-%m")

    buckets: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT last_epoch, cost_usd, input_tokens, output_tokens FROM sessions"
    ):
        k = key(r["last_epoch"] or 0.0)
        if k is None:
            continue
        b = buckets.setdefault(k, {"period": k, "sessions": 0, "cost_usd": 0.0, "tokens": 0})
        b["sessions"] += 1
        b["cost_usd"] += r["cost_usd"] or 0.0
        b["tokens"] += (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
    ordered = [buckets[k] for k in sorted(buckets.keys())][-n:]
    for b in ordered:
        b["cost_usd"] = round(b["cost_usd"], 4)
    return {"period": period, "periods": ordered}


# ---------------------------------------------------------------------------
# v0.5.2: diffs for a whole session  (used by MCP get_diff_for_session, F9)
# ---------------------------------------------------------------------------

def diffs_for_session(conn, session_id: str, file_path: str | None = None) -> dict:
    """Every inline diff in a session, optionally filtered to one file."""
    detail = get_session(conn, session_id)
    if detail is None:
        return {"session_id": session_id, "error": "not found", "diffs": []}
    want = (file_path or "").strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    diffs = []
    for m in detail.get("timeline", []):
        for t in m.get("tools", []):
            if not t.get("diff"):
                continue
            fname = _diff_path(t.get("input") or {})
            if want and fname.lower() != want:
                continue
            diffs.append({
                "seq": m.get("seq"), "tool": t.get("name"), "file": fname,
                "diff": t["diff"], "truncated": t.get("diff_truncated", False),
            })
    return {"session_id": session_id, "file_path": file_path, "diffs": diffs}


# ---------------------------------------------------------------------------
# v0.5.2: batch export + archive  (F11)  — pure stdlib zipfile
# ---------------------------------------------------------------------------

def export_batch(conn, session_ids, fmt: str = "md", include_index: bool = True) -> dict:
    """Bundle several sessions into a ZIP archive built in memory.

    Each session becomes one Markdown/HTML file; an ``index.md`` table of contents
    is added when ``include_index`` is set. Returns ``{bytes, content_type,
    filename, count}``.
    """
    import zipfile

    fmt = fmt if fmt in ("md", "markdown", "html", "json") else "md"
    ids = [str(s) for s in (session_ids or []) if str(s).strip()]
    buf = io.BytesIO()
    entries: list[dict] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for sid in ids:
            out = export_session(conn, sid, fmt)
            if out is None:
                continue
            arc = f"{_slug(sid, sid[:8] or 'session')}-{out['filename']}"
            zf.writestr(arc, out["text"])
            summ = get_session_summary(conn, sid) or {}
            entries.append({
                "session_id": sid, "file": arc,
                "title": summ.get("title") or "Untitled",
                "last_ts": summ.get("last_ts") or "",
                "cost_usd": summ.get("cost_usd") or 0.0,
                "msg_count": summ.get("msg_count") or 0,
            })
        if include_index:
            zf.writestr("index.md", _batch_index_md(entries))
    return {
        "bytes": buf.getvalue(), "content_type": "application/zip",
        "filename": "claudestudio-export.zip", "count": len(entries),
    }


def _batch_index_md(entries: list[dict]) -> str:
    lines = ["# ClaudeStudio export", "",
             f"{len(entries)} session(s).", "",
             "| Session | Date | Messages | Cost | File |",
             "|---|---|---|---|---|"]
    for e in entries:
        title = str(e["title"]).replace("|", "\\|")[:60]
        date = str(e["last_ts"])[:10]
        lines.append(
            f"| {title} | {date} | {e['msg_count']} | "
            f"${float(e['cost_usd']):.2f} | [{e['file']}]({e['file']}) |"
        )
    return "\n".join(lines) + "\n"
