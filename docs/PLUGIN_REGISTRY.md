# Community Plugin Registry

> ClaudeStudio v0.6.3+ — discover and install community plugins without copying
> files by hand.

The [plugin system](PLUGINS.md) lets you drop a `.py` file into
`~/.claudestudio/plugins/`. The **registry** is a curated index of community
plugins you can browse and install from the CLI.

## Quick start

```bash
claudestudio plugins update              # fetch + cache the registry
claudestudio plugins list                # see what's available (● installed, ○ available)
claudestudio plugins info slack_notify   # description, tags, author, source URL
claudestudio plugins install slack_notify  # download (with confirmation) into your plugins dir
claudestudio plugins remove slack_notify   # delete it again
```

Add `--yes` to `install` to skip the confirmation prompt in CI.

## Security model

Installing code from the internet is a serious action, so the registry is
deliberately strict:

1. **HTTPS only.** The registry JSON and every plugin source must be `https://`.
   A plain-HTTP URL is refused.
2. **Hardcoded host allowlist.** Plugin content may be fetched **only** from
   `raw.githubusercontent.com`. This is not configurable — an arbitrary URL in a
   registry entry is rejected even if the registry itself is otherwise valid. A
   redirect that lands off-host is also rejected.
3. **Explicit confirmation.** Before any download, the full URL is shown and you
   must confirm. `--yes` skips this (for CI only).
4. **Checksum verification.** If a registry entry includes a `sha256`, the
   downloaded bytes are hashed and compared **before** anything is written to
   disk. A mismatch aborts the install and writes nothing.
5. **No silent network.** Nothing is fetched until you run `plugins update` or
   `plugins install`. The server never fetches the registry on its own.
6. **Size cap.** Both the registry JSON and a plugin source are capped at 1 MB.

Plugins still run with your privileges once installed — review the source (the
`info` command prints the URL) before installing anything you don't trust.

## Registry format

The registry is a single JSON file hosted at
`https://raw.githubusercontent.com/ingridtoulotte/claudestudio/main/registry/plugins.json`:

```json
{
  "version": 1,
  "plugins": [
    {
      "name": "slack_notify",
      "description": "Post session summaries to a Slack webhook",
      "author": "community",
      "url": "https://raw.githubusercontent.com/ingridtoulotte/claudestudio/main/registry/plugins/slack_notify.py",
      "version": "1.0.0",
      "tags": ["notifications", "slack"],
      "sha256": "…optional but recommended…"
    }
  ]
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | ✅ | install name; the file becomes `<name>.py` |
| `description` | ✅ | one-line summary shown in `list` |
| `url` | ✅ | HTTPS `raw.githubusercontent.com` source URL |
| `author` | — | defaults to `community` |
| `version` | — | semver string |
| `tags` | — | array of strings for discovery |
| `sha256` | — | hex digest; when present, verified before writing |

## Seed plugins

Three working examples ship in [`registry/plugins/`](../registry/plugins):

- **`slack_notify`** — posts a one-line session summary to a Slack Incoming
  Webhook (`CLAUDESTUDIO_SLACK_WEBHOOK`) via the `on_session_indexed` hook.
- **`github_status`** — sets a GitHub commit status when a session runs in a git
  repo (`GITHUB_TOKEN` / `GITHUB_REPOSITORY` / `GITHUB_SHA`).
- **`ascii_report`** — registers `GET /api/ascii-report`, a plain-text table of
  today's sessions.

## Submitting a plugin

Open a **Plugin submission** issue (the form is at
`.github/ISSUE_TEMPLATE/plugin_submission.yml`). Plugins must be stdlib-only, have
no telemetry, and make no outbound network calls without explicit user config.

## API & MCP

- `GET /api/plugins/registry` — cached registry with installed status.
- `GET /api/plugins/installed` — installed plugin names.
- MCP tool `list_registry_plugins` — the registry with installed status.
- MCP tool `get_plugin_info` — full metadata for one plugin.
