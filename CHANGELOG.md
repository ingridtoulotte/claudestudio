# Changelog

All notable changes to ClaudeStudio are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/ingridtoulotte/claudestudio/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ingridtoulotte/claudestudio/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ingridtoulotte/claudestudio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ingridtoulotte/claudestudio/releases/tag/v0.1.0
