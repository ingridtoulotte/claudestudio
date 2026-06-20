"""HTTP-agnostic API layer.

Each function takes a SQLite connection plus parsed query params and returns a
JSON-able dict/list. `server.py` is the only thing that knows about HTTP; this
module is what the tests exercise directly.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time

from . import analytics, export, wrapped, index, ask as ask_engine

SORT_COLUMNS = {
    "recent": "last_epoch",
    "oldest": "first_epoch",
    "messages": "msg_count",
    "tools": "tool_calls",
    "cost": "cost_usd",
    "tokens": "(input_tokens+output_tokens+cache_write+cache_read)",
    "duration": "duration_s",
    "title": "title",
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
    limit = min(int(params.get("limit", 60) or 60), 500)
    offset = int(params.get("offset", 0) or 0)

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
            ORDER BY {sort_col} {order}
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
    return session


def search(conn, params: dict) -> dict:
    q = (params.get("q") or "").strip()
    limit = min(int(params.get("limit", 40) or 40), 200)
    if not q:
        return {"results": [], "query": q}
    try:
        rows = conn.execute(
            """SELECT f.session_id, f.message_uuid, f.seq, f.kind,
                      snippet(search_fts, 0, '⟦', '⟧', ' … ', 14) AS snip,
                      bm25(search_fts) AS score,
                      s.title, s.project_name, s.last_epoch
               FROM search_fts f JOIN sessions s USING(session_id)
               WHERE search_fts MATCH ?
               ORDER BY score LIMIT ?""",
            (_fts_query(q), limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return {"results": [], "query": q, "error": "bad query"}
    return {"query": q, "results": [dict(r) for r in rows]}


def set_state(conn, session_id: str, body: dict) -> dict:
    conn.execute("INSERT OR IGNORE INTO user_state(session_id) VALUES(?)", (session_id,))
    fields, args = [], []
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


def _slug(text: str, fallback: str = "session") -> str:
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
    ext = "html" if content_type.startswith("text/html") else "md"
    filename = f"{_slug(detail.get('title'), session_id[:8] or 'session')}.{ext}"
    return {"text": text, "content_type": content_type, "filename": filename}


def _fts_query(q: str) -> str:
    """Make a forgiving FTS5 query: quote terms, prefix-match the last word."""
    terms = [t for t in q.replace('"', " ").split() if t]
    if not terms:
        return '""'
    quoted = [f'"{t}"' for t in terms[:-1]]
    quoted.append(f'"{terms[-1]}"*')
    return " ".join(quoted)


# convenience wrappers used by both server and CLI
def summary(conn): return index.session_summary(conn)
def analytics_payload(conn): return analytics.overview(conn)
def projects_payload(conn): return {"projects": analytics.projects(conn)}
def wrapped_payload(conn, year=None): return wrapped.generate(conn, year)


def ask(conn, question, session=None) -> dict:
    """Grounded, local Q&A over the indexed history (see `ask.py`). No model calls."""
    payload = ask_engine.answer(conn, question, session)
    payload["suggestions"] = ask_engine.suggestions(bool(session))
    return payload
