# Changelog

All notable changes to ClaudeStudio are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.2] - 2026-06-25

The "ambient intelligence" release. ClaudeStudio now reads *meaning* out of your
history, not just metadata: it scores how productive each session was, tracks
your spend against a budget, generates a `CLAUDE.md` from how you actually work,
lets you annotate sessions into a personal knowledge base, and surfaces a
reusable prompt library — all still local-first, zero-dependency, and covered by
an expanded self-test (301 → 396 checks). The MCP server grows 10 → 14 tools.

### Added
- **Session health score (`health.py`).** Every session gets a deterministic
  0–100 health score + letter grade, a weighted blend of tool-success rate,
  error density, output/input token ratio, and a completion signal (did it end
  on a clean wrap-up, mid-tool-call, or an unanswered prompt?). Cached on a new
  `sessions.health_score` column at index time, surfaced as a coloured A–F dot in
  the session list, a breakdown card in the detail view, and a new `list --sort
  health` ordering.
- **Budget tracker & spend alerts (`budget.py`, `claudestudio budget`,
  `GET/POST/DELETE /api/budget`).** Set a `$/month` or `$/week` ceiling; the app
  tracks cumulative spend for the current calendar period from local logs,
  renders a pure-SVG radial progress arc in the Efficiency view, and raises a
  sticky, dismissible banner when you cross 75%. Zero model calls.
- **CLAUDE.md generator (`generate_claude_md.py`, `claudestudio
  generate-claude-md`, `GET /api/project/{id}/claude-md`).** Analyses a project's
  indexed history and renders a ready-to-paste `CLAUDE.md` — top tools, most
  -touched files, inferred stack, recurring pitfalls and prompt intents. Backs
  the new MCP `generate_project_brief` tool, so a brief and a CLAUDE.md agree.
- **Session annotations (`annotations` table + `annotations_fts`,
  `GET/POST/DELETE /api/session/{id}/annotations`, `GET /api/annotations/search`).**
  Attach personal notes to a whole session or an individual message. Notes live
  in their own table (survive reindexing), are full-text searchable via FTS5, and
  are exposed to Claude Code through the new MCP `get_annotations` tool.
- **Token efficiency dashboard (`GET /api/analytics/efficiency`).** A new
  Efficiency view: output-tokens-per-dollar, tool-success rate, messages per
  session, median duration, a per-project efficiency ranking (pure-SVG bars), and
  a 12-week trend sparkline. Computed entirely from existing tables — no new
  storage.
- **Git context (`git_context.py`).** For a session whose project is a git repo,
  ClaudeStudio resolves the commit that was `HEAD` at the session's time (read
  -only `git log`, cached, never raises) and shows a `🔀 branch @ sha` badge in
  the detail view (click to copy). Exposed on `GET /api/session/{id}`.
- **Prompt library (`prompt_library.py`, `prompt_library` table,
  `GET/POST/DELETE /api/prompts`, `POST /api/prompts/extract`).** Auto-extracts
  your most reusable prompt patterns (trigram clustering + a reusability
  heuristic that penalises one-off references), plus manual add/star/search. A
  new Prompts view in the sidebar.
- **Batch export + archive (`POST /api/export/batch`, `claudestudio export --all
  --zip`).** Bundle many sessions into a single ZIP (stdlib `zipfile`) of
  individual Markdown/HTML files plus a generated `index.md` table of contents.
- **Four new MCP tools (10 → 14):** `get_cost_by_period`, `get_diff_for_session`,
  `get_annotations`, `generate_project_brief`. See [`docs/MCP.md`](docs/MCP.md).
- **Keyboard navigation system (`web/keyboard.js`).** A `KeyboardNavigator` that
  maps keys to high-level `cs:navigate`/`cs:action` intents (decoupled from view
  code) and a `?` cheat-sheet overlay; preferences stored in `localStorage`.
- **New full HTTP API reference: [`docs/API.md`](docs/API.md).**

### Changed
- Schema **v2 → v3**: adds the `sessions.health_score` column and the `budgets`,
  `annotations` (+ `annotations_fts`) and `prompt_library` tables. Migration is
  in-place and idempotent; no indexed data or user state is lost.
- `Development Status` classifier promoted to **5 - Production/Stable**.
- `claudestudio watch` accepts `--poll-interval` as an alias for `--interval`.
- Self-test grew **301 → 396** checks; MCP now exposes **14** tools.

### Fixed
- N/A — additive release; all prior behaviour and existing tests are preserved.

