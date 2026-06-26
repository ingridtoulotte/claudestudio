# Using ClaudeStudio from Claude Code

ClaudeStudio is built to *compose* with Claude Code — through hooks (keep the
index fresh automatically), the CLI, and an MCP server that lets Claude Code
query your own history.

## 1. Keep the index fresh — the SessionEnd hook

Install a `SessionEnd` hook so ClaudeStudio reindexes automatically every time a
Claude Code session finishes:

```bash
claudestudio hook install
```

This merges a hook into `~/.claude/settings.json` (safe + idempotent + reversible
— `claudestudio hook uninstall` removes exactly what it added and leaves any other
hooks untouched). Check it any time with `claudestudio hook status`.

Pair it with `claudestudio watch` (or the in-app live updates) and the workspace
stays current with zero manual syncing.

## 2. Register the MCP server

ClaudeStudio ships an MCP server (JSON-RPC 2.0 over stdio) that exposes your
indexed history to Claude Code as **read-only tools**. Register it in
`~/.claude.json` (or your MCP client config):

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

`claudestudio info` prints this snippet for you. The server makes **no model
calls and no network calls** — it only reads your local index.

### Example prompts

Once registered, ask Claude Code things like:

- *"Search my history for sessions about authentication."*
- *"What files did I touch most last week?"*
- *"Generate a CLAUDE.md for my backend project."*
- *"Which sessions discussed issue #412?"*
- *"What should I reopen next?"*

These route to ClaudeStudio's grounded tools (`search_sessions`, `ask_history`,
`generate_project_brief`, `find_sessions_by_github_ref`, `get_cross_refs`, …).
See [MCP.md](MCP.md) for the full tool list and input schemas.

## 3. The CLI

Everything the app does is also a command — handy in scripts and other agents:

```bash
claudestudio ask "what should I fix next?"
claudestudio search "race condition" --since 2026-06-01
claudestudio generate-claude-md --project my-api --out CLAUDE.md
claudestudio report --since 2026-06-01 --out sprint.html
claudestudio feed            # RSS/Atom URL for any reader
```

## 4. Onboarding in one command

New machine? `claudestudio init` walks you through indexing, the hook, a `watch`
helper, an optional budget, and a self-test — or run `claudestudio init --yes` to
accept every default non-interactively.
