# ClaudeStudio HTTP API Reference

ClaudeStudio's desktop UI is a thin client over a small, local HTTP+JSON API
served by `claudestudio serve`. The same API is yours to build on — scripts,
dashboards, editor integrations.

- **Base URL:** `http://127.0.0.1:8787` (the server picks the next free port if
  8787 is taken; watch the `serve` banner for the actual URL).
- **Auth:** none — the server is single-user and binds to loopback only.
- **Rate limiting:** none — it's a local process.
- **CSRF / host hardening:** state-changing requests (`POST`/`DELETE`) are
  rejected if they carry a cross-site `Sec-Fetch-Site` or a non-loopback
  `Origin`, and every request's `Host` must resolve to loopback (DNS-rebinding
  defence). Same-origin app traffic and non-browser clients (curl, scripts) are
  unaffected.
- **Errors:** failures return a JSON body `{"error": "..."}` with an appropriate
  status (`404` not found, `500` on an unexpected error) — never a raw traceback.
- **Versioning:** the API has been stable since v0.4.0. New endpoints are
  additive; existing shapes don't break. (This document covers v0.5.2.)

All responses are `application/json; charset=utf-8` unless noted (CSV, HTML,
Markdown and ZIP exports set their own content type + `Content-Disposition`).

---

## Sessions & search

### `GET /api/summary`
Index-wide totals: sessions, messages, tool calls, tokens, cost, projects,
`indexed_at`.

### `GET /api/sessions`
The session list. Query params: `q`, `project`, `model`, `root`, `sort`
(`recent|oldest|messages|tools|cost|tokens|duration|title|health`), `order`,
`favorite=1`, `archived` (`exclude|only|all`), `since`, `until` (epoch or
`YYYY-MM-DD`), `limit` (≤500), `offset`. Returns `{sessions: [...], total,
limit, offset}`. Each session row includes `health_score` *(v0.5.2)*.

### `GET /api/session/{id}`
Full session detail: metadata, token/cost totals, user state, and a `timeline`
of messages (each with its tool calls, inline `diff` for edits, and inter-message
`gap_s`). Also includes `health` (the breakdown, *v0.5.2*) and `git` (the git
context badge, may be `null`, *v0.5.2*). `404` if unknown.

### `GET /api/session/{id}/similar?limit=5`
Sessions most similar to this one by prompt content (TF-IDF cosine).

### `GET /api/session/{id}/diffs?file=NAME` *(v0.5.2)*
Every inline file diff in the session, optionally filtered to one file (by
basename). Returns `{session_id, file_path, diffs: [{seq, tool, file, diff,
truncated}]}`.

### `GET /api/search`
Full-text search (FTS5/BM25). Params: `q` (required), `kind`
(`user|assistant|tool`), `project`, `session`, `root`, `since`, `until`, `limit`.
Returns `{query, results: [...], filters}`.

---

## Analytics

### `GET /api/analytics`
Overview: totals, per-model spend, per-tool counts, daily activity, weekday×hour
heatmap, top projects.

### `GET /api/analytics/efficiency` *(v0.5.2)*
Effectiveness metrics, computed from existing tables (no new storage):

```json
{
  "overall": {
    "output_tokens_per_dollar": 41233.0,
    "tool_success_rate": 0.94,
    "avg_messages_per_session": 38.2,
    "median_session_duration_s": 612
  },
  "by_project": [
    {"project": "orbit-api", "sessions": 23, "cost_usd": 14.2,
     "tool_success_rate": 0.96, "output_per_dollar": 50211.0, "efficiency_rank": 1}
  ],
  "trend": [{"week": "2026-W24", "tool_success_rate": 0.95, "output_per_dollar": 48120.0}]
}
```

`by_project` is sorted by `efficiency_rank` (1 = best); `trend` holds up to the
last 12 ISO weeks present in the data.

### `GET /api/analytics.csv` · `GET /api/sessions.csv`
Spreadsheet-ready CSV exports.

### `GET /api/projects` · `GET /api/tools/stats` · `GET /api/tools/latency` · `GET /api/graph` · `GET /api/highlights`
Project aggregates, tool-usage intelligence, per-tool latency, the
session×project×file knowledge graph, and smart highlights.

### `GET /api/wrapped?year=YYYY` · `GET /api/compare?a=ID&b=ID` · `GET /api/report[.html|.md|.json]`
Year-in-review cards, a two-session comparison, and a shareable activity report.

---

## Budget *(v0.5.2)*

### `GET /api/budget`
Current spend vs the active budget. Always a stable shape:

```json
{"has_budget": true, "period": "monthly", "ceiling_usd": 50.0, "spent_usd": 31.4,
 "percent": 62.8, "remaining_usd": 18.6, "sessions_this_period": 27,
 "days_remaining": 11, "alert": false}
```

### `POST /api/budget`
Body `{"period": "monthly"|"weekly", "ceiling_usd": 50}`. Replaces the active
budget. Returns `{period, ceiling_usd}`.