## [0.5.1] - 2026-06-25

The "make it part of your daily loop" release. ClaudeStudio now keeps itself
current (Claude Code hooks + live watch), lets you mark and revisit individual
messages, renders real diffs of every edit, and ships a one-click activity
report — all still local-first, zero-dependency, and covered by an expanded
self-test (209 → 301 checks).

### Added
- **Live watch mode + SSE push (`claudestudio watch`, `GET /api/events`).** The
  open app no longer goes stale: a Server-Sent-Events stream notifies the SPA the
  moment the index changes, showing a one-click "new sessions available" toast
  that refreshes in place — no full reload. `claudestudio watch` is a foreground
  poller that reindexes whenever a `.jsonl` changes. Polling, not inotify, so it
  behaves identically on every OS.
- **Message bookmarks (`POST /api/session/{id}/bookmark`, `GET /api/bookmarks`,
  `DELETE /api/bookmark/{id}`).** Star a *specific* message, not just a whole
  session. Bookmarks live in their own table (never wiped by reindexing), get a
  global Bookmarks view in the sidebar, and deep-link straight back to the exact
  session + message. Also exposed to Claude Code as the new MCP tool
  `list_bookmarks`.
- **Inline unified diffs in the replay view.** Every `Edit`/`Write`/`MultiEdit`
  tool call now carries a `diff` field (stdlib `difflib`, capped at 200 lines)
  rendered as a syntax-highlighted, XSS-safe diff with a Diff/Raw toggle.
- **Activity report (`claudestudio report`, `GET /api/report.html|.json`).** A
  shareable, self-contained HTML (or Markdown) summary of any week or month:
  hero stats, top projects & tools, an ASCII activity chart, and notable sessions
  — print-optimized with a Save-as-PDF button. Defaults to the current week.
- **Claude Code hook integration (`claudestudio hook install|status|uninstall`).**
  One command wires `claudestudio index` to Claude Code's `SessionEnd` event in
  `~/.claude/settings.json`, so the index refreshes itself after every session.
  Merges cleanly (never clobbers existing hooks), idempotent, fully reversible.
  Guide in [`docs/HOOK.md`](docs/HOOK.md); `doctor` now reports hook status.
- **Per-tool latency analytics (`GET /api/tools/latency`).** p50/p95/p99/max/mean
  latency per tool, derived from message timestamps, shown as color-banded bars in
  the Tools dashboard.
- **Multi-root support.** One index can span several projects roots (work laptop,
  personal machine, remote). `--root` accepts several paths separated by the
  platform path separator; `list`/`search` gain a `?root=` filter; `doctor`/`info`
  report per-root counts. Schema migrated to v2 in place (no data loss).
- **Prompt pattern extraction (`GET /api/prompts/patterns`).** Clusters your
  near-identical prompts (trigram Jaccard) into a personal prompt library — the
  things you ask Claude again and again — with copy buttons. New MCP tool
  `get_prompt_patterns`.
- **Export enhancements.** Print-optimized CSS (`@media print`) + a Save-as-PDF
  button in HTML exports; CSV exports (`GET /api/analytics.csv`,
  `GET /api/sessions.csv`); and `claudestudio export --all` batch mode with
  progress and skip-existing.
- **`claudestudio info` + `--version`.** `--version`/`-V` prints `claudestudio
  0.5.1`; `info` prints a full environment summary (version, Python, platform,
  index path/size, session count, FTS5 + hook status, roots, MCP snippet) for bug
  reports.
- **Keyboard navigation & accessibility pass.** Landmark roles
  (`navigation`/`main`/`complementary`), `aria-label`s on icon buttons, a
  `prefers-reduced-motion` media query, Space = play/pause and `←`/`→` = step in
  the replay (guarded against firing while typing), and `B` to bookmark.
- **Community infrastructure.** `CITATION.cff`, a tag-triggered release workflow
  (`.github/workflows/release.yml`) that builds the wheel and gates on the
  self-test, and a Discussions contact link.

### Changed
- Self-test grows from **209 to 301** exact-assertion checks; every new module
  (`hook`, `report`, `patterns`) and endpoint has fixture-based coverage.
- MCP server now exposes **10** tools (was 8).
- `pyproject.toml`: new keywords (`bookmarks`, `hooks`, `live-watch`,
  `diff-view`, `report`) and `Documentation` / `Changelog` project URLs.
- The `export` command's `session_id` is now optional (omit it with `--all`).

