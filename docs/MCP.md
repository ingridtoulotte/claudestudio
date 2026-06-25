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
  "serverInfo":{"name":"claudestudio","version":"0.5.1"}}}

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
