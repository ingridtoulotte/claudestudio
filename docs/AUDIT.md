# ClaudeStudio — Backend & Architecture Audit

_Evidence-based audit. Measurements from a deterministic 400-session corpus
(`fixtures.build_corpus`, seed 11): 400 sessions / 15,240 messages / 12,806 tool
calls / 22,966 FTS rows. Date: 2026-06-20._

Verdict: backend is well-built, **not finished**. Clean layering
(`parser → index → api → {server, cli, ask, export}`), pure-read API layer,
zero-dependency, deterministic, robust error handling. Search and incremental
indexing are genuinely fast. Real gaps remain: parsed-but-dropped data, a
single-field search index that caps relevance, N+1 queries in Ask, no schema
migration path, and no read-only/caching path on the server.

## Top findings

1. **Parsed-then-dropped data.** `parent_uuid`, `is_sidechain`, `skill`,
   `plugin`, `is_meta` are parsed into `Message` (`parser.py:42,54-57`) but never
   persisted — `messages` schema has no such columns (`index.py:77-94`). Branching
   tree + skill/sub-agent analytics are free data, discarded.
2. **Single-field FTS = no field weighting.** `search_fts` indexes only `body`;
   other columns are `UNINDEXED` (`index.py:111-118`). Title/project are not in
   FTS, so `search()` cannot match/rank on them. "Custom BM25 / field weighting"
   does not exist today — default bm25 on one column (`api.py:215`).
3. **Ask N+1 (measured).** `reopen_suggestions` fires **47 SQL statements** for one
   answer (40x `_ended_on_error` + per-item `_cite`->`_session_meta`)
   (`ask.py:326-327,349`).
4. **Per-request waste.** Every API call re-runs `executescript(_SCHEMA)` + PRAGMA
   + meta-insert (`index.py:147`, `server.py:60`): measured **0.39 ms/request** of
   pure overhead; no read-only path, no caching.
5. **No migration path.** `SCHEMA_VERSION=1` is written but never read
   (`index.py:27,149`); tables are `CREATE IF NOT EXISTS` only -> a column add
   silently will not apply to existing DBs.

## Measured performance

| Metric | Result | Read |
|---|---|---|
| Cold index (force) | 3.23 s (~4,700 msgs/s) | one-time; acceptable |
| Warm reindex (0 changed) | 14 ms | incremental skip excellent |
| Search p50 / p95 / max | 0.07 / 4.99 / 5.06 ms | not a bottleneck |
| analytics.overview | 16 ms (by_model 4.8 ms) | by_model = scaling risk |
| wrapped.generate | 5.2 ms | fine |
| index.connect vs bare | 0.459 vs 0.069 ms (0.39 ms/req schema overhead) | avoidable waste |
| reopen_suggestions | 1.17 ms / 47 SQL stmts | N+1, grows with window |
| file_history LIKE | 1.9 ms / 1 stmt full-scan | linear in tool_calls |
| DB size | 14.57 MB = 1.38x raw jsonl | text duplicated in FTS body |

Things that grow with a power user (10k+ sessions, 500k+ messages):
`by_model` full-scan, `file_history` LIKE scan, `reopen` N+1, cold-index time.
None user-visible at 400; all linear and addressable.

## Subsystem assessment

### Session model / parser — `parser.py`
- Current: single-pass JSONL -> `ParsedSession` dataclasses; `pending_tools` dict
  spans the file so cross-message `tool_result` linkage works (`:296-301`);
  tolerant (`errors="replace"`, skips bad JSON `:208`).
- Strengths: faithful wire model; public `parse_session` + `docs/FORMAT.md`.
- Weaknesses: aggregates are recomputed `@property` (multiple O(n) passes per
  session at index time, `:91-126`); whole file held in memory.
- Missed: `skill/plugin/is_sidechain/parent_uuid` captured then dropped.

### Indexing pipeline — `index.py`
- Current: denormalized `sessions`+`messages`+`tool_calls`+`search_fts`;
  incremental by `(mtime,size)` (`:275`); user state isolated and survives
  reindex (`:120-137`); WAL + `synchronous=NORMAL` (`:145-146`); commit/25 (`:295`).
- Strengths: incremental skip excellent (14 ms warm); covering indexes (`:73-75,95,108-109`).
- Debt: schema re-exec every connect; no migration; `primary_model` rebuilt with
  its own per-message loop (`:178-183`).

### SQLite schema
- Indexes: `sessions(last_epoch)`,`(project)`,`(cost_usd)`; `messages(session_id,seq)`;
  `tool_calls(session_id,seq)`,`(name)`.
- Gaps: **no index on `messages.model`** (hurts by_model); no `tool_paths` table
  (file lookups need LIKE scans).

### FTS5 / BM25 / Search API — `index.py:111`, `api.py:166-229`
- Current: porter+unicode61, single `body` column, `bm25(search_fts)` ordering with
  deterministic tiebreak (`:219`), `snippet()` markers (`:214`), filters
  kind/project/session/since/until, parameterized, forgiving `_fts_query`
  (last term prefixed `*`, `:349-356`).
- Strengths: fast, injection-safe, deterministic; filters useful.
- Weaknesses: one field -> no column weighting, title/project not ranked; no
  recency blend in score (recency only a tiebreak); no OR/field query syntax;
  `list_sessions` builds `WHERE session_id IN (... up to 5000 ...)` (`api.py:64-82`).

### Replay engine — `api.py:127-163`
- `get_session` builds `timeline` with computed `gap_s` in one messages query +
  one tool_calls query joined in Python. Not N+1. Good.