### Fixed
- The index schema is versioned and migrated forward to v2 automatically;
  opening a newer-schema index still fails loudly rather than returning wrong data.

## [0.5.0] - 2026-06-24

ClaudeStudio becomes a first-class part of your Claude Code toolchain: it can now
be queried by Claude Code itself over MCP, and it surfaces deeper, deterministic
intelligence about how you actually work — all still local-first, zero-dependency,
and covered by an expanded self-test (161 → 209 checks).

### Added
- **MCP server (`claudestudio mcp`).** A JSON-RPC 2.0 server over stdio that
  exposes your indexed history to any MCP client, Claude Code included. Eight
  read-only tools — `search_sessions`, `get_session`, `get_session_annotations`,
  `get_project_stats`, `get_analytics_summary`, `find_sessions_by_file`,
  `get_recent_sessions`, `ask_history` — each reusing the existing query layer.
  No new dependencies, no model or network calls. New `claudestudio-mcp` entry
  point and a setup guide in [`docs/MCP.md`](docs/MCP.md).
- **Tool-usage intelligence (`GET /api/tools/stats`).** A tool leaderboard with
  per-tool success rates, tool×project breakdown, most-edited files, and a
  tool co-occurrence matrix — all computed from the existing index.
- **Smart Highlights engine (`GET /api/highlights`, `claudestudio highlights`).**
  Deterministic heuristics surface breakthrough moments (error chain → clean
  result), cost spikes, marathon sessions, most-revisited files, recurring prompts
  (trigram Jaccard), abandoned sessions, and model-migration days.
- **Knowledge graph (`GET /api/graph`).** Nodes (sessions, projects, files) and
  edges (belongs-to, touched) for a connected view of your work, bounded and
  filterable by project.
- **Session similarity (`GET /api/session/{id}/similar`).** Finds related sessions
  by prompt content using TF-IDF cosine similarity, computed entirely in stdlib.
- **`find_sessions_by_file`** lookup: which sessions touched a given file.
- **JSON export** (`claudestudio export <id> --format json`) for piping a full
  session into other tooling.
- **`cs` short alias** for the `claudestudio` command.
- **`doctor`** now reports the MCP tool count and the on-disk schema version.

### Fixed
- **`index.connect` no longer leaks an open SQLite handle** when it rejects a
  newer-schema index. The connection is closed before the error propagates, so the
  index file can be removed or rebuilt afterwards (previously the dangling handle
  blocked deletion on Windows).

### Changed
- `pyproject.toml`: version `0.5.0`, expanded keywords/classifiers, and the new
  `cs` / `claudestudio-mcp` console scripts.
- Self-test expanded from 161 to 209 assertions, covering every new subsystem
  (tool stats, graph, similarity, highlights, JSON export, and full MCP dispatch).

## [0.4.1] - 2026-06-24

### Security
- **Hardened request-body parsing against a malformed `Content-Length`.** The
  POST/DELETE handlers drain the request body *before* the security gates, so a
  non-numeric `Content-Length` crashed `int()` and reset the socket instead of
  returning a clean response, and a negative value sent `rfile.read(-1)` to EOF —
  hanging the worker on a keep-alive connection (a DoS vector). The parser now
  treats any non-positive or non-numeric length as "no body" and closes the
  connection (the framing is untrustworthy), so a hostile or buggy client gets a
  clean HTTP response and the server never crashes or hangs. Added regression tests.
- **Hardened static-file path containment.** The web server's directory-traversal
  guard now folds path separators before normalising (so a backslash segment
  can't survive `normpath` on POSIX and reconstitute a `../`) and checks
  containment with `realpath` + `commonpath` instead of a string `startswith`
  (so a sibling whose name shares a prefix — e.g. `web` vs `web_secrets` — is no
  longer treated as inside the web root). Added regression tests.

### Added
- **Date-range filtering on the session list.** The Sessions view, the
  `/api/sessions` endpoint, and `claudestudio list` now accept `since`/`until`
  bounds (epoch or `YYYY-MM-DD`), matching the date filter `search` already had.
  Overlap semantics: `since` keeps sessions still active on/after the bound,
  `until` keeps sessions started on/before it. Backward-compatible — both
  optional; unparseable values are ignored, not fatal.

### Fixed
- **`until` date bound now includes the whole selected day.** A bare `until`
  date (e.g. from the UI date picker) resolved to that day's *midnight*, so
  `list_sessions`/`search` dropped everything that happened after 00:00 — picking
  today as the end date returned nothing for today's sessions. The upper bound now
  stretches to the day's last instant. `since` was already correct (midnight keeps
  the whole start day); raw epochs and values with an explicit time are unchanged.
