# ClaudeStudio Architecture

A map of how the pieces fit together. ClaudeStudio is a zero-dependency,
local-first Python application: it reads Claude Code's `.jsonl` session logs,
indexes them into SQLite, and serves a single-page app, a CLI, and an MCP server
over the same query layer.

## Data flow

```
~/.claude/projects/**/*.jsonl
        │
        ▼
   parser.py          parse one session → ParsedSession (plain dataclasses)
        │             (Message, ToolCall; no SQL, no I/O policy)
        ▼
   index.py           reindex(): incremental, skips files whose (mtime,size)
        │             are unchanged. Writes the sessions / messages / tool_calls
        │             tables + the search_fts FTS5 shadow table. User state
        │             (favorites/tags/notes/saved searches) lives in its own
        │             tables and is never wiped by reindexing.
        ▼
   SQLite index (~/.claudestudio/index.db, WAL mode)
        │
        ├── api.py · analytics.py · ask.py · highlights.py · wrapped.py · export.py
        │   (pure reads — the same functions back every surface below)
        │
        ├── server.py  → HTTP JSON API + static SPA (web/)        → browser UI
        ├── cli.py     → list / search / ask / export / highlights … → terminal
        └── mcp.py     → JSON-RPC 2.0 over stdio (14 tools)       → Claude Code / MCP
```

The key invariant: **`server.py` is the only module that knows about HTTP**, and
`cli.py`/`mcp.py` are the only modules that know about their transports. Everything
else is transport-free and returns JSON-able Python, so the self-test can exercise
the real logic directly and the three surfaces can never drift apart.

## Incremental index design

`reindex()` records each source file's `(path, mtime, size, session_id)` in a
`sources` table. On a subsequent run a file whose `mtime` and `size` are unchanged
is skipped entirely; a changed file has its old rows deleted and is re-parsed; a
file that disappeared has its session dropped. This keeps re-indexing fast even at
thousands of sessions. The denormalized `sessions` table carries every column the
common list/sort/filter queries need, so those hit one table with covering indexes.

## FTS5 + BM25 ranking

`search_fts` is an FTS5 virtual table (`tokenize = 'porter unicode61'`) holding
one row per message body and per tool call. Search ranks by `bm25(search_fts)`
(lower = more relevant) with a deterministic tiebreak (`last_epoch DESC, session_id,
seq`) so the same query always returns the same order. `_fts_query` quotes each
term and prefix-matches the final word for forgiving "search as you type". Local
filters (kind/project/session/date) are applied as SQL `WHERE` clauses on top.

## Schema versioning & migrations

`index.SCHEMA_VERSION` is stored in the `meta` table. `maybe_migrate()` runs on
every `connect()`: it is idempotent, applies forward-only migrations, and **raises
a clear error if it opens an index written by a newer build** (so an old binary
never reads a schema it doesn't understand and returns wrong numbers). `connect()`
closes the handle if migration rejects the index, so the file can be rebuilt.

## API routes

| Method | Path | Handler | Notes |
|--------|------|---------|-------|
| GET | `/api/summary` | `api.summary` | Headline totals. |
| GET | `/api/sessions` | `api.list_sessions` | Filter/sort/paginate; `q`, `project`, `model`, `since`, `until`, `favorite`, `archived`. |
| GET | `/api/session/{id}` | `api.get_session` | Full timeline with tool calls. |
| GET | `/api/session/{id}/similar` | `api.similar_sessions` | TF-IDF cosine neighbours. |
| GET | `/api/session/{id}/export[.fmt]` | `api.export_session` | `md` / `html` / `json`. |
| GET | `/api/search` | `api.search` | FTS5 + BM25, with filters. |
| GET | `/api/analytics` | `analytics.overview` | Models, tools, daily, heatmap, projects. |
| GET | `/api/projects` | `analytics.projects` | Per-project rollups. |
| GET | `/api/tools/stats` | `api.tools_stats` | Leaderboard, success rates, co-occurrence, most-edited. |
| GET | `/api/graph` | `api.graph` | Knowledge-graph nodes + edges. |
| GET | `/api/highlights` | `highlights.generate` | Deterministic highlight categories. |
| GET | `/api/wrapped` | `wrapped.generate` | Year-in-review. |
| GET | `/api/compare` | `api.compare` | Two sessions side by side. |
| GET | `/api/ask` | `api.ask` | Grounded local Q&A (no model). |
| GET | `/api/saved` | `api.list_saved` | Saved searches. |
| POST | `/api/reindex` | `index.reindex` | Refresh the index. |
| POST | `/api/saved` | `api.add_saved` | Create a saved search. |
| POST | `/api/state/{id}` | `api.set_state` | Favorite / archive / tags / notes. |
| DELETE | `/api/saved/{id}` | `api.delete_saved` | Remove a saved search. |

## Security posture

Local-first by design: the server binds to `127.0.0.1`, makes no outbound calls,
and emits defence-in-depth headers (CSP, `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`) on every response. A Host-header check defeats DNS-rebinding; a
`Sec-Fetch-Site`/`Origin` check blocks cross-site state changes; static-file serving
is path-traversal-contained via `realpath` + `commonpath`. Every SQL query is
parameterized. Binding to a non-loopback host is an explicit opt-in with a loud
warning.

## MCP server

See [`MCP.md`](MCP.md). `mcp.py` is a JSON-RPC 2.0 stdio server whose tools are
thin adapters over the same `api`/`analytics`/`ask` functions — so the MCP surface
inherits the rest of the system's behaviour for free, and `handle_request` is unit
-tested directly in the self-test without spawning a process.
