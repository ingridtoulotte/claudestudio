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
# v3 (v0.5.2): `sessions` gained a cached `health_score` column, and four new
# user-owned tables landed — `budgets` (spend ceilings), `annotations` (inline
# session/message notes) with its `annotations_fts` shadow, and `prompt_library`
# (a personal, starrable prompt collection). The migration adds the column in
# place; the tables are created by the schema script with IF NOT EXISTS. All
# indexed data and user state is preserved.
# v4 (v0.6.0): a `session_github_refs` table holds the GitHub issue/PR references
# detected in each session (#123, owner/repo#456, full URLs). It is rebuilt from
# the source on (re)index — derived data, not user state — so an old index simply
# starts empty and fills in on the next reindex. The migration only creates the
# table (schema script, IF NOT EXISTS) and bumps the version; no data is lost.
# v5 (v0.6.1): user-owned session tagging system. Two new tables — `available_tags`
# (the palette of labels) and `session_tags` (which session carries which label) —
# plus a `preferences` key/value table for cross-device UI state (theme, etc.).
# All three are user state: created by the schema script (IF NOT EXISTS) and never
# wiped by reindexing, so an old index opens cleanly and simply starts with no tags.
# v6 (v0.6.2): a `session_errors` table holds the classified tool errors found in
# each session (taxonomy: permission_error/file_not_found/syntax_error/timeout/
# api_error/assertion_failure/unknown). Like `session_github_refs` it is *derived*
# data, rebuilt from the source on every (re)index — not user state — so an old
# index simply starts empty and fills in on the next reindex. The migration only
# creates the table (schema script, IF NOT EXISTS) and bumps the version; no data
# is lost. Webhook config (v0.6.2) rides in the existing `preferences` table, so
# it needs no schema change.
SCHEMA_VERSION = 7
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
    preview      TEXT,
    health_score INTEGER
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

-- spend ceilings (Budget Tracker) — user-owned, survives reindexing. A single
-- active budget per period is the norm; the table keeps history so a ceiling
-- change is auditable. `period` is 'monthly' | 'weekly'.
CREATE TABLE IF NOT EXISTS budgets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    period     TEXT,
    ceiling_usd REAL,
    created_at REAL
);

-- inline notes on a session (message_idx = -1) or one of its messages
-- (message_idx = the 0-based seq). User-owned; never wiped by reindexing.
CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    message_idx INTEGER DEFAULT -1,
    note        TEXT DEFAULT '',
    created_at  REAL,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_annotations_session ON annotations(session_id, message_idx);

-- full-text shadow for annotation notes, kept in lockstep by the annotation
-- CRUD helpers (reindexing never touches it, so notes are always searchable).
CREATE VIRTUAL TABLE IF NOT EXISTS annotations_fts USING fts5(
    note,
    annotation_id UNINDEXED,
    session_id UNINDEXED,
    tokenize = 'porter unicode61'
);

-- a personal, reusable prompt library — auto-extracted from history and/or
-- hand-added. User-owned; survives reindexing. `source` is 'extracted' | 'manual'.
CREATE TABLE IF NOT EXISTS prompt_library (
    id         TEXT PRIMARY KEY,
    text       TEXT,
    source     TEXT DEFAULT 'manual',
    frequency  INTEGER DEFAULT 1,
    starred    INTEGER DEFAULT 0,
    created_at REAL
);

-- GitHub issue/PR references found in a session (#123, owner/repo#456, full URLs).
-- Derived data (rebuilt on reindex, keyed by session), NOT user state.
CREATE TABLE IF NOT EXISTS session_github_refs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    seq         INTEGER,
    ref         TEXT,
    owner       TEXT,
    repo        TEXT,
    number      INTEGER,
    kind        TEXT,
    url         TEXT
);
CREATE INDEX IF NOT EXISTS idx_ghrefs_session ON session_github_refs(session_id);
CREATE INDEX IF NOT EXISTS idx_ghrefs_number  ON session_github_refs(number);
CREATE INDEX IF NOT EXISTS idx_ghrefs_repo    ON session_github_refs(owner, repo);