- **Time-bucketed views no longer crash on an out-of-range epoch.** A session
  whose timestamp parses to a far-future instant (valid ISO-8601 up to year 9999,
  or a corrupt millisecond value read as seconds) fed `datetime.fromtimestamp`
  an epoch past its range — raising `OSError` on Windows and silently bucketing
  into year 9999 on POSIX. The daily chart, hour/weekday heatmap, Wrapped, and
  `available_years` now route every epoch through a shared bounded helper that
  drops the corrupt row identically on every OS; SQL totals are unaffected.
- **Date-range filter bounds tolerate unrepresentable dates.** `?since=1900-01-01`
  (pre-epoch) or a far-future bound made `_as_epoch`'s `.timestamp()` raise
  `OSError`/`OverflowError` on Windows, escaping as an HTTP 500 with a leaked
  Python message. An unrepresentable bound is now treated as "not applied"
  (returns `None`) on every platform.
- **`wrapped` falls back to all-time for an unrepresentable year.** `?year=9999`
  (or `claudestudio wrapped --year 9999`) overflowed the calendar math —
  `OSError` on Windows, `ValueError` past `datetime.MAXYEAR` — surfacing as an
  HTTP 500 or a raw CLI traceback. An out-of-range year now resolves to the
  all-time view, matching the existing `?year=abc` behaviour.
- **Pagination params guarded against malformed input.** `?limit=abc` / empty
  `?limit=` raised `ValueError` → HTTP 500, and `?limit=-1` reached SQLite as an
  *unbounded* `LIMIT` (cap bypass, full-table dump). `limit`/`offset`/`year` now
  pass through a single coercer that falls back to the default on bad input and
  clamps the value to a safe range.
- **`ask` no longer crashes on a blank tool command.** A `Bash`/`PowerShell`
  tool call with an empty or whitespace-only `command` made `important_tools`
  index an empty `splitlines()`, raising `IndexError` and 500-ing `/api/ask`.
  The empty first line is now guarded and skipped.

## [0.4.0] - 2026-06-21

### Security
- **Host-header validation.** The local server now rejects any request whose
  `Host` header is not its own loopback interface (or an explicitly chosen
  `--host`), returning `421`. This closes the DNS-rebinding / localhost-CSRF
  surface where a malicious page resolves its name to `127.0.0.1`.
- **Cross-site write protection.** State-mutating endpoints (favorites, archive,
  tags, saved searches, reindex) now require `Sec-Fetch-Site: same-origin` and
  reject a cross-origin `Origin`, so a third-party page can't change your state.
- **Security response headers** on every response: a strict
  `Content-Security-Policy`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.
- **`--host` warning.** Binding to a non-loopback host prints a clear warning to
  stderr — it exposes your session history to the network. The default is
  unchanged (`127.0.0.1`).
- **Contained exports.** `export --out` paths are resolved (collapsing `..`)
  before writing.

### Added
- **Terminal-first workflows.** New `list`, `search`, and `ask` CLI commands put
  the core workflows in the terminal without the UI. All three support `--json`
  for scripting and print full, copy-pasteable session ids (feed one straight to
  `export`). `search` accepts `--kind`, `--project`, `--session`, `--since`, and
  `--until` filters; `list` accepts `--query`, `--project`, `--model`, `--sort`,
  `--favorite`, and `--archived`.
- **Search filters & stable ranking.** `/api/search` (and the CLI) now filter by
  message kind, project, single-session scope, and message time window — all
  local, all expressible from the query string. Results are BM25-ranked with a
  deterministic tiebreak, so the same query always returns the same order.
- **Replay you can drive.** The session replay bar gained **restart**, **step
  back**, and **step forward** controls, and the message thread now highlights
  the **current** turn during replay so the active position is always obvious.
- **Schema versioning & forward migration.** The SQLite index records its schema
  version; opening one written by a newer build now fails with a clear, actionable
  error instead of returning wrong numbers, and there is a place for ordered
  forward migrations that preserve your favorites/tags/notes.
- **PyPI publishing workflow.** A Trusted-Publishing (OIDC) GitHub Actions
  workflow builds and uploads the wheel + sdist (no stored tokens), enabling
  `pip install claudestudio` once the publisher is configured.
