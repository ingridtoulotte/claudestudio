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
import uuid

from . import parser
from .parser import ParsedSession

# Bump this whenever the on-disk schema changes in a way an old index can't
# satisfy, and add the matching step to `maybe_migrate`. The version is stored
# in the `meta` table so an upgrade can migrate forward — and a *downgrade*
# (opening a newer index with an older build) fails loudly instead of silently
# returning wrong data.
# v2: `sources` gained a `root` column so one index can span several projects
# roots (work laptop + personal machine + remote). The migration adds the column
# in place, preserving all indexed data and user state.
SCHEMA_VERSION = 2
# Back-compat alias for callers/tests that want the explicit name.
CURRENT_SCHEMA_VERSION = SCHEMA_VERSION


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
    size        INTEGER,
    root        TEXT
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
CREATE INDEX IF NOT EXISTS idx_msg_model   ON messages(model);

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

-- per-message bookmarks — user-owned, never wiped by reindexing (like user_state)
CREATE TABLE IF NOT EXISTS bookmarks (
    id            TEXT PRIMARY KEY,
    session_id    TEXT,
    message_seq   INTEGER,
    note          TEXT DEFAULT '',
    created_epoch REAL
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_session ON bookmarks(session_id, message_seq);
"""


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        maybe_migrate(conn)
        conn.commit()
    except Exception:
        # Don't leak the open handle when migration rejects a newer-schema index
        # (a leaked handle blocks the file from being removed/rebuilt on Windows).
        conn.close()
        raise
    return conn


def stored_schema_version(conn: sqlite3.Connection) -> int:
    """Read the schema version recorded in the index, or 0 if none/garbage.

    0 means "predates versioning" — `maybe_migrate` treats it as a baseline that
    upgrades cleanly to the current version.
    """
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def maybe_migrate(conn: sqlite3.Connection) -> None:
    """Bring an index up to `SCHEMA_VERSION`. Safe (idempotent) on every open.

    The schema script (run by `connect`) already creates every table with
    ``IF NOT EXISTS``; this owns the *version bookkeeping* and the forward/back
    safety checks. Opening an index written by a newer ClaudeStudio raises a
    clear, actionable error instead of letting a build read a schema it doesn't
    understand and return wrong numbers.
    """
    stored = stored_schema_version(conn)
    if stored > SCHEMA_VERSION:
        raise RuntimeError(
            f"This index was written by a newer ClaudeStudio (schema v{stored}) "
            f"than this build (schema v{SCHEMA_VERSION}). Upgrade ClaudeStudio, "
            f"or delete the index and re-run `claudestudio index` to rebuild it."
        )
    # Forward migrations go here, ordered and idempotent. Each lifts an old index
    # to the next version without losing indexed data or user state.
    # v2: multi-root — tag every source with the projects root it came from.
    # Idempotent: only ALTER when the column is genuinely absent (a fresh db
    # already has it from the schema script, an old v1 db does not).
    src_cols = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
    if "root" not in src_cols:
        conn.execute("ALTER TABLE sources ADD COLUMN root TEXT")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
        (str(SCHEMA_VERSION),),
    )


def connect_ro(db_path: str) -> sqlite3.Connection:
    """Open the index for *reading only* — no schema script, no writes.

    Read endpoints hit this on every request, so it skips the
    ``executescript(_SCHEMA)`` + PRAGMA + meta-insert that :func:`connect` runs
    (measured ~0.39 ms of pure per-request overhead). ``query_only`` makes any
    accidental write fail loudly. The schema is the writers' responsibility —
    ``serve``/``index`` build it via :func:`connect` before any read is served.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
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
        primary_model = max(counts, key=lambda k: counts[k]) if counts else ps.models[0]

    preview = ""
    for m in ps.messages:
        if m.role == "user" and not m.is_meta and m.text:
            preview = m.text[:280]
            break

    conn.execute(
        """INSERT OR REPLACE INTO sessions VALUES
           (:sid,:title,:project,:pname,:branch,:version,:fts,:lts,:fe,:le,:dur,
            :mc,:um,:am,:tc,:models,:pm,:it,:ot,:cw,:cr,:cost,:fp,:prev)""",
        {
            "sid": ps.session_id, "title": ps.title, "project": ps.project,
            "pname": project_name, "branch": ps.git_branch, "version": ps.version,
            "fts": ps.first_ts, "lts": ps.last_ts,
            "fe": _parse_ts(ps.first_ts) or 0.0, "le": _parse_ts(ps.last_ts) or 0.0,
            "dur": ps.duration_seconds, "mc": len(ps.messages), "um": ps.user_msgs,
            "am": ps.assistant_msgs, "tc": ps.tool_call_count,
            "models": json.dumps(ps.models), "pm": primary_model,
            "it": ps.total_input, "ot": ps.total_output, "cw": ps.total_cache_write,
            "cr": ps.total_cache_read, "cost": ps.cost_usd, "fp": ps.file_path,
            "prev": preview,
        },
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


def normalize_roots(root) -> list[str]:
    """Coerce the `root` argument into a deduplicated list of root paths.

    Accepts ``None`` (→ the default projects root), a single ``str``/``Path``, or
    a list/tuple of them — so every existing single-root caller keeps working
    while power users can index several machines/installs in one index.
    """
    if root is None:
        return [parser.default_projects_root()]
    if isinstance(root, (str, os.PathLike)):
        items = [os.fspath(root)]
    else:
        items = [os.fspath(r) for r in root]
    seen, out = set(), []
    for r in items:
        r = str(r)
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out or [parser.default_projects_root()]


def reindex(
    conn: sqlite3.Connection,
    root=None,
    *,
    force: bool = False,
    progress=None,
) -> dict:
    """Scan one or more projects roots and (incrementally) update the index.

    `root` may be ``None``, a single path, or a list of paths (multi-root). Each
    indexed file is tagged with the root it was found under, so the API can later
    filter by root. Returns a stats dict; `progress(done, total)` is called as
    files are processed.
    """
    roots = normalize_roots(root)

    # (path -> root) across every configured root; first root wins if a file is
    # reachable from two overlapping roots, so a path maps to exactly one root.
    file_root: dict[str, str] = {}
    for rt in roots:
        if not os.path.isdir(rt):
            continue
        for path in parser.iter_session_files(rt):
            file_root.setdefault(path, rt)
    files = list(file_root.keys())
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
            "INSERT OR REPLACE INTO sources(path,session_id,mtime,size,root) "
            "VALUES (?,?,?,?,?)",
            (path, ps.session_id, st.st_mtime, st.st_size, file_root[path]),
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
    # 'root' (first) kept for back-compat; 'roots' holds the full list.
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('root',?)", (roots[0],)
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('roots',?)",
        (json.dumps(roots),),
    )
    conn.commit()
    return {
        "root": roots[0], "roots": roots, "files": total, "added": added,
        "updated": updated, "skipped": skipped, "removed": removed,
    }


def index_db_mtime(db_path: str) -> float:
    """Modification time of the index file, or 0.0 if it doesn't exist yet.

    The SSE watch endpoint compares this between polls — when it changes, an
    ``index`` run (hook, CLI, or the in-app Sync) refreshed the data, so connected
    browsers get a "new sessions" nudge.
    """
    try:
        return os.path.getmtime(db_path)
    except OSError:
        return 0.0


def newest_source_mtime(root=None) -> float:
    """Newest mtime among all `.jsonl` session files under the given root(s).

    Pure filesystem scan (no DB), used by `claudestudio watch` to decide when to
    reindex. Returns 0.0 when nothing is found. Accepts the same `root` shapes as
    :func:`reindex` (None / str / list).
    """
    newest = 0.0
    for rt in normalize_roots(root):
        if not os.path.isdir(rt):
            continue
        for p in parser.iter_session_files(rt):
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m > newest:
                newest = m
    return newest


def root_counts(conn) -> list[dict]:
    """Per-root session counts, for `doctor` / `info` / the Projects view."""
    rows = conn.execute(
        "SELECT COALESCE(root,'(unknown)') AS root, COUNT(DISTINCT session_id) AS sessions "
        "FROM sources GROUP BY root ORDER BY sessions DESC"
    ).fetchall()
    return [dict(r) for r in rows]


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


# ---------------------------------------------------------------------------
# per-message bookmarks  (user state — survives reindexing, never wiped)
# ---------------------------------------------------------------------------

def add_bookmark(conn, session_id: str, seq, note: str = "") -> dict:
    """Create a bookmark on one message of a session. Returns the new row.

    `seq` is the message's sequence index inside the session (0-based, as the
    timeline/replay use). The id is a random hex token so the client never has to
    guess it. Bookmarks live in their own table and are untouched by reindexing.
    """
    try:
        s = int(seq)
    except (TypeError, ValueError):
        s = 0
    bid = uuid.uuid4().hex
    epoch = time.time()
    conn.execute(
        "INSERT INTO bookmarks(id,session_id,message_seq,note,created_epoch) "
        "VALUES(?,?,?,?,?)",
        (bid, session_id, s, str(note or ""), epoch),
    )
    conn.commit()
    return {"id": bid, "session_id": session_id, "seq": s,
            "note": str(note or ""), "created_epoch": epoch}


def delete_bookmark(conn, bookmark_id: str) -> dict:
    cur = conn.execute("DELETE FROM bookmarks WHERE id=?", (str(bookmark_id),))
    conn.commit()
    return {"deleted": cur.rowcount > 0, "id": bookmark_id}


def list_bookmarks(conn, session_id: str | None = None) -> list[dict]:
    """All bookmarks (newest first), or just one session's when `session_id` is set.

    Joins the session title so a global bookmarks view can deep-link with context.
    """
    if session_id:
        rows = conn.execute(
            "SELECT b.id, b.session_id, b.message_seq AS seq, b.note, b.created_epoch, "
            "       COALESCE(s.title,'') AS session_title "
            "FROM bookmarks b LEFT JOIN sessions s USING(session_id) "
            "WHERE b.session_id=? ORDER BY b.message_seq ASC, b.created_epoch ASC",
            (session_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT b.id, b.session_id, b.message_seq AS seq, b.note, b.created_epoch, "
            "       COALESCE(s.title,'') AS session_title "
            "FROM bookmarks b LEFT JOIN sessions s USING(session_id) "
            "ORDER BY b.created_epoch DESC, b.id ASC"
        ).fetchall()
    return [dict(r) for r in rows]
