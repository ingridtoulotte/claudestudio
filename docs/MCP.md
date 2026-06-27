# ClaudeStudio MCP Server

ClaudeStudio ships an [MCP](https://modelcontextprotocol.io) server so **Claude
Code can query your own session history**. Ask Claude things like _"find the
session where I fixed the tokenizer"_ or _"what did I spend on this project last
week?"_ and it answers from your local index — no model calls, no network, just
the same deterministic query layer the rest of the app uses.

## Quick start

```bash
# 1. Build an index (once; re-run any time to refresh)
claudestudio index

# 2. Launch the MCP server (JSON-RPC 2.0 over stdio)
claudestudio mcp
# or the dedicated entry point:
claudestudio-mcp
```

The server reads newline-delimited JSON-RPC requests on **stdin** and writes
responses on **stdout**. `stdout` is the protocol channel — the server prints
nothing else there, so it is safe to pipe directly into an MCP client.

## Register it with Claude Code

Add ClaudeStudio to your MCP configuration (e.g. `~/.claude.json`):

```json
{
  "mcpServers": {
    "claudestudio": {
      "command": "claudestudio-mcp",
      "args": []
    }
  }
}
```

If `claudestudio-mcp` is not on your `PATH` (e.g. you run from a clone), use the
module form instead:

```json
{
  "mcpServers": {
    "claudestudio": {
      "command": "python",
      "args": ["-m", "claudestudio", "mcp"]
    }
  }
}
```

Point at a specific index with `--db /path/to/index.db` if you don't use the
default (`~/.claudestudio/index.db`).

## Tools

All tools are **read-only**. Each returns a single text-content block holding a
JSON document.

| Tool | Arguments | Returns |
|------|-----------|---------|
| `search_sessions` | `query: str`, `limit?: int = 10` | Matching session summaries (BM25 + title match). |
| `get_session` | `session_id: str` | One session's metadata, token/cost totals, per-tool call counts. |
| `get_session_annotations` | `session_id: str` | The user's notes attached to a session. |
| `get_project_stats` | `project_name: str` | Aggregate stats for one project. |
| `get_analytics_summary` | `days?: int = 30` | All-time totals + a trailing window, plus per-model spend. |
| `find_sessions_by_file` | `file_path: str`, `limit?: int = 20` | Sessions whose tool calls referenced a file (by basename). |
| `get_recent_sessions` | `limit?: int = 5` | The most recently active sessions. |
| `ask_history` | `question: str`, `session_id?: str` | A grounded, cited answer computed locally (no model calls). |
| `list_bookmarks` | `session_id?: str` | Per-message bookmarks (starred moments). Optionally scope to one session. |
| `get_prompt_patterns` | `min_count?: int = 3` | Recurring prompt clusters — the prompt shapes you repeat — with counts. |
| `get_cost_by_period` | `period?: "daily"\|"weekly"\|"monthly" = monthly`, `n?: int = 6` | Spend, token totals and session counts for the last N calendar periods. *(v0.5.2)* |
| `get_diff_for_session` | `session_id: str`, `file_path?: str` | Every inline file diff in a session (old→new for each edit/write), optionally filtered to one file. *(v0.5.2)* |
| `get_annotations` | `session_id: str` | The user's inline annotations on a session (whole-session + per-message notes). *(v0.5.2)* |
| `generate_project_brief` | `project_id: str` | A full onboarding brief: sessions, spend, top files/tools, last activity, and an inferred CLAUDE.md profile. *(v0.5.2)* |
| `get_cross_refs` | `limit?: int = 50` | Prompts that reference an earlier session ("as we did last time") with candidate target sessions. *(v0.6.0)* |
| `find_sessions_by_github_ref` | `ref?: str`, `number?: int`, `repo?: str` | Sessions that discussed a GitHub issue/PR (`#123`, `owner/repo#456`). Read-only; no GitHub API calls. *(v0.6.0)* |
| `list_tags` | _(none)_ | All user-defined session tags with their session counts. *(v0.6.1)* |
| `get_session_tags` | `session_id: str` | The tags applied to a specific session (`id`, `name`, `colour`). *(v0.6.1)* |
| `get_session_narrative` | `session_id: str` | A deterministic narrative of a session: goal, approach, outcome, files changed, errors, recovery, next steps, quality. No model calls. *(v0.6.1)* |
| `get_file_heatmap` | `project_id?: str`, `since?: str`, `until?: str` | The top-10 hottest files with `heat_score`, `edit_count`, `session_count`. *(v0.6.1)* |
| `generate_resume_brief` | `session_id: str` | A copy-paste-ready brief to resume a session: last tools, recent errors, branch/SHA, open questions. *(v0.6.2)* |
| `compare_sessions` | `session_id_a: str`, `session_id_b: str` | Cost/token/health deltas, prompt overlap, shared files, and a plain-English verdict. *(v0.6.2)* |
| `get_error_taxonomy` | `project?: str`, `since?: str` | Error-type distribution across sessions, worst sessions, weekly trend. *(v0.6.2)* |
| `verify_claude_md` | `project_id: str` | Score a project's CLAUDE.md claims against its real session history. *(v0.6.2)* |
| `search_by_error_type` | `error_type: str`, `limit?: int = 20` | Sessions containing errors of a taxonomy type, most recent first. *(v0.6.2)* |
| `get_budget_forecast` | _(none)_ | End-of-month spend projection, biggest driver, sessions-until-limit, efficiency opportunity. *(v0.6.2)* |
| `get_onboarding_status` | _(none)_ | First-run signals: `tour_completed`, `hook_installed`, `sessions_indexed`, `budget_set`. *(v0.6.3)* |
| `list_registry_plugins` | _(none)_ | The community plugin registry with each plugin's installed status. *(v0.6.3)* |
| `get_plugin_info` | `name: str` | Full metadata for one registry plugin (description, tags, author, url). *(v0.6.3)* |
| `get_search_history` | `limit?: int = 20` | The user's recent searches, with result counts and timestamps. *(v0.6.3)* |
| `get_ai_session_summary` | `session_id: str` | AI summary of a session (goal, approach, quality, 3 improvement suggestions). Opt-in: requires `ANTHROPIC_API_KEY`, else a 402-style error. *(v0.7.0)* |
| `find_similar_sessions` | `session_id: str`, `top?: int = 10` | Sessions most similar to one, by local TF-IDF cosine similarity, with shared terms. *(v0.7.0)* |
| `get_session_clusters` | `k?: int = 8` | k-means topic clusters of all sessions, auto-labelled, with counts and avg cost/health. *(v0.7.0)* |
| `get_live_session_events` | `session_id: str`, `since_line?: int = 0` | New events appended to an active session's `.jsonl` since `since_line`. *(v0.7.0)* |
| `get_context_analysis` | `session_id: str` | Per-turn context-window utilization, efficiency ratings, and a waste indicator. *(v0.7.0)* |
| `get_model_analytics` | _(none)_ | Cost/tokens/health/tool-success by model, plus a model recommendation. *(v0.7.0)* |
| `export_annotations` | _(none)_ | All annotations as a portable JSON payload. *(v0.7.0)* |
| `import_annotations` | `data: object`, `strategy?: "merge"\|"replace"` | Import an annotation payload into the local index. *(v0.7.0)* |

### v0.6.1 tool examples

**Schemas.** `list_tags` → `{"tags":[{id,name,colour,created_at,session_count}]}`.
`get_session_tags` → `{"session_id","tags":[{id,name,colour,created_at}]}`.
`get_session_narrative` → `{headline,goal,approach,outcome,files_changed,
errors_encountered,recovery,next_steps,quality,word_count,session_id}`.
`get_file_heatmap` → `{"files":[{path,edit_count,session_count,heat_score}],
"total_files"}`.

`get_session_narrative` — auto-draft a PR description from your last session:

```jsonc
{"jsonrpc":"2.0","id":7,"method":"tools/call",
 "params":{"name":"get_session_narrative","arguments":{"session_id":"<id>"}}}
// ← {"headline":"✅ Successful: Refactor auth module…","goal":"…","outcome":"…", …}
```

`get_file_heatmap` — "which files am I touching most this week?":

```jsonc
{"jsonrpc":"2.0","id":8,"method":"tools/call",
 "params":{"name":"get_file_heatmap","arguments":{"since":"2026-06-20"}}}
// ← {"files":[{"path":"src/auth.py","edit_count":28,"heat_score":0.94}, …]}
```

### v0.5.2 tool examples

`get_cost_by_period` — "how much did I spend this week?" answered from local logs:

```jsonc
// →
{"jsonrpc":"2.0","id":5,"method":"tools/call",
 "params":{"name":"get_cost_by_period","arguments":{"period":"weekly","n":4}}}
// ← text content holding {"period":"weekly","periods":[
//    {"period":"2026-W24","sessions":7,"cost_usd":3.81,"tokens":1840221}, …]}
```

`get_diff_for_session` — show exactly what a session changed:

```jsonc
// →
{"jsonrpc":"2.0","id":6,"method":"tools/call",
 "params":{"name":"get_diff_for_session","arguments":{"session_id":"…","file_path":"parser.py"}}}
// ← text content holding {"session_id":"…","file_path":"parser.py","diffs":[
//    {"seq":14,"tool":"Edit","file":"parser.py","diff":"--- a/parser.py\n+++ b/parser.py\n@@ …","truncated":false}]}
```

`get_annotations` — recall the notes you attached:

```jsonc
// →
{"jsonrpc":"2.0","id":7,"method":"tools/call",
 "params":{"name":"get_annotations","arguments":{"session_id":"…"}}}
// ← text content holding {"session_id":"…","annotations":[
//    {"id":3,"session_id":"…","message_idx":-1,"note":"the race-condition fix","created_at":…,"updated_at":…}]}
```

`generate_project_brief` — get up to speed on a project in one call:

```jsonc
// →
{"jsonrpc":"2.0","id":8,"method":"tools/call",
 "params":{"name":"generate_project_brief","arguments":{"project_id":"orbit-api"}}}
// ← text content holding {"project_name":"orbit-api","found":true,"sessions":23,
//    "cost_usd":14.2,"top_files":[…],"top_tools":[…],"tech_stack":["Python", …],
//    "last_activity":"2026-06-24T…","profile":{…}}
```

### Example invocations

`list_bookmarks` — every starred message, newest first:

```jsonc
// →
{"jsonrpc":"2.0","id":3,"method":"tools/call",
 "params":{"name":"list_bookmarks","arguments":{}}}
// ← text content holding {"bookmarks":[{"id":"…","session_id":"…",
//    "session_title":"…","seq":12,"note":"the actual fix","created_epoch":…}]}
```

`get_prompt_patterns` — the prompts you ask again and again:

```jsonc
// →
{"jsonrpc":"2.0","id":4,"method":"tools/call",
 "params":{"name":"get_prompt_patterns","arguments":{"min_count":3}}}
// ← text content holding {"patterns":[{"pattern_id":"p1",
//    "canonical_text":"write unit tests for …","count":12,
//    "sessions":["…"],"last_seen_epoch":…,"similarity_score":0.87}]}
```

## Protocol

Standard MCP / JSON-RPC 2.0. The server implements `initialize`, `tools/list`,
`tools/call`, and `ping`; it ignores the `notifications/initialized` ack.

```jsonc
// → initialize
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
// ← capabilities + serverInfo
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2024-11-05",
  "capabilities":{"tools":{"listChanged":false}},
  "serverInfo":{"name":"claudestudio","version":"0.6.3"}}}

// → call a tool
{"jsonrpc":"2.0","id":2,"method":"tools/call",
 "params":{"name":"search_sessions","arguments":{"query":"tokenizer","limit":5}}}
// ← text content holding the JSON result
{"jsonrpc":"2.0","id":2,"result":{
  "content":[{"type":"text","text":"{\"query\":\"tokenizer\",\"sessions\":[…]}"}],
  "isError":false}}
```

A tool that fails (e.g. an unknown `session_id`) returns a normal result with
`"isError": true` and an `{"error": "…"}` payload, rather than a protocol-level
error — so the client can show the message without the call being treated as a
transport failure. Genuine protocol problems (unknown method, missing tool name)
use JSON-RPC error codes (`-32601`, `-32602`).

## Try it from a shell

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_recent_sessions","arguments":{"limit":3}}}' \
  | claudestudio mcp
```

## Design notes

- **Zero dependencies.** JSON-RPC is just structured JSON on stdin/stdout.
- **Local-first.** Every tool is a pure read over the local SQLite index. Nothing
  leaves your machine; `ask_history` is computed deterministically, not by a model.
- **Reuses one query layer.** The MCP tools call the same `api`/`analytics`/`ask`
  functions that back the HTTP API and CLI, so behaviour can't drift between
  surfaces — and the self-test exercises the MCP dispatch directly.