-- v5 (v0.6.1): user-defined session tags. The palette of available labels and
-- the per-session applications live in their own tables — user state, never
-- wiped by reindexing (like favorites/notes). Tag names are normalised lowercase.
CREATE TABLE IF NOT EXISTS available_tags (
    id         TEXT PRIMARY KEY,
    name       TEXT UNIQUE,
    colour     TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS session_tags (
    session_id TEXT,
    tag_id     TEXT,
    created_at REAL,
    PRIMARY KEY (session_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_session_tags_tag ON session_tags(tag_id);

-- v5 (v0.6.1): cross-device UI preferences (theme, etc.). A tiny key/value store;
-- user state, survives reindexing. The frontend persists locally too, but a write
-- here keeps the choice consistent across machines sharing one synced index.
CREATE TABLE IF NOT EXISTS preferences (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- v6 (v0.6.2): classified tool errors per session (Error Taxonomy). Derived data
-- (rebuilt on every reindex, keyed by session), NOT user state — an old index
-- starts empty and backfills on the next reindex. `error_type` is one of the
-- fixed taxonomy buckets; `message_idx` is the owning message's 0-based seq.
CREATE TABLE IF NOT EXISTS session_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    error_type  TEXT NOT NULL,
    error_text  TEXT,
    tool_name   TEXT,
    message_idx INTEGER,
    ts          TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_errors_type ON session_errors(error_type);
CREATE INDEX IF NOT EXISTS idx_session_errors_session ON session_errors(session_id);

-- v7 (v0.6.3): persistent search history. User-owned (the searches you ran),
-- never wiped by reindexing. Capped at SEARCH_HISTORY_MAX rows (oldest pruned on
-- insert) so it can never grow without bound. `kind` mirrors the optional search
-- filter (user|assistant|tool|NULL); `searched_at` is unix epoch seconds.
CREATE TABLE IF NOT EXISTS search_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT    NOT NULL,
    kind         TEXT,
    project      TEXT,
    result_count INTEGER,
    searched_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_history_time ON search_history(searched_at DESC);
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
    # v3: cached per-session health score. Idempotent — a fresh db already has the
    # column from the schema script; an old v1/v2 db gains it here (nullable, so
    # existing rows read as "unscored" until the next reindex recomputes them).
    sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "health_score" not in sess_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN health_score INTEGER")
    # v4: session_github_refs is created by the schema script (IF NOT EXISTS) and
    # populated lazily on the next reindex (it's derived data, never user state),
    # so there is nothing to migrate beyond recording the version below.
    # v5 (v0.6.1): user-owned session tagging system. `available_tags`,
    # `session_tags` and `preferences` are created by the schema script
    # (IF NOT EXISTS) and hold user state only — an old v4 index gains the empty
    # tables here and keeps every session, favorite, note and annotation intact.
    # Nothing to copy or backfill, so recording the version below is the migration.
    # v6 (v0.6.2): `session_errors` is created by the schema script (IF NOT EXISTS)
    # and populated lazily on the next reindex (derived data, never user state), so
    # there is nothing to migrate beyond recording the version below.
    # v7 (v0.6.3): `search_history` is created by the schema script (IF NOT EXISTS)
    # and holds user state only (the searches you ran). An old v6 index gains the
    # empty table here and keeps every session, favorite, note, tag and annotation
    # intact — nothing to copy or backfill, so recording the version below is the
    # whole migration.
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
    conn.execute("DELETE FROM session_github_refs WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM session_errors WHERE session_id=?", (session_id,))


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

    # Cache the deterministic session-health score (0..100) so the list view can
    # sort/colour by it without recomputing per request. Pure function of the
    # parsed session — no model calls. Imported lazily to keep `health` free to
    # import `parser` types without a cycle back through `index`.
    from . import health
    health_score = health.compute_health_score(ps)["score"]

    conn.execute(
        """INSERT OR REPLACE INTO sessions VALUES
           (:sid,:title,:project,:pname,:branch,:version,:fts,:lts,:fe,:le,:dur,
            :mc,:um,:am,:tc,:models,:pm,:it,:ot,:cw,:cr,:cost,:fp,:prev,:health)""",
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
            "prev": preview, "health": health_score,
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

    # v4: detect + store GitHub issue/PR references (derived data; rebuilt here
    # on every (re)index). Imported lazily to avoid any import cycle.
    from . import github_linker
    gh_rows = [
        (ps.session_id, ref["seq"], ref["ref"], ref["owner"], ref["repo"],
         ref["number"], ref["kind"], ref["url"])
        for ref in github_linker.extract_from_session(ps)
    ]
    if gh_rows:
        conn.executemany(
            "INSERT INTO session_github_refs"
            "(session_id,seq,ref,owner,repo,number,kind,url) VALUES (?,?,?,?,?,?,?,?)",
            gh_rows,
        )

    # v6: classify + store tool errors (Error Taxonomy). Derived data, rebuilt on
    # every (re)index. Imported lazily to avoid an import cycle.
    from . import error_taxonomy
    err_rows = [
        (ps.session_id, e["error_type"], e["error_text"], e["tool_name"],
         e["message_idx"], e["ts"])
        for e in error_taxonomy.extract_errors(ps)
    ]
    if err_rows:
        conn.executemany(
            "INSERT INTO session_errors"
            "(session_id,error_type,error_text,tool_name,message_idx,ts) "
            "VALUES (?,?,?,?,?,?)",
            err_rows,
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


# ---------------------------------------------------------------------------
# annotations  (inline session/message notes — user state, survive reindexing)
# ---------------------------------------------------------------------------
#
# An annotation is a personal note on a whole session (``message_idx = -1``) or
# on one message (``message_idx = 0-based seq``). Notes live in their own table
# (never wiped by reindexing) with an FTS5 shadow kept in lockstep here so the
# search box finds them. There is at most one note per (session, message_idx);
# writing the same target again updates it in place.

def _ann_fts_delete(conn, annotation_id: int) -> None:
    conn.execute("DELETE FROM annotations_fts WHERE annotation_id=?", (annotation_id,))


def _ann_fts_insert(conn, annotation_id: int, session_id: str, note: str) -> None:
    conn.execute(
        "INSERT INTO annotations_fts(note, annotation_id, session_id) VALUES(?,?,?)",
        (note, annotation_id, session_id),
    )


def upsert_annotation(conn, session_id: str, message_idx, note: str) -> dict:
    """Create or update the note for one (session, message) target. Returns the row.

    ``message_idx = -1`` is the session-level note. The FTS shadow is updated in
    the same transaction so a freshly-written note is immediately searchable.
    """
    try:
        midx = int(message_idx)
    except (TypeError, ValueError):
        midx = -1
    note = str(note or "")
    now = time.time()
    existing = conn.execute(
        "SELECT id FROM annotations WHERE session_id=? AND message_idx=?",
        (session_id, midx),
    ).fetchone()
    if existing:
        aid = existing["id"]
        conn.execute(
            "UPDATE annotations SET note=?, updated_at=? WHERE id=?",
            (note, now, aid),
        )
        _ann_fts_delete(conn, aid)
        _ann_fts_insert(conn, aid, session_id, note)
        created = None
    else:
        cur = conn.execute(
            "INSERT INTO annotations(session_id, message_idx, note, created_at, updated_at) "
            "VALUES(?,?,?,?,?)",
            (session_id, midx, note, now, now),
        )
        aid = cur.lastrowid
        _ann_fts_insert(conn, aid, session_id, note)
        created = now
    conn.commit()
    return {
        "id": aid, "session_id": session_id, "message_idx": midx, "note": note,
        "created_at": created if created is not None else now, "updated_at": now,
    }


def list_annotations(conn, session_id: str) -> list[dict]:
    """All annotations for a session, session-level note first then by message."""
    rows = conn.execute(
        "SELECT id, session_id, message_idx, note, created_at, updated_at "
        "FROM annotations WHERE session_id=? ORDER BY message_idx ASC, id ASC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_annotation(conn, annotation_id) -> dict:
    try:
        aid = int(annotation_id)
    except (TypeError, ValueError):
        return {"deleted": False, "id": annotation_id}
    cur = conn.execute("DELETE FROM annotations WHERE id=?", (aid,))
    _ann_fts_delete(conn, aid)
    conn.commit()
    return {"deleted": cur.rowcount > 0, "id": aid}


def search_annotations(conn, query: str, limit: int = 50) -> list[dict]:
    """Full-text search over annotation notes (FTS5). Returns matching notes with
    their session title for deep-linking. Empty/garbled query → []."""
    q = (query or "").strip()
    if not q:
        return []
    terms = [f'"{t}"' for t in q.replace('"', " ").split() if t]
    if not terms:
        return []
    match = " ".join(terms[:-1] + [terms[-1] + "*"]) if terms else '""'
    try:
        rows = conn.execute(
            "SELECT a.id, a.session_id, a.message_idx, a.note, "
            "       COALESCE(s.title,'') AS session_title "
            "FROM annotations_fts f "
            "JOIN annotations a ON a.id = f.annotation_id "
            "LEFT JOIN sessions s ON s.session_id = a.session_id "
            "WHERE annotations_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (match, max(1, int(limit))),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# prompt library  (a personal, reusable prompt collection — user state)
# ---------------------------------------------------------------------------

def upsert_prompt(conn, *, prompt_id=None, text: str, source: str = "manual",
                  frequency: int = 1, starred=None) -> dict:
    """Insert or update one library prompt. A new prompt gets a random hex id."""
    text = str(text or "").strip()
    if prompt_id:
        pid = str(prompt_id)
        existing = conn.execute(
            "SELECT id, starred, frequency FROM prompt_library WHERE id=?", (pid,)
        ).fetchone()
    else:
        pid, existing = uuid.uuid4().hex, None
    star_val = (1 if starred else 0) if starred is not None else (
        existing["starred"] if existing else 0
    )
    if existing:
        conn.execute(
            "UPDATE prompt_library SET text=?, source=?, frequency=?, starred=? WHERE id=?",
            (text or "", source, int(frequency), int(star_val), pid),
        )
        created = None
    else:
        created = time.time()
        conn.execute(
            "INSERT INTO prompt_library(id, text, source, frequency, starred, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (pid, text, source, int(frequency), int(star_val), created),
        )
    conn.commit()
    row = conn.execute(
        "SELECT id, text, source, frequency, starred, created_at "
        "FROM prompt_library WHERE id=?", (pid,)
    ).fetchone()
    d = dict(row)
    d["starred"] = bool(d["starred"])
    return d


def list_prompts(conn, q: str | None = None, starred=None, limit: int = 200) -> list[dict]:
    """List library prompts, optionally filtered by substring `q` and/or starred."""
    where, args = ["1=1"], []
    if q and q.strip():
        where.append("text LIKE ? ESCAPE '\\'")
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args.append(f"%{needle}%")
    if starred:
        where.append("starred = 1")
    clause = " AND ".join(where)
    rows = conn.execute(
        f"SELECT id, text, source, frequency, starred, created_at "
        f"FROM prompt_library WHERE {clause} "
        f"ORDER BY starred DESC, frequency DESC, created_at DESC "
        f"LIMIT ?",
        (*args, max(1, int(limit))),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["starred"] = bool(d["starred"])
        out.append(d)
    return out


def delete_prompt(conn, prompt_id) -> dict:
    cur = conn.execute("DELETE FROM prompt_library WHERE id=?", (str(prompt_id),))
    conn.commit()
    return {"deleted": cur.rowcount > 0, "id": prompt_id}


# ---------------------------------------------------------------------------
# GitHub references  (derived data — rebuilt on reindex, Feature 2.10)
# ---------------------------------------------------------------------------

def github_refs_for_session(conn, session_id: str) -> list[dict]:
    """Every GitHub issue/PR reference detected in one session (by message order)."""
    rows = conn.execute(
        "SELECT seq, ref, owner, repo, number, kind, url "
        "FROM session_github_refs WHERE session_id=? ORDER BY seq, number",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def search_github_refs(conn, *, number=None, repo: str = "", owner: str = "",
                       limit: int = 100) -> list[dict]:
    """Find the sessions that referenced a given issue/PR number and/or repo.

    Filters are AND-combined; all optional. ``repo`` matches the repo name and
    ``owner`` the owner; ``number`` the issue/PR number. Returns one row per
    (session, ref) with the session title for deep-linking. Deterministic order.
    """
    where: list = ["1=1"]
    args: list = []
    if number is not None:
        try:
            where.append("g.number=?")
            args.append(int(number))
        except (TypeError, ValueError):
            return []
    if repo:
        where.append("LOWER(g.repo)=?")
        args.append(str(repo).lower())
    if owner:
        where.append("LOWER(g.owner)=?")
        args.append(str(owner).lower())
    rows = conn.execute(
        f"SELECT g.session_id, g.seq, g.ref, g.owner, g.repo, g.number, g.kind, g.url, "
        f"       COALESCE(s.title,'') AS session_title, COALESCE(s.last_epoch,0) AS last_epoch "
        f"FROM session_github_refs g LEFT JOIN sessions s USING(session_id) "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY s.last_epoch DESC, g.session_id, g.seq LIMIT ?",
        (*args, max(1, int(limit))),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# preferences  (cross-device UI state — user-owned, survives reindexing, v5)
# ---------------------------------------------------------------------------

def get_preference(conn, key: str, default: str | None = None) -> str | None:
    """Read one preference value, or `default` when unset. Never raises."""
    row = conn.execute(
        "SELECT value FROM preferences WHERE key=?", (str(key),)
    ).fetchone()
    return row["value"] if row else default


def set_preference(conn, key: str, value: str) -> dict:
    """Upsert one preference key/value pair. Returns the stored row."""
    conn.execute(
        "INSERT OR REPLACE INTO preferences(key,value) VALUES(?,?)",
        (str(key), str(value)),
    )
    conn.commit()
    return {"key": str(key), "value": str(value)}


def all_preferences(conn) -> dict:
    """Every stored preference as a flat dict (empty when none set)."""
    return {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM preferences"
    )}
