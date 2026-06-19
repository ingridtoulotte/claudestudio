"""SQLite index over parsed sessions — the performance core of ClaudeStudio.

Design goals:
  * Instant search over millions of messages  -> FTS5 (bm25 ranking).
  * Incremental updates                        -> skip files whose (mtime,size)
                                                  are unchanged since last index.
  * User state survives re-indexing            -> favorites / archive / tags /
                                                  notes live in their own table,
                                                  keyed by session_id, never wiped.

The schema is intentionally denormalized on the `sessions` table so the common
list/sort/filter queries hit one table with covering indexes and stay instant
even at thousands of sessions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Iterable

from . import parser
from .parser import ParsedSession

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    path        TEXT PRIMARY KEY,
    session_id  TEXT,
    mtime       REAL,
    size        INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    title        TEXT,
    project      TEXT,
    project_name TEXT,
    git_branch   TEXT,
    version      TEXT,
    first_ts     TEXT,
    last_ts      TEXT,
    first_epoch  REAL,
    last_epoch   REAL,
    duration_s   REAL,
    msg_count    INTEGER,
    user_msgs    INTEGER,
    assistant_msgs INTEGER,
    tool_calls   INTEGER,
    models       TEXT,
    primary_model TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cache_write   INTEGER,
    cache_read    INTEGER,
    cost_usd     REAL,
    file_path    TEXT,
    preview      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_last  ON sessions(last_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_proj  ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_cost  ON sessions(cost_usd DESC);

CREATE TABLE IF NOT EXISTS messages (
    uuid        TEXT PRIMARY KEY,
    session_id  TEXT,
    parent_uuid TEXT,
    role        TEXT,
    ts          TEXT,
    epoch       REAL,
    seq         INTEGER,
    model       TEXT,
    text        TEXT,
    thinking    TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cache_write   INTEGER,
    cache_read    INTEGER,
    cost_usd    REAL,
    tool_count  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    message_uuid TEXT,
    seq         INTEGER,
    name        TEXT,
    ts          TEXT,
    is_error    INTEGER,
    input_json  TEXT,
    result_preview TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_session ON tool_calls(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_tool_name    ON tool_calls(name);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    body,
    session_id UNINDEXED,
    message_uuid UNINDEXED,
    seq UNINDEXED,
    kind UNINDEXED,
    tokenize = 'porter unicode61'
);

-- user state is never touched by reindexing
CREATE TABLE IF NOT EXISTS user_state (
    session_id TEXT PRIMARY KEY,
    favorite   INTEGER DEFAULT 0,
    archived   INTEGER DEFAULT 0,
    tags       TEXT DEFAULT '[]',
    notes      TEXT DEFAULT ''
);

-- saved searches / smart collections — user-owned, survives reindexing
CREATE TABLE IF NOT EXISTS saved_searches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT,
    query      TEXT DEFAULT '',
    sort       TEXT DEFAULT 'recent',
    filters    TEXT DEFAULT '{}',
    created_at REAL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


def default_db_path() -> str:
    base = os.path.join(os.path.expanduser("~"), ".claudestudio")
    return os.path.join(base, "index.db")


# ---------------------------------------------------------------------------
# write path
# ---------------------------------------------------------------------------

def _delete_session(conn, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM tool_calls WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM search_fts WHERE session_id=?", (session_id,))


def _insert_session(conn, ps: ParsedSession) -> None:
    from .parser import _parse_ts

    project_name = os.path.basename(ps.project.rstrip("/\\")) or ps.project
    primary_model = ""
    if ps.models:
        # most-used model wins as "primary"
        counts: dict[str, int] = {}
        for m in ps.messages:
            if m.model:
                counts[m.model] = counts.get(m.model, 0) + 1
        primary_model = max(counts, key=counts.get) if counts else ps.models[0]

    preview = ""
    for m in ps.messages:
        if m.role == "user" and not m.is_meta and m.text:
            preview = m.text[:280]
            break

    conn.execute(
        """INSERT OR REPLACE INTO sessions VALUES
           (:sid,:title,:project,:pname,:branch,:version,:fts,:lts,:fe,:le,:dur,
            :mc,:um,:am,:tc,:models,:pm,:it,:ot,:cw,:cr,:cost,:fp,:prev)""",
        dict(
            sid=ps.session_id, title=ps.title, project=ps.project, pname=project_name,
            branch=ps.git_branch, version=ps.version, fts=ps.first_ts, lts=ps.last_ts,
            fe=_parse_ts(ps.first_ts) or 0.0, le=_parse_ts(ps.last_ts) or 0.0,
            dur=ps.duration_seconds, mc=len(ps.messages), um=ps.user_msgs,
            am=ps.assistant_msgs, tc=ps.tool_call_count,
            models=json.dumps(ps.models), pm=primary_model,
            it=ps.total_input, ot=ps.total_output, cw=ps.total_cache_write,
            cr=ps.total_cache_read, cost=ps.cost_usd, fp=ps.file_path, prev=preview,
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO user_state(session_id) VALUES(?)", (ps.session_id,)
    )

    msg_rows, tool_rows, fts_rows = [], [], []
    for m in ps.messages:
        msg_rows.append((
            m.uuid, ps.session_id, m.parent_uuid, m.role, m.ts, _parse_ts(m.ts) or 0.0,
            m.seq, m.model, m.text, m.thinking, m.input_tokens, m.output_tokens,
            m.cache_write_tokens, m.cache_read_tokens, m.cost_usd, len(m.tool_calls),
        ))
        body = "\n".join(p for p in (m.text, m.thinking) if p)
        if body:
            fts_rows.append((body, ps.session_id, m.uuid, m.seq, m.role))
        for tc in m.tool_calls:
            tj = json.dumps(tc.input)[:8000]
            tool_rows.append((
                ps.session_id, m.uuid, m.seq, tc.name, tc.ts,
                1 if tc.is_error else 0, tj, tc.result_preview,
            ))
            fts_rows.append((
                f"{tc.name} {tj}", ps.session_id, m.uuid, m.seq, "tool",
            ))

    conn.executemany(
        "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        msg_rows,
    )
    conn.executemany(
        """INSERT INTO tool_calls
           (session_id,message_uuid,seq,name,ts,is_error,input_json,result_preview)
           VALUES (?,?,?,?,?,?,?,?)""",
        tool_rows,
    )
    conn.executemany(
        "INSERT INTO search_fts(body,session_id,message_uuid,seq,kind) VALUES (?,?,?,?,?)",
        fts_rows,
    )


def reindex(
    conn: sqlite3.Connection,
    root: str | None = None,
    *,
    force: bool = False,
    progress=None,
) -> dict:
    """Scan the projects root and (incrementally) update the index.

    Returns a stats dict. `progress(done, total)` is called as files are processed.
    """
    root = root or parser.default_projects_root()
    files = list(parser.iter_session_files(root)) if os.path.isdir(root) else []
    total = len(files)

    known = {
        r["path"]: (r["mtime"], r["size"], r["session_id"])
        for r in conn.execute("SELECT path,mtime,size,session_id FROM sources")
    }
    seen_paths = set()
    added = updated = skipped = 0

    for i, path in enumerate(files):
        seen_paths.add(path)
        try:
            st = os.stat(path)
        except OSError:
            continue
        prior = known.get(path)
        if (not force) and prior and prior[0] == st.st_mtime and prior[1] == st.st_size:
            skipped += 1
            if progress:
                progress(i + 1, total)
            continue

        ps = parser.parse_file(path)
        if ps is None:
            continue
        if prior:
            _delete_session(conn, prior[2])
            updated += 1
        else:
            added += 1
        _delete_session(conn, ps.session_id)  # guard against id collisions
        _insert_session(conn, ps)
        conn.execute(
            "INSERT OR REPLACE INTO sources(path,session_id,mtime,size) VALUES (?,?,?,?)",
            (path, ps.session_id, st.st_mtime, st.st_size),
        )
        if (added + updated) % 25 == 0:
            conn.commit()
        if progress:
            progress(i + 1, total)

    # drop sessions whose source file disappeared
    removed = 0
    for path, (_, _, sid) in known.items():
        if path not in seen_paths and not os.path.exists(path):
            _delete_session(conn, sid)
            conn.execute("DELETE FROM sources WHERE path=?", (path,))
            removed += 1

    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('indexed_at',?)",
        (str(time.time()),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('root',?)", (root,)
    )
    conn.commit()
    return {
        "root": root, "files": total, "added": added, "updated": updated,
        "skipped": skipped, "removed": removed,
    }


# ---------------------------------------------------------------------------
# read helpers used by the API layer
# ---------------------------------------------------------------------------

def session_summary(conn) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(msg_count),0) msgs,
                  COALESCE(SUM(tool_calls),0) tools,
                  COALESCE(SUM(input_tokens+output_tokens+cache_write+cache_read),0) tokens,
                  COALESCE(SUM(cost_usd),0) cost,
                  COUNT(DISTINCT project) projects
           FROM sessions"""
    ).fetchone()
    indexed_at = conn.execute(
        "SELECT value FROM meta WHERE key='indexed_at'"
    ).fetchone()
    return {
        "sessions": row["n"], "messages": row["msgs"], "tool_calls": row["tools"],
        "tokens": row["tokens"], "cost_usd": row["cost"], "projects": row["projects"],
        "indexed_at": float(indexed_at["value"]) if indexed_at else None,
    }