- **pytest test suite** (`tests/`): parser-free unit + server-integration tests
  for the index/migration, pricing, API, export-path guard, and the new security
  gates — run with `pip install -e ".[dev]" && pytest`. The zero-dependency
  `--selftest` remains the canonical gate.
- **`py.typed` marker** (PEP 561) so downstream type checkers trust the public
  parser/pricing hints.
- **Pricing staleness signal.** `pricing.py` carries a `PRICE_TABLE_DATE`;
  `doctor` flags — and the self-test guards — a table older than its max age.

### Changed
- Self-test grew to **148** exact-assertion checks (schema migration + pricing
  staleness covered) on top of search filters, deterministic ordering, and the
  CLI commands.
- CI gained `ruff` + `mypy` and a cross-platform `pytest` job alongside the
  existing zero-dependency self-test matrix; `pyproject.toml` now carries
  `[project.optional-dependencies] dev`, `[tool.ruff]`, `[tool.mypy]`, and
  `[tool.pytest.ini_options]`.

### Fixed
- The server now drains a rejected request's body before responding, so a blocked
  cross-site `POST`/`DELETE` returns a clean `403` instead of resetting the socket.

## [0.3.0] - 2026-06-20

### Added
- **Ask — a grounded, local companion for your history.** A new top-level view
  that answers natural-language questions about your Claude Code sessions:
  *"what should I reopen next?"*, *"give me a handoff brief"*, *"which files
  changed?"*, *"why was `X` edited?"*, *"where did the tokens go?"* Answers are
  **computed** from the local index with deterministic rules — **no model calls,
  no network, nothing uploaded** — and every answer cites the exact sessions and
  messages it drew from, deep-linking straight into them.
- **Per-session intelligence.** Each session detail now shows an at-a-glance
  **brief** (files touched, tools used, error count) and an **✦ Ask about this**
  button that scopes Ask to that session (digest, handoff brief, most-important
  tool calls).
- **Deep-linking into replay.** Search results and Ask citations now jump to the
  exact message and spotlight it, instead of dropping you at the top.
- **First-run onboarding.** An empty index now shows a clear, local-first
  getting-started card (Sync / `claudestudio demo`) instead of a bare message.
- Public, dependency-free engine module `claudestudio.ask` (covered by the
  self-test) so the same grounded reports are scriptable.

### Changed
- Self-test grew to **96** exact-assertion checks (Ask engine + routing covered).

## [0.2.0] - 2026-06-20

### Added
- **Run in one command** with `pipx run` — no clone, no install:
  `pipx run --spec git+https://github.com/ingridtoulotte/claudestudio claudestudio`.
  The web package bundles a built wheel so the throwaway environment launches fast.
- **Export a session** to clean **Markdown** or a single self-contained **HTML**
  page (inline styles, no scripts, no network) — from the session view or
  `python -m claudestudio export <id> --format md|html`.
- **Saved searches & smart collections** — name any filter (query, sort,
  favorites, project) and jump back to it; collections survive every re-index.
- **Claude Wrapped → PNG card** — save a shareable image of your year / all-time
  summary.
- **Public parser API** —
  `from claudestudio import parse_session, iter_session_files, default_projects_root`.
  A dependency-free reference for the Claude Code session wire format, covered by
  the self-test.
- **`docs/FORMAT.md`** — full documentation of the `.jsonl` wire format: record
  types, content blocks, usage/cost, and every dataclass field.
- Project trust docs: `SECURITY.md`, `CODE_OF_CONDUCT.md`, this changelog, and
  issue / pull-request templates.

### Changed
- Self-test grew to **70** exact-assertion checks.
- README: added "Who it's for", a trust-badge row, and the builders / parser
  section.

## [0.1.0] - 2026-06-19

### Added
- First public release.
- Browse, sort, filter, favorite, and archive every Claude Code session.
- Replay any session chronologically on a scrubable timeline.
- Full-text search (SQLite **FTS5**, BM25) across prompts, responses, thinking
  blocks, and tool calls.
- Usage & cost analytics with a deterministic, cache-aware estimate at public
  Anthropic prices; unpriced models are flagged, never guessed.
- Project, Timeline, and Compare views.
- Claude Wrapped summary.
- Synthetic `demo` mode and a built-in `--selftest`.
- Cross-platform CI (3 operating systems x 3 Python versions).

[Unreleased]: https://github.com/ingridtoulotte/claudestudio/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/ingridtoulotte/claudestudio/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ingridtoulotte/claudestudio/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ingridtoulotte/claudestudio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ingridtoulotte/claudestudio/releases/tag/v0.1.0