### `DELETE /api/budget`
Removes the budget. Returns `{cleared: bool}`.

---

## Annotations *(v0.5.2)*

### `GET /api/session/{id}/annotations`
`{session_id, annotations: [{id, session_id, message_idx, note, created_at,
updated_at}]}`. `message_idx = -1` is the session-level note.

### `POST /api/session/{id}/annotations`
Body `{"message_idx": int, "note": str}`. Upserts the note for that target (one
note per `(session, message_idx)`); the FTS shadow is updated in the same
transaction. Returns the stored row.

### `DELETE /api/session/{id}/annotations/{annotation_id}`
Returns `{deleted: bool, id}`.

### `GET /api/annotations/search?q=...&limit=50`
Full-text search over annotation notes. Returns `{query, results: [{id,
session_id, message_idx, note, session_title}]}`.

---

## Prompt library *(v0.5.2)*

### `GET /api/prompts?q=...&starred=1&limit=200`
`{prompts: [{id, text, source, frequency, starred, created_at}]}`, ordered
starred-first then by frequency.

### `POST /api/prompts`
Body `{text, source?, frequency?, starred?, id?}`. Adds or (with `id`) updates a
prompt. Returns the stored row.

### `POST /api/prompts/extract`
Body `{top_n?, min_count?}`. Extracts reusable prompt patterns from history and
upserts them (stable ids, idempotent). Returns `{extracted, total}`.

### `DELETE /api/prompts/{id}`
Returns `{deleted: bool, id}`.

---

## CLAUDE.md *(v0.5.2)*

### `GET /api/project/{project_id}/claude-md`
`project_id` is the project path or short name (URL-encode it). Returns
`{markdown, profile}` — a paste-ready `CLAUDE.md` plus the structured project
profile it was rendered from.

---

## Export

### `GET /api/session/{id}/export[.md|.html|.json]`
A single session as Markdown, standalone HTML, or JSON
(`Content-Disposition: attachment`).

### `POST /api/export/batch` *(v0.5.2)*
Body `{"session_ids": [str], "format": "md"|"html"|"json", "include_index":
bool}`. Streams an `application/zip` archive: one file per session plus a
generated `index.md` table of contents.

---

## State, bookmarks, saved searches

### `POST /api/state/{id}`
Patch user state: `{favorite?, archived?, tags?, notes?}`.

### `GET /api/bookmarks?session=ID` · `POST /api/session/{id}/bookmark` · `DELETE /api/bookmark/{id}`
Per-message bookmarks.

### `GET /api/saved` · `POST /api/saved` · `DELETE /api/saved/{id}`
Saved searches / smart collections.

---

## Live & misc

### `GET /api/events`
A Server-Sent-Events stream. Emits `data: {"type":"reindex","ts":<epoch>}` when
the index file changes, plus `: keepalive` comment frames. `text/event-stream`.

### `POST /api/reindex`
Body `{"force": bool}`. Re-scans the configured projects root(s). Returns the
reindex stats.

### `GET /api/ask?q=...&session=ID`
Grounded, local Q&A over your history (no model calls). Returns the structured
answer plus suggestions.

## v0.6.0

### `GET /api/cross-refs?limit=N`
Prompts that reference an earlier session ("as we did last time", "like in the
refactor session"). Returns `{"cross_refs": [{session_id, message_index,
matched_phrase, text, candidate_sessions:[{session_id,title,last_ts,score}]}]}`.
`limit` defaults to 200 (clamped 1–2000). Empty index → `{"cross_refs": []}`.

### `GET /api/patterns/workflows?min_count=N`
Top recurring tool-sequence workflows. `{"workflows":[{steps:[...],count,sessions,length}]}`.

### `GET /api/patterns/debug-loops?min_repeat=N`
Runs where one tool fired N+ times in a row, plus most-productive hours.
`{"debug_loops":[{session_id,tool,length}], "time_of_day":[{hour,label,sessions,emoji}]}`.

### `GET /api/patterns/momentum?weeks=N`
Per-project session frequency, recent vs. prior half of the window.
`{"momentum":[{project,project_name,recent,older,total,momentum}]}` where
`momentum` is `rising` | `stalling` | `steady`.

### `GET /api/session/{id}/github-refs`
GitHub issue/PR references detected in one session.
`{"session_id": id, "refs": [{seq,ref,owner,repo,number,kind,url}]}`. `kind` is
`issue` | `pr`. (These also ride along on `GET /api/session/{id}` as `github_refs`.)

### `GET /api/github-refs/search?q=...&repo=owner/repo`
Find sessions that discussed a given issue/PR. `q` accepts `#123`, `owner/repo#456`
or a number; `repo` an optional `owner/repo` filter. Returns
`{"query","repo","number","results":[{session_id,seq,ref,owner,repo,number,kind,url,session_title,last_epoch}]}`.