### Analytics — `analytics.py`
- Pure GROUP-BYs. `by_model` full-scans `messages` GROUP BY `model` with no index
  (4.8 ms of the 16 ms overview). daily/heatmap/available_years pull all session
  rows into Python (linear, fine).

### Ask mode — `ask.py`
- Strong: grounded, deterministic, cited, no model calls.
- Debt: `reopen` N+1 (47 stmts, `:326-327`); `_cite` re-queries meta per item
  (`:114-120`); `file_history` `input_json LIKE '%needle%'` unindexable scan
  (`:365`, linear in tool_calls).

### Export — `export.py`
- Clean, `html.escape` throughout, single self-contained file, zero-dep. No issues.

### Local server / API — `server.py`
- 127.0.0.1 only, per-request short-lived conn, every exception -> JSON 500 (never
  crashes, `:123,181`). `Cache-Control: no-store` everywhere; no read-only path; no
  analytics cache; schema re-exec per request.

### CLI — `cli.py`
- Full verb set (index/serve/list/search/ask/wrapped/export/doctor/stats/demo),
  UTF-8 console fix, JSON output for scripting. Solid. No `import <file>` (low value).

### Storage / caching / loading
- DB 1.38x raw jsonl: text stored twice (`messages.text/thinking` and
  `search_fts.body`, `index.py:217-219`). No result/aggregate cache anywhere.

### Error handling
- Consistently defensive across parser/index/server. Among the strongest parts.

### Tests — `selftest.py`
- 136 exact assertions, deterministic fixtures, zero-dep, cross-OS CI (+21
  UI-wiring guards added 2026-06-20). Gaps: no direct HTTP-endpoint test (only the
  `api` layer), no perf-regression guard, no schema-migration test.

## Technical debt (ranked)

| # | Debt | Severity | Effort | Evidence |
|---|---|---|---|---|
| D1 | Per-request schema re-exec; no read-only conn | Med | S | `index.py:147`, `server.py:60` (0.39 ms/req) |
| D2 | No migration despite `SCHEMA_VERSION` | Med | M | `index.py:27,149` |
| D3 | Ask N+1 (reopen 47 stmts; `_cite` re-query) | Med | S | `ask.py:326-327,349,114-120` |
| D4 | `file_history` unindexable LIKE scan | Med | M | `ask.py:365` |
| D5 | No index on `messages.model` | Low | S | `analytics.py:57-67` |
| D6 | Text duplicated (messages + FTS body) 1.38x | Low | M | `index.py:217-219` |
| D7 | No analytics/summary caching | Low | S | `server.py:146-153` |
| D8 | No HTTP-endpoint tests; no perf guard | Low | S | `selftest.py` |
| D9 | Parser aggregates = repeated O(n) `@property` | Low | S | `parser.py:91-126` |

## Feature opportunities (ranked, real value only)

| # | Feature | Impact | Effort | Risk | Files |
|---|---|---|---|---|---|
| F1 | Conversation branching tree (parent_uuid already parsed) | High | M | Med | index.py, api.py, web/app.js |
| F2 | Skill / plugin / sub-agent analytics (already parsed) | High | S | Low | index.py, analytics.py |
| F3 | Multi-field FTS + bm25 column weights (body+title+project) | High | M | Med | index.py, api.py |
| F4 | Bookmarks + annotations on messages | High | S | Low | index.py, api.py, server.py, app.js |
| F5 | Session similarity (shared files + tool profile cosine, no embeddings) | Med-High | M | Low | similarity.py(new), api.py |
| F6 | Indexed `tool_paths` table -> instant file-history | Med | M | Med | index.py, ask.py, api.py |
| F7 | Recency-blended ranking option in search | Med | S | Low | api.py |
| F8 | Search facets (counts by kind/project/model) | Med | M | Low | api.py, app.js |
| F9 | Transcript diff view between two sessions | Med | M | Low | api.py/export.py, app.js |

Deliberately NOT recommended: semantic/embedding search (breaks zero-dep /
local-first unless a heavy local model is added); any cloud/telemetry.

## Roadmap

**Quick wins (1-2 days)** — D1 read-only conn for reads; D3 collapse reopen N+1 +
`_cite` reuse; D5 `idx_msg_model`; F2 persist already-parsed fields; HTTP smoke +
perf guard in selftest.

**Medium (1-2 weeks)** — F3 multi-field weighted FTS (schema bump); D2 migration
runner; F1 branching view; F4 bookmarks/annotations; F6+D4 `tool_paths` table.

**Major (multi-week)** — F5 similarity/clustering; F8/F9 facets + transcript diff;
external-content FTS (D6) to cut DB ~30%; streaming parse for multi-MB sessions.

## Next 5 (if lead maintainer)

1. Persist what you already parse (F2 + F1 groundwork) — highest value-to-effort.
2. Migration runner (D2) — unlocks every schema change safely.
3. Multi-field weighted FTS (F3) — lifts the product core (search relevance).
4. Scale & hygiene sprint: D1 + D3 + D5 + D7 — remove every measured inefficiency
   before a 10k-session corpus exposes it.
5. Bookmarks + annotations (F4) — turns a viewer into a tool people return to.

Competitive read: vs Claude Code (no native history explorer), Cursor history
(ephemeral, no analytics), OSS extractors (dump-to-markdown, no search/replay/cost)
— ClaudeStudio already wins on local-first + grounded Ask + deterministic cost +
FTS replay. Unbuilt moat: branching/sub-agent viz, skill analytics,
bookmarks/annotations, similarity — all local, zero-dep, from data it already has.
