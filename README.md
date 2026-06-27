<div align="center">

<img src="docs/hero.png" alt="ClaudeStudio — the desktop app Claude Code deserves" width="100%" />

<h1>ClaudeStudio</h1>

**The desktop app Claude Code deserves.**
Explore, search, replay, and understand every Claude Code session — all on your machine.

[![CI](https://github.com/ingridtoulotte/claudestudio/actions/workflows/ci.yml/badge.svg)](https://github.com/ingridtoulotte/claudestudio/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-3776ab)
![Dependencies](https://img.shields.io/badge/dependencies-zero-5ec98a)
![Local-first](https://img.shields.io/badge/data-100%25%20local-ff8a5b)
![Platforms](https://img.shields.io/badge/platform-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-9a8cff)
![License](https://img.shields.io/badge/license-MIT-blue)
[![Changelog](https://img.shields.io/badge/changelog-read-9a8cff)](CHANGELOG.md)
[![Works with Claude Code](https://img.shields.io/badge/works%20with-Claude%20Code-9a8cff)](https://claude.ai/code)
![Plugins](https://img.shields.io/badge/plugins-registry-9a8cff)
![Version](https://img.shields.io/badge/version-0.7.0-9a8cff)
![Self-test](https://img.shields.io/badge/self--test-1000%2B%20checks-5ec98a)
![MCP Tools](https://img.shields.io/badge/MCP%20tools-38-9a8cff)
![Schema](https://img.shields.io/badge/schema-v8-9a8cff)
![AI insights](https://img.shields.io/badge/AI%20insights-opt--in-ff8a5b)
[![Release](https://img.shields.io/github/v/release/ingridtoulotte/claudestudio?color=9a8cff&label=release)](https://github.com/ingridtoulotte/claudestudio/releases)
[![PyPI](https://img.shields.io/pypi/v/claudestudio)](https://pypi.org/project/claudestudio/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/claudestudio)](https://pypi.org/project/claudestudio/)
[![Discussions](https://img.shields.io/github/discussions/ingridtoulotte/claudestudio)](https://github.com/ingridtoulotte/claudestudio/discussions)
[![Contributing](https://img.shields.io/badge/contributing-welcome-5ec98a)](CONTRIBUTING.md)
[![Plugin Registry](https://img.shields.io/badge/plugins-registry-9a8cff)](registry/plugins.json)
[![GitHub Actions](https://img.shields.io/badge/CI-GitHub%20Actions-2088ff)](docs/GITHUB_ACTIONS.md)
[![Last Commit](https://img.shields.io/github/last-commit/ingridtoulotte/claudestudio)](https://github.com/ingridtoulotte/claudestudio/commits/main)
[![Stars](https://img.shields.io/github/stars/ingridtoulotte/claudestudio?style=social)](https://github.com/ingridtoulotte/claudestudio/stargazers)

[Highlights](#-highlights) · [Quickstart](#-quickstart) · [What's new in v0.7.0](#-whats-new-in-v070) · [Compare](#-how-claudestudio-compares) · [Architecture](#️-architecture) · [Power recipes](#-power-user-recipes) · [Features](#-features) · [CLI](#-cli) · [Community](#-community) · [FAQ](#-faq)

<br/>

> 🤖 **Built for the Claude Code ecosystem.** ClaudeStudio is the workspace that makes your Claude Code sessions searchable, replayable, and understandable — 100% local, zero dependencies, and designed to compose with Claude Code's MCP, hooks, and CLI.

<br/>

<img src="docs/demo.svg" alt="ClaudeStudio replaying a Claude Code session — prompt, thinking, edit, test, and result unfolding on a scrubable timeline" width="100%" />

<sub><i>Replay any session like a movie — prompt → thinking → tool calls → result, on a scrubable timeline.</i></sub>

<!-- TODO docs/demos/v070_quickstart.gif — 60-second demo: install → serve → AI insights → semantic search → live session → cluster view -->
<sub><i>60-second demo (coming soon): install → serve → AI insights → semantic search → live session → cluster view.</i></sub>

</div>

**What is ClaudeStudio?** It's the local-first desktop workspace for everyone who
runs Claude Code. Point it at your machine and it indexes, searches, replays,
analyzes, *clusters* and (optionally) AI-summarizes every session you've ever run —
on Windows, macOS and Linux, with **zero dependencies** and **zero data leaving your
machine**. New in v0.7.0: an opt-in AI insights layer and fully local semantic
search. Built for the whole Claude Code community, not just its maintainer.

---

> 🌍 **Built by and for Claude Code developers.** Using ClaudeStudio?
> [Leave a review in Discussions →](https://github.com/ingridtoulotte/claudestudio/discussions) or submit a plugin to the [registry](registry/plugins.json).

---

## ✨ Highlights

- ✨ **Opt-in AI insights** — `claudestudio ai-summary`, `ai-coach`, `ai-prompt`. The *only* feature that can touch the network, and only when you set `ANTHROPIC_API_KEY`. Zero model calls by default. *(new in v0.7.0)*
- 🧭 **Local semantic search** — find sessions by *meaning* with zero-dependency TF-IDF + cosine (`claudestudio similar`). No embeddings API. *(new in v0.7.0)*
- 🗺 **Session clustering** — auto-group your history into labelled topic clusters with stdlib k-means (`claudestudio clusters`). *(new in v0.7.0)*
- 📐 **Context-window analyzer** — per-turn window utilization with a waste indicator, so you know when you're fragmenting work. *(new in v0.7.0)*
- 🧪 **Per-model analytics** — cost / health / tool-success by model, plus a "use this model for this task" recommender. *(new in v0.7.0)*
- 📓 **Jupyter export** — turn any session into a runnable `.ipynb` to share a reproducible workflow. *(new in v0.7.0)*
- 🤝 **Collaborative annotations** — export/import your notes as portable JSON; share gold-standard sessions with the team, 100% local. *(new in v0.7.0)*
- 🐚 **Shell completions** — first-class bash/zsh/fish tab completion (`claudestudio completions install`). *(new in v0.7.0)*
- 🔴 **Live session viewer** — watch a session stream in as Claude Code writes it (`claudestudio watch-session`). *(new in v0.7.0)*
- 🔒 **100% local, zero dependencies** — pure Python standard library. No `pip install`, no `node_modules`, no telemetry, no phone-home.
- 🗂 **Browse every session** — fast, sortable, filterable list of every Claude Code conversation; star the keepers, archive the noise.
- 🔎 **Search everything, instantly** — SQLite **FTS5 / BM25** full-text across every prompt, response, thinking block, and tool call, behind a <kbd>⌘K</kbd> / <kbd>Ctrl K</kbd> palette.
- ⏯ **Replay sessions like a movie** — watch a conversation unfold on a scrubable timeline: prompt → thinking → tool calls → result.
- ✦ **Ask your history** — grounded, deterministic local Q&A with deep-link citations. **Zero model calls, nothing uploaded, no API key.**
- 📊 **Cost & usage analytics** — deterministic spend at public Anthropic prices (cache-aware), plus tokens, tools, and a weekday×hour heatmap.
- 📤 **Export & share** — turn any session into clean Markdown, JSON, or a single self-contained HTML page.
- 🔌 **MCP server** — `claudestudio mcp` lets **Claude Code query your own history**: search sessions, pull a session, ask grounded questions. Zero model calls. *(new in v0.5.0)*
- ✦ **Smart highlights & knowledge graph** — deterministic heuristics surface breakthroughs, cost spikes, marathons, and recurring prompts; a session×project×file graph shows your work as a connected web.
- 🪝 **Auto-index with Claude Code hooks** — `claudestudio hook install` keeps the index fresh after every session, hands-free. *(new in v0.5.1)*
- 📡 **Live updates** — `claudestudio watch` + an in-app Server-Sent-Events toast surface new sessions the moment they land. *(new in v0.5.1)*
- 📘 **Message bookmarks** — star a *specific* message, not just a session, and deep-link straight back to it. *(new in v0.5.1)*
- 🔀 **Inline diffs** — see a real unified diff of every edit in the replay, not raw tool args. *(new in v0.5.1)*
- 📄 **Activity reports** — a shareable, print-ready HTML/Markdown summary of any week or month (`claudestudio report`). *(new in v0.5.1)*
- 🩺 **Session health scores** — a deterministic 0–100 grade per session (tool success, errors, token efficiency, completion) as a coloured A–F dot. *(new in v0.5.2)*
- 💰 **Budget tracker** — set a `$/month` or `$/week` ceiling and get a radial-arc gauge plus a spend-alert banner, all from local logs. *(new in v0.5.2)*
- 📘 **CLAUDE.md generator** — `claudestudio generate-claude-md` writes a `CLAUDE.md` from how you actually work on a project. *(new in v0.5.2)*
- ✎ **Session annotations** — attach searchable personal notes to any session or message; they survive reindexing. *(new in v0.5.2)*
- ⚡ **Efficiency dashboard** — output-per-dollar, tool-success rate, and a per-project efficiency ranking. *(new in v0.5.2)*
- 📚 **Prompt library** — auto-extract your most reusable prompts, plus star/search your own. *(new in v0.5.2)*
- 🏷 **Session tags & labels** — custom coloured labels for any session; filter by any combination. *(new in v0.6.1)*
- 📖 **Smart session narratives** — one-paragraph story of every session (goals, approach, outcome, next steps). No model calls. *(new in v0.6.1)*
- 🔥 **Per-file impact heatmap** — which files does Claude edit most? A 12-week visual matrix. *(new in v0.6.1)*
- 📰 **Daily digest** — standup-ready summary of today's (or any day's) Claude Code sessions. *(new in v0.6.1)*
- 🎨 **Theme system** — dark / light / system / high-contrast themes; keyboard shortcut `T`. *(new in v0.6.1)*
- 📦 **Static share pack** — share any session as a self-contained `.html` file. No server, no upload. *(new in v0.6.1)*
- 📊 **Benchmark** — week-over-week and month-over-month efficiency comparison with trend arrows. *(new in v0.6.1)*
- 🔌 **Plugin API** — drop a `.py` into `~/.claudestudio/plugins/` to extend ClaudeStudio. *(new in v0.6.1)*
- 📋 **Session resume briefs** — `claudestudio resume --last` (or press `R`) builds a copy-paste-ready context block to pick up exactly where you left off in a *new* Claude Code window. *(new in v0.6.2)*
- ⚖️ **Session comparison** — diff any two sessions (cost, tokens, health, prompts, files) with a plain-English verdict on which approach was better. Press `C`. *(new in v0.6.2)*
- ⚠️ **Error taxonomy** — every tool error is classified (`permission_error`, `file_not_found`, `syntax_error`, `timeout`, `api_error`, `assertion_failure`) into an errors dashboard with a week-over-week trend. *(new in v0.6.2)*
- 🧬 **Prompt-to-outcome trace** — a collapsible causal tree (prompt → tools → files → errors → outcome) for any prompt. Press `X`. *(new in v0.6.2)*
- 🔔 **Local webhooks** — POST alerts to a loopback/LAN URL on new sessions, budget alerts, or low health — RFC-1918 enforced so data never leaves your network. *(new in v0.6.2)*
- ✅ **CLAUDE.md verification** — `claudestudio verify-claude-md` scores each claim in your `CLAUDE.md` against what Claude Code actually did (✅ verified / ⚠️ stale / ❓ unverifiable). *(new in v0.6.2)*
- 📈 **Budget forecasting** — project end-of-month spend at the current pace, the biggest cost driver, and the most wasteful (expensive + low-health) pattern. *(new in v0.6.2)*

---

## 🆕 What's new in v0.7.0

The **Intelligence Layer** release — the most significant yet. Nine substantial
features for the developer who has hundreds of sessions and can't find the one
with the perfect auth refactor. Schema migrates in place to **v8**; self-test
**858 → 1145**; MCP **30 → 38 tools**. Still pure Python standard library, still
100% local — the *one* feature that can reach the network (AI analysis) is strictly
opt-in and gated on your own `ANTHROPIC_API_KEY`.

<!-- TODO docs/demos/v070_quickstart.gif — 60-second demo: install → serve → AI insights → semantic search → live session → cluster view -->

- **✨ Opt-in AI analysis** — natural-language session summaries, a personal coaching
  report, and prompt rewriting. Zero model calls by default.

  ```console
  $ claudestudio ai-summary --last
    Refactored the auth middleware to async; led with the file path, iterated on tests…
    Improvement suggestions:
      1. Lead debugging sessions with the failing test output.
      2. …
    (claude-haiku-4-5-20251001 · 1,240 tokens · $0.0009)
  ```
  <!-- TODO screenshot: docs/screenshots/v070_ai_insights.png -->

- **🧭 Local semantic search** — TF-IDF vectors + cosine similarity, zero deps.

  ```console
  $ claudestudio similar <session_id> --top 5
    0.930  Debug N+1 query in orders endpoint   shares: join, eager, p95
  ```
  <!-- TODO screenshot: docs/screenshots/v070_similar_sessions.png -->

- **🗺 Session clustering** — deterministic k-means over the vectors, auto-labelled.

  ```console
  $ claudestudio clusters --k 8
    ▸ debugging·auth·jwt   (22 sessions · $1.00 avg · health 77)
  ```
  <!-- TODO screenshot: docs/screenshots/v070_clusters.png -->

- **🔴 Live session viewer** — watch a session stream in as it's written.

  ```console
  $ claudestudio watch-session --last
    [14:32:17] 🛠 tool_use: Read auth.py
  ```
  <!-- TODO screenshot: docs/screenshots/v070_live_session.png -->

- **🐚 Shell completions** — one command, permanent tab completion.

  ```console
  $ claudestudio completions install
    ✓ installed bash completions to ~/.bash_completion.d/claudestudio
  ```

- **📐 Context-window efficiency analyzer** — per-turn utilization + waste flag.

  ```console
  $ claudestudio context-analysis --last
    t  0 |###---------------------------|   9.6%
    avg 6.6%  peak 15.6%  ⚠ fragmented
  ```
  <!-- TODO screenshot: docs/screenshots/v070_context_analyzer.png -->

- **🧪 Per-model analytics & recommender** — cost/health/tool-success by model.

  ```console
  $ claudestudio model-stats
    claude-opus-4-8     10   $10.48   health 77   97.9% tools ok
  ```
  <!-- TODO screenshot: docs/screenshots/v070_model_stats.png -->

- **🤝 Collaborative annotations** — share your notes as portable JSON.

  ```console
  $ claudestudio annotations export --out annotations.json
  $ claudestudio annotations import annotations.json --merge
  ```

- **📓 Jupyter notebook export** — a runnable `.ipynb` from any session.

  ```console
  $ claudestudio export --format ipynb --last --out session.ipynb
  ```
  <!-- TODO screenshot: docs/screenshots/v070_notebook_export.png -->

Each feature is exposed three ways — **CLI**, **HTTP API**, and an **MCP tool** — so
Claude Code can consume its own history. See the per-feature docs:
[AI Analysis](docs/AI_ANALYSIS.md) · [Semantic Search](docs/SEMANTIC_SEARCH.md) ·
[Clustering](docs/CLUSTERING.md) · [Completions](docs/COMPLETIONS.md) ·
[Context Analysis](docs/CONTEXT_ANALYSIS.md) · [Model Analytics](docs/MODEL_ANALYTICS.md) ·
[Collaborative Annotations](docs/COLLAB_ANNOTATIONS.md) · [Notebook Export](docs/NOTEBOOK_EXPORT.md).

---

## 🥊 How ClaudeStudio compares

| | **ClaudeStudio** | Commercial session viewers | DIY viewer scripts |
|---|:---:|:---:|:---:|
| 100% local (no cloud) | ✅ | ⚠️ usually cloud | ✅ |
| Zero dependencies | ✅ | ❌ | ⚠️ varies |
| Full-text search (FTS5/BM25) | ✅ | ✅ | ❌ |
| Session replay | ✅ | ✅ | ❌ |
| MCP integration | ✅ 38 tools | ❌ | ❌ |
| Plugin system | ✅ + registry | ⚠️ | ❌ |
| AI insights (opt-in) | ✅ | ✅ (always-on) | ❌ |
| Semantic search | ✅ (local TF-IDF) | ✅ (cloud embeddings) | ❌ |
| Session clustering | ✅ | ⚠️ | ❌ |
| Shell completions | ✅ | ⚠️ | ❌ |
| Jupyter export | ✅ | ❌ | ❌ |
| Open source (MIT) | ✅ | ❌ | ✅ |

---

## 🏗️ Architecture

```
~/.claude/projects/     ←  read-only
       ↓
   claudestudio index
  (SQLite + FTS5 + TF-IDF vectors + clusters + AI cache)
       ↓ ↕ ↕ ↕
  HTTP API (127.0.0.1)  ←→  Browser UI  ←→  MCP Server  ←→  Claude Code
       ↕
  CLI (50+ commands)  →  stdout / clipboard / .ipynb / .html / .md
```

ClaudeStudio reads your Claude Code session `.jsonl` files **read-only** and builds
a local SQLite index (full-text via FTS5, plus derived TF-IDF vectors, cluster
assignments and an AI summary cache). Every surface — the browser UI, the HTTP API,
the MCP server, and the CLI — reads from that one index, so behaviour can never
drift between them, and the self-test exercises all of them.

Nothing leaves your machine. The server binds to `127.0.0.1` only; every analytic is
computed deterministically and locally. The **single** exception is the opt-in AI
analysis layer, which calls `https://api.anthropic.com` via `urllib` and *only* when
you set `ANTHROPIC_API_KEY` — with no key, there is no network code path at all.

---

## ⚡ Power user recipes

```bash
# Morning standup: what did Claude do yesterday?
claudestudio digest --yesterday | pbcopy

# Before a PR: get an AI summary of your last session
claudestudio ai-summary --last | gh pr comment --body-file -

# Find sessions about authentication across your history
claudestudio similar $(claudestudio list --json | jq -r 'first(.[].id)')

# Export today's best session as a Jupyter notebook to share with the team
claudestudio export --format ipynb --last --out session_$(date +%Y%m%d).ipynb

# Cluster all your sessions and identify your top topic areas
claudestudio clusters --k 8

# Get a coaching report on your last month of Claude Code usage
claudestudio ai-coach

# Install tab completion in one command
claudestudio completions install

# Live-watch a session as Claude Code writes it
claudestudio watch-session --last
```

---

## 🆕 What's new in v0.6.3

The **Community & Clarity** release — ClaudeStudio grows from a power-user tool into a community platform. Schema migrates in place to **v7**; self-test **729 → 858**; MCP **26 → 30 tools**. Still 100% local, zero dependencies, deterministic.

- **🎬 First-run guided tour** — a pure-JS, zero-dependency overlay walks new users through search, sessions, analytics and the hook on their very first launch. Replay any time with `?tour=1`, or read it in the terminal with `claudestudio tour`.
- **🔌 Community plugin registry** — discover and install community plugins without copying files:

  ```console
  $ claudestudio plugins list
     ○ slack_notify     Post a session summary to a Slack webhook   [notifications, slack]
     ○ github_status    Set a GitHub commit status for a session    [github, ci]
     ○ ascii_report     GET /api/ascii-report — today's sessions    [reporting]
  $ claudestudio plugins install slack_notify   # HTTPS-only, allowlisted host, checksum-verified
  ```

- **📱 Mobile-responsive UI** — the sidebar collapses to a 5-icon bottom nav, 44px touch targets, a full-screen ⌘K palette, and PWA install meta tags. Desktop is unchanged. No new dependencies.
- **🔍 Persistent search history** — your last 200 searches are remembered and surfaced as one-tap suggestions in the ⌘K palette (rerun, `Delete` to remove, "Clear history"). Press `H`.
- **🤖 GitHub Actions integration** — a reusable workflow + `claudestudio github-summary` post a session digest (cost, tokens, tool success, health, top files) as a PR comment when Claude Code runs in CI. See [docs/GITHUB_ACTIONS.md](docs/GITHUB_ACTIONS.md).
- **📊 Tool Chain Visualizer** — a server-rendered Sankey-style SVG of your most common `tool_A → tool_B → tool_C` sequences, with avg cost and health per chain.
- **📝 Session templates** — pick a template (`refactor`, `debug`, `new-feature`, `review`), fill the blanks, and copy a ready-to-paste context block — with `{auto-context}` filled from your own history. Press `N`.
- **🤝 Community infrastructure** — a v0.6.x [SECURITY.md](SECURITY.md), an overhauled [CONTRIBUTING.md](CONTRIBUTING.md), and a plugin-submission issue form.

<!-- TODO screenshot: docs/screenshots/v063_tour_overlay.png — First-run guided tour overlay (desktop) -->
<!-- TODO screenshot: docs/screenshots/v063_mobile_sessions.png — Mobile session list with bottom nav -->
<!-- TODO screenshot: docs/screenshots/v063_search_history.png — Search palette showing recent searches -->
<!-- TODO screenshot: docs/screenshots/v063_plugin_registry.png — `claudestudio plugins list` terminal output -->
<!-- TODO screenshot: docs/screenshots/v063_tool_chain_svg.png — Tool chain Sankey SVG in the browser -->
<!-- TODO screenshot: docs/screenshots/v063_github_pr_comment.png — GitHub PR comment from CI integration -->
<!-- TODO screenshot: docs/screenshots/v063_template_picker.png — Session template picker UI -->
<!-- TODO demo: docs/demos/quickstart.gif — install → index → serve → open → click a session (30s) -->
<!-- TODO demo: docs/demos/search_palette.gif — ⌘K, type a query, see results with history (15s) -->
<!-- TODO demo: docs/demos/replay.gif — open a session, play, scrub the timeline (20s) -->

---

## 🆕 What's new in v0.6.2

The **Insight Engine** release — ClaudeStudio stops being just a recorder of your history and becomes an active intelligence layer that surfaces what matters, prevents waste, and accelerates your next session. Schema migrates in place to **v6**; self-test **623 → 729**; MCP **20 → 26 tools**. Still 100% local, zero dependencies, deterministic.

- **`claudestudio resume`** — a context-rich handoff brief (last tool calls + results, recent errors, uncommitted files via `git status`, branch/SHA, open questions) wrapped in a `CONTEXT FOR NEW SESSION` block. One keystroke (`R`) copies it to your clipboard.

  ```console
  $ claudestudio resume --last
  === CONTEXT FOR NEW SESSION ===
  Resuming work from a previous Claude Code session: Refactor auth middleware to async
  Project: /home/dev/orbit-api   ·   Git: branch feat/async @ 4f31a2c
  Most recent actions:  ✓ Edit — auth.py   ✗ Bash — pytest (2 failed)
  Open questions / where I left off:  - should the token refresh be idempotent?
  === END CONTEXT ===
  ✓ copied to clipboard
  ```

- **Session comparison** — `claudestudio compare <A> <B>` (or `C` in the UI):

  ```console
  $ claudestudio compare 8f2c… 1ab7…
  cost Δ    -$0.0143
  tokens Δ  -48,210
  health Δ  +12
  Session B was 31% cheaper and a higher tool-success rate — probably a better approach.
  ```

- **Error taxonomy** — `get_error_taxonomy` (MCP) / the Errors card in Analytics: top error types, which projects trigger the most, the worst sessions, and a 12-week trend.
- **Outcome trace**, **local webhooks**, **CLAUDE.md verification**, and **budget forecasting** round out the release. Six new MCP tools expose all of it to Claude Code itself.

> **Upgrade note:** the index migrates `v5 → v6` in place on first open (adds the derived `session_errors` table; no data loss). An older ClaudeStudio opening a v6 index fails loudly with a clear version-mismatch message rather than reading the wrong schema.

---

## 🆕 What's new in v0.6.1

The **Deep Intelligence & Community** release. Schema migrates in place to **v5**; self-test **495 → 623**; MCP **16 → 20 tools**. Still 100% local, zero dependencies, deterministic.

<!-- TODO v0.6.1: docs/screenshots/tags_filter.png — session list filtered by tag -->
<!-- TODO v0.6.1: docs/screenshots/narrative_card.png — session narrative panel -->
<!-- TODO v0.6.1: docs/screenshots/file_heatmap.png — 12-week file heatmap SVG -->
<!-- TODO v0.6.1: docs/screenshots/digest_terminal.png — terminal digest output -->
<!-- TODO v0.6.1: docs/screenshots/theme_toggle.png — light/dark theme comparison -->
<!-- TODO v0.6.1: docs/screenshots/share_pack.png — standalone share HTML in browser -->
<!-- TODO v0.6.1: docs/screenshots/benchmark.png — benchmark with trend arrows -->
<!-- TODO v0.6.1: docs/screenshots/plugin_api.png — example plugin output -->

- **Session tags & labels** — a personal knowledge layer: create coloured tags (`bug-fix`, `architecture`, `ship-it`), apply them to any session, and filter by any combination. User state, survives reindexing (schema **v5**; `/api/tags`; MCP `list_tags`, `get_session_tags`; `claudestudio tag`).
- **Smart session narratives** — a deterministic one-paragraph story of any session: goal, approach, outcome, files changed, errors, recovery, next steps, and a quality label — no model calls. Perfect for PR descriptions and stand-up notes (`/api/session/{id}/narrative`; MCP `get_session_narrative`; `claudestudio narrative`).
- **Per-file impact heatmap** — which files does Claude touch most? A file × 12-week matrix as JSON and a pure-stdlib SVG (`/api/files/heatmap`, `/api/files/heatmap.svg`; MCP `get_file_heatmap`).
- **Daily digest** — a standup-ready summary of any day's sessions with a pre-rendered Markdown block (`/api/digest`, `/api/digest.md`; `claudestudio digest [--yesterday|--date]`).
- **Theme system** — dark / light / system / high-contrast, expressed as CSS custom-property overrides, persisted locally and to the index for cross-device consistency. Keyboard `T` cycles (`web/themes.js`, `/api/preferences`).
- **Static share pack** — export a session as a single self-contained `.html` file that replays from itself: no server, no network, no upload (`/api/session/{id}/share.html`; `claudestudio share`).
- **Benchmark** — week / month / quarter efficiency comparison (output-per-dollar, tool success, health) with trend arrows and a plain-English verdict (`/api/benchmark`; `claudestudio benchmark`).
- **Plugin API foundations** — drop a `.py` in `~/.claudestudio/plugins/` to add HTTP routes, MCP tools, CLI commands, or post-index hooks. Isolated, localhost-only, additive ([`docs/PLUGINS.md`](docs/PLUGINS.md)).
- **Enhanced `doctor`** — index freshness, schema version, plugin status, preferences, hottest file, a benchmark verdict, auto-fix hints, and exit codes (0 healthy / 1 warnings / 2 critical).

## 🆕 What's new in v0.6.0

The **workspace, completed.** Schema migrates in place to **v4**; self-test **396 → 495**; MCP **14 → 16 tools**.

- **Time-machine replay with speed control** — scrub at `0.5× / 1× / 2× / 5× / ∞`, a CSS typewriter reveal, "jump to first error", and an end-of-playback summary card. ([`docs/REPLAY.md`](docs/REPLAY.md))
- **Cross-session references** — finds prompts like *"as we did last time"* and proposes the session you meant (`/api/cross-refs`, MCP `get_cross_refs`).
- **Prompt effectiveness score** — a deterministic 0–100 score per prompt from what happened next, shown as a bar in the Prompt Library.
- **Multi-machine sync (no cloud)** — `claudestudio sync --push/--pull` over git or rsync; only `~/.claudestudio/` is touched. ([`docs/SYNC.md`](docs/SYNC.md))
- **Installable PWA** — `manifest.json` + a service worker that caches the shell (instant load, offline state) and keeps API data network-first.
- **Patterns dashboard** — recurring tool workflows as SVG mini-flowcharts, debugging loops, peak hours, and a 4-week project-momentum index.
- **RSS / Atom feed** — `/api/feed.rss`, `/api/feed.atom`, `claudestudio feed` — pipe your history into any reader or Slack bot.
- **`claudestudio init`** — a one-command onboarding wizard (hook, watch, budget, self-test); `--yes` for non-interactive.
- **GitHub deep linker** — detects `#123` / `owner/repo#456` / URLs at index time; a references card + `find_sessions_by_github_ref` (MCP #16). Schema **v4** table.
- **CHANGELOG draft generator** — `claudestudio changelog-draft` sorts the git log since the last tag into Added/Changed/Fixed/Security.
- **Developer self-test dashboard** — hidden `?dev=1` / `Shift+D` view that streams the self-test over SSE.
- **WCAG 2.1 AA pass** — focus rings, `aria-label`s (CI-enforced), keyboard-operable replay slider, `role="status"` toast, per-route `<title>`. ([`docs/ACCESSIBILITY.md`](docs/ACCESSIBILITY.md))
- **Community-grade infra** — issue forms, PR template, 5×3 CI matrix, CodeQL, Dependabot, stale bot.

<details><summary>What's new in v0.5.2</summary>

## 🆕 What's new in v0.5.2

- **Session health scores** — a deterministic 0–100 grade per session (`health.py`), cached on the index, shown as an A–F dot in the list and a breakdown card in the detail view. `list --sort health`.
- **Budget tracker & alerts** — `claudestudio budget --set 50 --period monthly`, `GET/POST/DELETE /api/budget`; a radial-arc gauge in the Efficiency view and a spend-alert banner.
- **CLAUDE.md generator** — `claudestudio generate-claude-md`, `GET /api/project/{id}/claude-md`; analyses a project's history into a paste-ready `CLAUDE.md`.
- **Session annotations** — notes on a session or message, FTS5-searchable, survive reindexing (`/api/session/{id}/annotations`, `/api/annotations/search`).
- **Efficiency dashboard** — `GET /api/analytics/efficiency`: output-per-dollar, success rate, per-project ranking, 12-week trend.
- **Git context** — a `🔀 branch @ sha` badge resolved from `git log` (read-only, cached, never raises).
- **Prompt library** — `/api/prompts` (+ `?q=`, `?starred=1`), `POST /api/prompts/extract`; a Prompts view in the sidebar.
- **Batch export** — `claudestudio export --all --zip`, `POST /api/export/batch` (ZIP + generated `index.md`).
- **Keyboard navigation** — `web/keyboard.js` (`KeyboardNavigator`, `?` cheat sheet).
- **Four new MCP tools** (10 → **14**): `get_cost_by_period`, `get_diff_for_session`, `get_annotations`, `generate_project_brief`.
- **Full API reference** at [`docs/API.md`](docs/API.md). Self-test grew 301 → **396** checks; schema **v3**.

</details>

## 🛠 For power users

| Shortcut | Action |
|---|---|
| `/` | Open search palette |
| `j` / `k` | Navigate sessions / messages |
| `?` | Show all shortcuts |
| `s` | Star/unstar session |
| `e` | Export session |
| `T` | Cycle themes (dark → light → system → high-contrast) |
| `n` | Open narrative for current session |
| `d` | Open daily digest |
| `R` | Copy a resume brief for the current session |
| `C` | Compare two sessions |
| `X` | Toggle the outcome trace |
| `N` | New session from a template |
| `H` | Open recent search history |

<details><summary>What's new in v0.5.1</summary>

## 🆕 What's new in v0.5.1

- **Auto-index hooks** — `claudestudio hook install` wires reindexing to Claude Code's `SessionEnd`. See [`docs/HOOK.md`](docs/HOOK.md).
- **Live watch + SSE** — `claudestudio watch` and the in-app "new sessions available" toast (`/api/events`).
- **Message bookmarks** — per-message stars, a global Bookmarks view, deep links; new MCP tool `list_bookmarks`.
- **Inline unified diffs** in the replay view (Diff/Raw toggle).
- **Activity report** — `claudestudio report`, `/api/report.html|.json`, print-optimized.
- **Per-tool latency** (`/api/tools/latency`), **multi-root** (`--root a:b`, schema v2), **prompt patterns** (`/api/prompts/patterns`, MCP tool `get_prompt_patterns`).
- **Export+**: print CSS, CSV (`/api/analytics.csv`, `/api/sessions.csv`), `export --all`.
- **`--version` + `info`**. Self-test grew 209 → **301** checks; MCP exposed **10** tools.

</details>

<details><summary>What's new in v0.5.0</summary>

## 🆕 What's new in v0.5.0

- **MCP server** — make ClaudeStudio queryable by Claude Code itself over the Model Context Protocol (8 read-only tools, stdio JSON-RPC, zero deps). See [`docs/MCP.md`](docs/MCP.md).
- **Tool-usage dashboard API** — leaderboard, per-tool success rates, co-occurrence matrix, most-edited files (`/api/tools/stats`).
- **Smart Highlights** — `claudestudio highlights` and `/api/highlights`.
- **Knowledge graph** (`/api/graph`) and **session similarity** (`/api/session/{id}/similar`, TF-IDF).
- **JSON export** and a short **`cs`** command alias.
- Architecture reference: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Self-test grew 161 → **209** checks.

</details>

---

## The problem

You use Claude Code every day. Over a few months you accumulate **hundreds of sessions, millions of tokens, thousands of tool calls** — a real body of work and knowledge.

Then it evaporates. Sessions vanish into `~/.claude/projects/*.jsonl`. That brilliant debugging path from three weeks ago? Gone. The refactor where everything clicked? Unfindable. How much have you actually spent? No idea.

**You have the data. You don't have a workspace.**

ClaudeStudio is that workspace — a fast, local, beautiful home for everything you and Claude have ever built together.

<div align="center">
<img src="docs/screenshots/analytics.png" alt="ClaudeStudio analytics dashboard" width="92%" />
</div>

---

## ⚡ Quickstart

ClaudeStudio is **pure Python standard library** — no `pip install`, no `node_modules`, no build step. If you have Python 3.9+, you can run it.

**Try it in one command — no clone, no install** (needs [`pipx`](https://pipx.pypa.io)):

```bash
pipx run --spec git+https://github.com/ingridtoulotte/claudestudio claudestudio
```

That builds it in a throwaway environment and launches the app. Prefer a checkout?

```bash
git clone https://github.com/ingridtoulotte/claudestudio
cd claudestudio

# Launch the app — it indexes your sessions and opens in a window
python -m claudestudio
```

Either way: ClaudeStudio finds `~/.claude/projects`, builds a local index, and opens the workspace in an app window (Chrome/Edge app-mode if available, otherwise your browser).

**Just want to look around first?** Explore a realistic, fully synthetic dataset — no real data touched:

```bash
python -m claudestudio demo --serve
```

<div align="center">
<img src="docs/screenshots/sessions.png" alt="Session browser" width="92%" />
</div>

---

## 🪝 Auto-index with Claude Code Hooks

Stop thinking about Sync. One command makes ClaudeStudio **update itself every
time Claude Code finishes a session**:

```bash
claudestudio hook install
```

It adds a single entry to your `~/.claude/settings.json` that runs the
(incremental, sub-second) `claudestudio index` on Claude Code's `SessionEnd`
event:

```json
{
  "hooks": {
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "claudestudio index" } ] }
    ]
  }
}
```

The merge is **safe** (never clobbers your existing hooks), **idempotent**
(install twice = no duplicate), and **reversible** (`claudestudio hook
uninstall` restores the file). `claudestudio hook status` shows whether it's on
and when the index last ran; `claudestudio doctor` nudges you if it isn't.
Full guide: [`docs/HOOK.md`](docs/HOOK.md).

<!-- TODO: screenshot v0.5.1 — terminal showing `claudestudio hook install` and the JSON it wrote (docs/screenshots/v051_hook.png) -->

---

## 📡 Live Updates

The open app keeps itself current. ClaudeStudio holds a **Server-Sent-Events**
connection (`/api/events`); when the index changes — via the hook, the CLI, or
the Sync button — a non-blocking toast appears in the corner:

> **New sessions available — click to reload.**

Click it and the current view refreshes **in place** (no full page reload). Want
a foreground watcher too? Pair the app with:

```bash
claudestudio watch     # polls your projects root, reindexes on change, Ctrl-C to stop
```

<!-- TODO: screenshot v0.5.1 — the in-app "new sessions available" toast (docs/screenshots/v051_live.png) -->

---

## 📘 Bookmarks

Favorites mark whole sessions; **bookmarks mark the exact message that mattered**
— the moment the bug was found, the line that fixed it. Click the 🏷 next to any
message in the replay, add an optional note, and it shows up in the **Bookmarks**
view in the sidebar with a deep link that jumps straight back to that session and
message. Bookmarks live in their own table and survive reindexing, and Claude
Code can read them through the `list_bookmarks` MCP tool.

<!-- TODO: screenshot v0.5.1 — the global Bookmarks list with deep links (docs/screenshots/v051_bookmarks.png) -->

---

## ✦ Features

### 🗂 Browse every session
A fast, sortable, filterable list of every conversation. Search titles, filter by favorites, sort by recency, cost, message count, tools used, or duration. Star the keepers, archive the noise.

### ✦ Ask your history — a grounded, local companion
A question box for your whole Claude Code history. Ask *"what should I reopen next?"*, *"give me a handoff brief"*, *"which files changed?"*, *"why was `parser.py` edited?"*, or *"where did the tokens go?"* and get a structured answer with **citations that deep-link straight to the exact session and message**.

It is **grounded, not generative**: every answer is *computed* from your local index with deterministic rules — **no model calls, nothing uploaded, no API key.** The same question always gives the same answer, and each one is footed with what it was computed from. Open any session and hit **✦ Ask about this** to scope it to that session (digest, handoff brief, most-important tool calls).

### ⏯ Replay sessions like a movie
Watch Claude work. Press play and the conversation unfolds chronologically — prompts, thinking, tool calls, and edits revealing themselves with real pacing. Scrub the timeline, jump anywhere, change speed.

<div align="center">
<img src="docs/screenshots/session.png" alt="Session replay" width="92%" />
</div>

### 🔎 Search everything, instantly
Full-text search (SQLite **FTS5**, BM25-ranked) across every prompt, response, thinking block, and tool call you've ever made. Open the command palette with <kbd>⌘K</kbd> / <kbd>Ctrl K</kbd> and find anything in milliseconds.

<div align="center">
<img src="docs/screenshots/search.png" alt="Search experience" width="92%" />
</div>

### 📊 Understand your usage & cost
Tokens, models, tools, daily activity, a weekday-×-hour heatmap, and a **deterministic cost estimate** at public Anthropic prices (cache writes & reads priced correctly; unpriced models are flagged, never guessed).

### 🗃 Project workspace
Every repo Claude has touched, grouped and ranked — sessions, messages, spend, and last-active at a glance. Click through to that project's sessions.

<div align="center">
<img src="docs/screenshots/projects.png" alt="Project explorer" width="92%" />
</div>

### 📈 Timeline
Your whole history as activity over time — messages per day, spend per day, and a month-by-month breakdown.

<div align="center">
<img src="docs/screenshots/timeline.png" alt="Timeline view" width="92%" />
</div>

### ⚖️ Compare sessions
Put any two sessions side by side — messages, prompts, tool calls, tokens, cost, duration — with the winner highlighted per row.

<div align="center">
<img src="docs/screenshots/compare.png" alt="Compare mode" width="92%" />
</div>

### 📤 Export & share a session
Turn any session into a clean **Markdown** file or a **single self-contained HTML** page (inline styles, no scripts, no network) — perfect for an issue, a PR, or a gist. From the session view hit `⬇ .md` / `⬇ .html`, or use the CLI: `python -m claudestudio export <session-id> --format html`.

### 🔖 Saved searches & smart collections
Save any filter — a query, a sort, favorites-only, a project — as a named collection and jump back to it in one click. Saved collections live in your local index and survive every re-index.

### 🎁 Claude Wrapped
A shareable, swipeable, year-or-all-time summary of your Claude Code life. Your go-to model, favourite tool, home-base project, peak hours, epic session — copy it, or **save it as a PNG card** to share.

<div align="center">
<img src="docs/screenshots/wrapped.png" alt="Claude Wrapped" width="70%" />
</div>

---

## 🆚 Why ClaudeStudio

|                               | Raw `.jsonl` logs | `cat` / `grep` | Generic log viewer | Claudia (Tauri GUI) | Claude Code native | **ClaudeStudio** |
|-------------------------------|:-----------------:|:--------------:|:------------------:|:-------------------:|:------------------:|:----------------:|
| Browse & sort sessions        | ❌                | ⚠️ manual      | ⚠️                 | ✅                  | ❌                 | ✅               |
| Full-text search w/ ranking   | ❌                | ⚠️ line-by-line| ⚠️                 | ⚠️                  | ❌                 | ✅ FTS5 + BM25   |
| Chronological replay          | ❌                | ❌             | ❌                 | ⚠️                  | ❌                 | ✅               |
| Inline edit diffs             | ❌                | ❌             | ❌                 | ⚠️                  | ❌                 | ✅               |
| Token & **cost** analytics    | ❌                | ❌             | ❌                 | ✅                  | ❌                 | ✅ deterministic |
| Grounded local Q&A (no model) | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅               |
| MCP server (queryable by CC)  | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅ 38 tools      |
| Plugin system + registry      | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅ *(v0.6.3)*    |
| Mobile-ready UI               | ❌                | ❌             | ⚠️                 | ❌                  | ❌                 | ✅ *(v0.6.3)*    |
| GitHub Actions integration    | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅ *(v0.6.3)*    |
| Session templates             | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅ *(v0.6.3)*    |
| Persistent search history     | ❌                | ❌             | ⚠️                 | ❌                  | ❌                 | ✅ *(v0.6.3)*    |
| Auto-index hook + live watch  | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅               |
| Automatic CLAUDE.md generation| ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅               |
| Budget tracking & spend alerts| ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅               |
| Session health scoring        | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅               |
| **Resume brief** (handoff)    | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅ *(v0.6.2)*    |
| **Session comparison**        | ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅ *(v0.6.2)*    |
| **Error taxonomy**            | ❌                | ❌             | ⚠️                 | ❌                  | ❌                 | ✅ *(v0.6.2)*    |
| **Webhook notifications** (local)| ❌             | ❌             | ❌                 | ❌                  | ❌                 | ✅ *(v0.6.2)*    |
| Keyboard navigation           | ❌                | ❌             | ⚠️                 | ⚠️                  | ❌                 | ✅               |
| Message bookmarks + deep links| ❌                | ❌             | ❌                 | ❌                  | ❌                 | ✅               |
| 100% local, no telemetry      | ✅                | ✅             | ⚠️                 | ✅                  | ✅                 | ✅               |
| **No install / no toolchain** | —                 | ✅             | ❌                 | ❌ (Rust/Tauri)     | —                  | ✅ `pipx run`    |
| Zero dependencies             | —                 | ✅             | ❌                 | ❌                  | —                  | ✅ stdlib only   |

---

## 🛠 How it works

```
~/.claude/projects/**/*.jsonl                          your sessions, untouched
        │
        ▼
   parser.py            faithful, normalized model of the wire format
        │
        ▼
   index.py  ──────►   SQLite + FTS5            ~/.claudestudio/index.db
        │              · denormalized sessions table (instant sort/filter)
        │              · BM25 full-text index over messages + tool calls
        │              · incremental: unchanged files are skipped by (mtime,size)
        │              · your favorites / archive / tags survive every re-index
        ▼
   server.py  ◄──────  http.server on 127.0.0.1 (local only, no outbound calls)
        │              JSON API  +  static SPA
        ▼
   web/  (vanilla JS + CSS, no build step)     premium dark UI, hand-rolled charts
```

**Performance is a feature.** The index is denormalized for the common queries and backed by FTS5, so search and listing stay instant across thousands of sessions and millions of messages. Re-indexing is incremental — only changed files are re-parsed.

### Why this stack? (Electron / Tauri / React were all considered)

| Choice | Decision | Why |
|---|---|---|
| **Runtime** | Python **stdlib only** | The single strongest feature is *zero friction*: if you can run Claude Code, you can run this. No toolchain, no `npm`, no Rust, no 200 MB Electron download. |
| **Storage** | **SQLite + FTS5** | Ships with Python. Handles millions of rows and gives real full-text ranking for free. Survives years of history without degradation. |
| **Desktop shell** | Local web app, opened in a Chrome/Edge **app window** | All the polish of a modern UI with none of the bundle weight. One codebase, every OS. A thin Tauri wrapper is on the roadmap for those who want a true native window/installer. |
| **Frontend** | **Vanilla JS + CSS**, no framework | No build step means the repo runs as-is, forever. The UI is hand-built so every screen feels intentional rather than templated. |

Everything is **deterministic and transparent** — the cost table lives in one editable file ([`pricing.py`](claudestudio/pricing.py)), and `--selftest` asserts the numbers exactly.

---

## 🧩 For builders — use the parser, don't reverse-engineer the format

Building your own Claude Code tooling? ClaudeStudio's parser is a clean,
dependency-free reference implementation of the session wire format. Import it
instead of reading raw `.jsonl` by hand:

```python
from claudestudio import parse_session, iter_session_files, default_projects_root

for path in iter_session_files(default_projects_root()):
    s = parse_session(path)          # -> ParsedSession | None
    if s:
        print(s.title, s.user_msgs, "prompts", round(s.cost_usd, 4), "USD")
```

The full wire-format reference — record types, content blocks, usage/cost, and
every dataclass field — is documented in **[docs/FORMAT.md](docs/FORMAT.md)**.
The public API (`parse_session`, `iter_session_files`, `default_projects_root`,
`ParsedSession` / `Message` / `ToolCall`) is covered by the self-test.

---

## 💻 CLI

```text
python -m claudestudio [command]

  (no command)   build the index if needed, then launch the app
  serve          launch the desktop app          --port --host --no-browser
  index          scan & (incrementally) index     --force
  list           list sessions (filter & sort)    -q --project --model --since/--until --sort
  search         full-text search (BM25)          --kind --project --since/--until --json
  ask            grounded Q&A over your history   --session --json
  export         export a session to Markdown/HTML --format md|html --out FILE
  wrapped        print your Claude Wrapped         --year YYYY
  stats          headline numbers
  doctor         diagnose environment & index health
  demo           generate synthetic data & explore --count N --serve
  budget         track spend against a monthly/weekly ceiling
  generate-claude-md   write a CLAUDE.md from a project's history
  digest         standup-ready daily summary       --yesterday --date YYYY-MM-DD --html
  narrative      generate session narrative         <session_id> | --last
  share          export session as shareable HTML   <session_id> [--out FILE] [--no-annotations]
  benchmark      week/month/quarter efficiency report  --mode week|month|quarter --json
  tag            manage session tags                --add NAME [--colour HEX] | --list
  resume         copy-paste brief to resume a session  <session_id> | --last [--copy] [--out FILE]
  open           open a session/search in the browser  <session_id> | --last | --starred | --query TEXT
  compare        structured diff between two sessions   <session_a> <session_b> [--json]
  verify-claude-md  check a CLAUDE.md against history    --project NAME [--json]
  webhook        manage local/LAN webhook notifications --add URL --events … | --remove URL | --list
  tour           print the first-run guided tour (plain text)
  plugins        discover & install community plugins   list | install | remove | info | update [--yes]
  template       session starter templates             list | use NAME --file … --goal … | create NAME
  search-history show or clear your recent searches     --limit N --clear --json
  github-summary Markdown session summary for CI        --session PATH | --last
  ai-summary     opt-in AI session summary               <id> --last --copy --out
  ai-coach       opt-in AI coaching report               -n 20
  ai-prompt      rewrite a prompt for better results      "<prompt>"
  similar        semantically similar sessions (TF-IDF)   <id> --top N
  clusters       auto-group sessions by topic (k-means)   --k 8 --json
  context-analysis  per-turn context-window utilization   <id> --last
  model-stats    cost/health/tool-success by model        --json
  annotations    export/import your annotation layer      export|import [--merge|--replace]
  watch-session  stream a session's events as written     <id> --last
  completions    print/install shell tab-completions      bash|zsh|fish|install
  --selftest     run the built-in correctness suite (1145 checks, no deps)

  shared flags:  --db <path>   --root <projects dir>
```

**Keyboard shortcuts** (in the app): `R` copy a resume brief · `C` compare with another session · `X` toggle the prompt-to-outcome trace · `N` new session from a template · `H` search history · `/` search · `s` star · `e` export · `T` cycle theme · `?` full cheat sheet.

```bash
python -m claudestudio ask "what should I reopen next?"   # grounded, no model calls
python -m claudestudio doctor      # is everything wired up?
python -m claudestudio wrapped     # your year in review, in the terminal
python -m claudestudio stats       # quick totals
```

---

## 🔒 Privacy & trust

ClaudeStudio is built for people who care where their data goes.

- **100% local.** Your sessions never leave your machine. The server binds to `127.0.0.1` only.
- **No telemetry. No analytics. No phone-home.** Grep the source — there isn't a single outbound network call.
- **No cloud, no account, no lock-in.** The index is a plain SQLite file at `~/.claudestudio/index.db`; delete it anytime and re-build in seconds.
- **Open source & deterministic.** Pricing and aggregations are transparent and covered by an exact-assertion self-test.
- **Responsible disclosure.** Found something? See the [security policy](SECURITY.md) — the attack surface is deliberately tiny, and the localhost server is hardened (0.4.0+).

---

## 🗺 Roadmap

- [x] Run in one command with `pipx run` (no clone) — _v0.2_
- [x] Export a session to Markdown / shareable HTML — _v0.2_
- [x] Saved searches & smart collections — _v0.2_
- [x] Wrapped → shareable PNG card — _v0.2_
- [x] Documented public parser API (`from claudestudio import parse_session`) + [FORMAT.md](docs/FORMAT.md) — _v0.2_
- [x] **Ask** — grounded, local Q&A over your history (handoff briefs, "what to reopen", file history) — _v0.3_
- [x] **MCP server** — make ClaudeStudio queryable by Claude Code itself — _v0.5.0_
- [x] **Knowledge graph** (projects ↔ sessions ↔ files) & **smart highlights** — _v0.5.0_
- [x] **Diff view inside replay** (unified diff of every edit) — _v0.5.1_
- [x] **Auto-index hooks** + **live watch** (SSE) — _v0.5.1_
- [x] **Message bookmarks** with deep links — _v0.5.1_
- [x] **Activity reports** (shareable HTML/Markdown) — _v0.5.1_
- [x] **Multi-root** index across machines — _v0.5.1_
- [x] **Session health scores** + **budget tracker** — _v0.5.2_
- [x] **CLAUDE.md generator** + **session annotations** — _v0.5.2_
- [x] **Efficiency dashboard**, **prompt library**, **git context** — _v0.5.2_
- [x] **Keyboard navigation** + cheat sheet — _v0.5.2_
- [x] **Time-machine replay** with speed control + jump-to-error — _v0.6.0_
- [x] **Cross-session references** + **GitHub issue/PR deep linker** — _v0.6.0_
- [x] **Prompt effectiveness score** — _v0.6.0_
- [x] **Multi-machine sync** via git/rsync (no cloud) — _v0.6.0_
- [x] **PWA** (installable, offline shell) — _v0.6.0_
- [x] **Pattern-mining dashboard** (workflows, debug loops, momentum) — _v0.6.0_
- [x] **RSS / Atom feed** — _v0.6.0_
- [x] **`claudestudio init`** onboarding wizard — _v0.6.0_
- [x] **CHANGELOG draft generator** + **dev self-test dashboard** — _v0.6.0_
- [x] **WCAG 2.1 AA** accessibility pass — _v0.6.0_
- [x] Session tags & labels — custom coloured filtering layer — _v0.6.1_
- [x] Smart session narratives (deterministic, no model calls) — _v0.6.1_
- [x] Per-file impact heatmap (12-week SVG matrix) — _v0.6.1_
- [x] Daily digest (standup-ready summary) — _v0.6.1_
- [x] Theme system (dark/light/system/high-contrast) — _v0.6.1_
- [x] Static share pack (self-contained HTML, no upload) — _v0.6.1_
- [x] Benchmark (week/month/quarter efficiency trends) — _v0.6.1_
- [x] Plugin API foundations (~/.claudestudio/plugins/) — _v0.6.1_
- [x] **Session resume briefs** (one-keystroke handoff to a new window) — _v0.6.2_
- [x] **Session comparison** (cost/token/health/prompt/file diff + verdict) — _v0.6.2_
- [x] **Error taxonomy** & recurring-error dashboard — _v0.6.2_
- [x] **Prompt-to-outcome tracing** — _v0.6.2_
- [x] **Local/LAN webhooks** (RFC-1918 enforced) — _v0.6.2_
- [x] **CLAUDE.md verification** against real history — _v0.6.2_
- [x] **Budget forecasting** (end-of-month projection + waste finder) — _v0.6.2_
- [x] **First-run guided tour** (zero-dep overlay + terminal) — _v0.6.3_
- [x] **Community plugin registry** (discover + install, checksum-verified) — _v0.6.3_
- [x] **Mobile-responsive UI** (bottom nav, touch targets, PWA meta) — _v0.6.3_
- [x] **Persistent search history** (palette suggestions, schema v7) — _v0.6.3_
- [x] **GitHub Actions integration** (PR-comment session summary) — _v0.6.3_
- [x] **Tool chain visualizer** (Sankey-style SVG) — _v0.6.3_
- [x] **Session templates** (auto-context, copy-to-clipboard) — _v0.6.3_

### v0.7.0 — "Intelligence Layer" ✅ shipped
- [x] **Opt-in AI analysis** — natural-language summaries, coaching, prompt rewriting (`ANTHROPIC_API_KEY`-gated)
- [x] **Local semantic search** — TF-IDF vectors + cosine (no embeddings API)
- [x] **Session clustering** — stdlib k-means, auto-labelled
- [x] **Live session viewer** · **Shell completions** · **Context-window analyzer** · **Per-model analytics** · **Collaborative annotations** · **Jupyter export**

<details><summary>🗺️ Extended roadmap (planned)</summary>

### v0.7.1 — "Team Edition"
- **Read-only shared server** — `claudestudio serve --shared --read-only` exposes the UI to the LAN with an IP allowlist, for standup dashboards.
- **Multi-user index merge** — merge two team members' indexes into a unified read-only view.
- **PR auto-summary** — a GitHub App that posts a session summary automatically, no workflow file.

### v0.8.0 — "Developer Ecosystem"
- **VS Code extension** — sidebar of recent sessions, quick search, "continue this session".
- **JetBrains plugin** — the same for IntelliJ/PyCharm/WebStorm/GoLand.
- **Neovim plugin** — a Telescope picker over your sessions.
- **Session gallery** — opt-in public gallery of anonymised session summaries.

### v1.0.0 — "Stable Foundation"
- Stable API contract (no breaking changes without a major version).
- Full WCAG 2.1 AA audit and remediation.
- Localisation infrastructure (i18n-ready, English + French to start).
- Windows `.msi` and macOS `.app` distributables; Homebrew formula; VS Code marketplace listing.

</details>

Ideas and PRs welcome — see [CONTRIBUTING](CONTRIBUTING.md). Everything shipped so far lives in the [changelog](CHANGELOG.md).

### Built on ClaudeStudio

ClaudeStudio is designed to be composable. Import the parser, query the MCP server,
or build on top of the HTTP API. See [docs/API.md](docs/API.md) for the full reference.

---

## 🌍 Community

- 💬 [GitHub Discussions](https://github.com/ingridtoulotte/claudestudio/discussions) — Q&A, ideas, show & tell
- 🐛 [Issue tracker](https://github.com/ingridtoulotte/claudestudio/issues) — bug reports & feature requests
- 🔌 [Plugin registry](registry/plugins.json) — submit your plugin via the plugin-submission issue form
- 🤝 [Contributing](CONTRIBUTING.md) — first-contribution guide
- 🔒 [Security](SECURITY.md) — responsible disclosure
- 📋 [Changelog](CHANGELOG.md)
- 🗺 [Roadmap](#-roadmap)

### Built for the Claude Code ecosystem

ClaudeStudio is **infrastructure for the Claude Code community**, not a toy viewer:

- **A reference parser for the Claude Code wire format.** `from claudestudio import parse_session` gives any builder a faithful, self-test-pinned reader of the `.jsonl` session format — import it instead of reverse-engineering it ([`docs/FORMAT.md`](docs/FORMAT.md)).
- **Composable via MCP.** The 30-tool MCP server makes ClaudeStudio queryable *by Claude Code itself* — your history becomes a first-class context source for your next session.
- **Serves the entire Claude Code user base.** Anyone who runs Claude Code generates the sessions ClaudeStudio understands. It is the only zero-dependency, local-first session workspace for the tool.

ClaudeStudio was built to support the **[Anthropic Claude for Open Source](https://claude.com/contact-sales/claude-for-oss)** program's vision of thriving OSS tooling around Claude Code. If ClaudeStudio is useful to you, the most impactful thing you can do is **⭐ star the repo, use it daily, and open a PR** — that's how a quietly-depended-on tool earns its place.

---

## 📚 Citing ClaudeStudio

If you use ClaudeStudio in your work, you can cite it via [`CITATION.cff`](CITATION.cff) or:

```bibtex
@software{claudestudio,
  author  = {Toulotte, Ingrid},
  title   = {ClaudeStudio},
  url     = {https://github.com/ingridtoulotte/claudestudio},
  version = {0.7.0},
  year    = {2026}
}
```

---

## ❓ FAQ

<details>
<summary><b>Does any of my data leave my machine?</b></summary>

No. ClaudeStudio reads `~/.claude/projects` locally, builds a SQLite index on disk, and serves the UI from `127.0.0.1`. There are zero outbound network calls — `grep -r http claudestudio/` and see for yourself.
</details>

<details>
<summary><b>What is the resume brief?</b></summary>

`claudestudio resume --last` (or press `R` in the app) generates a copy-paste-ready **`CONTEXT FOR NEW SESSION`** block: the last few tool calls and their results, recent errors, your uncommitted files (via `git status`, gracefully skipped outside a repo), the current branch/SHA, and the open questions from the tail of the session. Paste it into a fresh Claude Code window to pick up exactly where you left off. Deterministic and 100% local.
</details>

<details>
<summary><b>Can I compare two sessions?</b></summary>

Yes — `claudestudio compare <A> <B>` (or `C` in the session view) produces a structured diff: cost / token / health deltas, prompts unique to each side, files touched by both, and a plain-English verdict ("Session B was 31% cheaper and had a higher tool-success rate — probably a better approach"). No model calls; every number comes from the local index.
</details>

<details>
<summary><b>How do webhooks work — is my data still local?</b></summary>

Webhooks POST a small JSON payload to a URL **you** configure when a new session is indexed, a budget threshold is crossed, or a session's health drops. Locality is enforced in code: a URL whose host is not loopback or a private-range IP (`127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`, or `localhost`) is **rejected outright** — so a webhook can pipe alerts into a local Slack bot or Home Assistant, but can never exfiltrate data to the public internet. The check is re-validated at send time and covered by the self-test.
</details>

<details>
<summary><b>Do I need to install anything (pip, npm, Rust, Electron)?</b></summary>

No. It's pure Python standard library. If you can run Claude Code, you already have everything: `git clone`, then `python -m claudestudio`. No `pip install`, no `node_modules`, no build step.
</details>

<details>
<summary><b>Will it touch or modify my session files?</b></summary>

Never. ClaudeStudio only **reads** your `.jsonl` files. Everything it generates (the index, your favorites/archive/tags) lives in a separate file at `~/.claudestudio/index.db`. Delete it anytime and rebuild in seconds.
</details>

<details>
<summary><b>How accurate is the cost estimate?</b></summary>

It's deterministic, not a guess. Token counts come straight from your session logs and are multiplied by public Anthropic prices (with cache writes and cache reads priced separately). The price table lives in one editable file — [`pricing.py`](claudestudio/pricing.py) — and `--selftest` asserts the math exactly. Models with no published price are **flagged**, never silently estimated.
</details>

<details>
<summary><b>How fast is it on a large history?</b></summary>

Built for it. The index is denormalized for the common queries and backed by SQLite FTS5/BM25, so listing and search stay instant across thousands of sessions and millions of messages. Re-indexing is incremental — unchanged files are skipped by `(mtime, size)`.
</details>

<details>
<summary><b>I just want to see it without exposing my own data.</b></summary>

Run `python -m claudestudio demo --serve`. It generates a realistic, fully synthetic corpus and opens the full app against it — your real sessions are never read.
</details>

<details>
<summary><b>Search says "degraded" — what's wrong?</b></summary>

Your Python was built without SQLite FTS5 (rare, but happens on some minimal builds). Run `python -m claudestudio doctor` to confirm. Everything else works; only full-text ranking is affected.
</details>

---

## 🤝 Contributing

```bash
python -m claudestudio --selftest   # must print ALLPASS before you push
python -m claudestudio demo --serve # iterate on the UI against synthetic data
```

No dependencies to install, no build step to learn. New here? See
[**Contributing in 5 minutes**](CONTRIBUTING.md#-contributing-in-5-minutes) for
specific starter tasks, and [docs/CLAUDE_CODE_INTEGRATION.md](docs/CLAUDE_CODE_INTEGRATION.md)
for the full Claude Code integration story.

## 🙌 Contributors

<!-- TODO: add an All Contributors table when we reach 5 contributors -->

ClaudeStudio is built for — and increasingly *by* — the Claude Code community.
Every feature in v0.7.0 was shaped by a real workflow problem developers hit at
scale. Want your name here? Good first issues, the plugin registry, and the
screenshot/GIF placeholders throughout this README are all great entry points —
start with [CONTRIBUTING.md](CONTRIBUTING.md).

## 🌟 Community showcase

> Built something cool with ClaudeStudio? [Open a Discussion](https://github.com/ingridtoulotte/claudestudio/discussions) to get featured here!

Using ClaudeStudio in your daily workflow? Add yourself to [USERS.md](USERS.md) via a PR.

## 📄 License

[MIT](LICENSE) © ClaudeStudio contributors

---

<div align="center">

### Your Claude Code history deserves a home.

If ClaudeStudio gave your sessions a place to live, **drop a ⭐** — it's the single biggest thing that helps other developers find it.

[![Star on GitHub](https://img.shields.io/github/stars/ingridtoulotte/claudestudio?style=for-the-badge&logo=github&label=Star%20this%20repo&color=9a8cff)](https://github.com/ingridtoulotte/claudestudio/stargazers)

<sub>Built for the Claude Code community · 100% local · zero dependencies</sub>

</div>