### `GET /api/prompts/{id}/effectiveness`
Aggregate effectiveness (0–100) for a library prompt across its historical
occurrences. `{"id","text","count","avg_score","samples":[...]}`. Unknown id →
`{"error":"not found","count":0,"avg_score":null}` (200).

### `GET /api/feed.rss` · `GET /api/feed.atom`
Valid RSS 2.0 / Atom 1.0 feeds of recent sessions. Honour `?project=`,
`?since=YYYY-MM-DD`, `?limit=N` (1–200, default 25). `application/rss+xml` /
`application/atom+xml`.

### `GET /api/dev/selftest`
**Dev-only.** Runs `python -m claudestudio --selftest` and streams the output as
Server-Sent Events: `{"type":"line","text":...}` per line, then
`{"type":"done","code":N}`. `text/event-stream`. Backs the hidden Developer view.

---

## v0.6.1 — tags, narrative, heatmap, digest, benchmark, preferences, share

### `GET /api/tags`
All user-defined tags with live session counts.
`{"tags":[{id,name,colour,created_at,session_count}]}`.
```bash
curl -s http://127.0.0.1:8787/api/tags
```

### `POST /api/tags`
Create a tag. Body `{"name":"bug-fix","colour":"#ff8a5b"}` (colour optional,
default `#9a8cff`). Names are normalised (lowercase, ≤32 chars, `[a-z0-9_-]`).
Returns the tag row. An empty/invalid name → `400 {"error":...}`. Idempotent by
normalised name.

### `DELETE /api/tags/{tag_id}`
Delete a tag and detach it from every session. `{"deleted":true,"id":...}`.

### `GET /api/tags/{tag_id}/sessions`
Sessions carrying one tag, most recent first. Paginated: `?limit=` (1–500,
default 60), `?offset=`. `{"tag_id","sessions":[…],"limit","offset"}`.

### `GET /api/session/{id}/tags`
Tags applied to one session. `{"session_id","tags":[{id,name,colour,created_at}]}`.

### `POST /api/session/{id}/tags`
Apply a tag to a session. Body `{"tag_id":"…"}`. Missing `tag_id` → `400`.
`{"session_id","tag_id","applied":true}`.

### `DELETE /api/session/{id}/tags/{tag_id}`
Remove one tag from one session. `{"session_id","tag_id","removed":true}`.

### `GET /api/session/{id}/narrative`
Deterministic one-paragraph narrative of a session (no model calls).
`{"headline","goal","approach","outcome","files_changed":[…],"errors_encountered",
"recovery","next_steps","quality","word_count","session_id"}`. Unknown id → `404`.
```bash
curl -s http://127.0.0.1:8787/api/session/<id>/narrative
```

### `GET /api/files/heatmap`
File × metric heatmap over the corpus. Filters: `?project=`, `?since=YYYY-MM-DD`,
`?until=YYYY-MM-DD`. Returns
`{"files":[{path,edit_count,session_count,last_touched,total_tokens,projects,weeks,
heat_score}],"total_files","date_range":{from,to}}`. An empty corpus is safe.

### `GET /api/files/heatmap.svg`
The same data rendered as a standalone SVG (top 20 files × last 12 ISO weeks).
`image/svg+xml`; `<svg role="img" aria-label="…">`, cells carry `data-tip`.

### `GET /api/digest`
Standup-ready digest for a calendar date. `?date=YYYY-MM-DD` (default today),
`?project=`. Returns `{"date","session_count","total_tokens","total_cost_usd",
"top_project","files_touched":[…],"tools_used":{…},"highlights":[…],
"session_summaries":[…],"markdown"}`. An empty day is safe.

### `GET /api/digest.md`
The digest's pre-rendered Markdown block as `text/markdown`.

### `GET /api/benchmark`
Current vs previous period efficiency comparison. `?mode=week|month|quarter`
(default week). Returns `{"mode","current","previous","delta","trend","verdict",
"highlights"}`. Metrics include `output_per_dollar`, `tool_success_rate`,
`avg_health_score`.

### `GET /api/preferences`
Stored cross-device UI preferences merged over defaults. `{"theme":"dark",…}`.

### `POST /api/preferences`
Persist preferences. Body `{"theme":"light"}` (one of `dark`, `light`, `system`,
`high-contrast`). `204 No Content` on success; an invalid theme → `400 {"error":…}`.

### `GET /api/session/{id}/share.html`
A fully self-contained share pack: one HTML file with the session inlined,
read-only replay, no server, no network. `?annotations=0` omits personal notes.
`text/html` (attachment). Unknown id → `404`.

---

## Notes for builders

- Prefer the **MCP server** (`claudestudio mcp`) if you're integrating with
  Claude Code itself — see [MCP.md](MCP.md). The HTTP API is for everything else.
- The **parser** is a documented public API:
  `from claudestudio import parse_session` (see [FORMAT.md](FORMAT.md)). Import it
  instead of re-implementing the wire format.
- Everything is **deterministic and local**. No endpoint calls a model or the
  network; all numbers are reproducible from the index.
