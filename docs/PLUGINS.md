# Writing ClaudeStudio plugins

ClaudeStudio is hackable by design. A **plugin** lets you extend it — add an HTTP
route, an MCP tool, a CLI subcommand, or react to each newly-indexed session —
without forking the project. Plugins are additive foundations introduced in
v0.6.1: no breaking surface, fully opt-in.

> **Security model.** Plugins run with the same trust level as you. They execute
> in your local process, can read your index, and are loaded from a directory only
> you control. They are **localhost-only** and make no network calls on your behalf
> unless you write code that does. Only drop in plugins you have read and trust —
> exactly as you would a shell script in your `PATH`.

---

## Anatomy

A plugin is a single `.py` file in:

```
~/.claudestudio/plugins/
```

It may define **any** of these hooks (all optional):

| Hook | Signature | When it runs |
|------|-----------|--------------|
| `register_routes` | `register_routes(handler_class) -> None` | server startup — add HTTP routes |
| `register_mcp_tools` | `register_mcp_tools(tools_list) -> None` | MCP startup — append tool dicts |
| `register_cli_commands` | `register_cli_commands(subparsers) -> None` | CLI build — add subcommands |
| `on_session_indexed` | `on_session_indexed(db, session_id) -> None` | after each session is indexed |

Discovery scans `~/.claudestudio/plugins/*.py` at startup via `importlib.util`.
Files beginning with `_` are skipped, and load order is alphabetical.

**Isolation.** An exception while importing or running a plugin logs a warning and
is skipped — a broken plugin can never take ClaudeStudio down.

**Stdlib only.** Plugins should import only the standard library and
`claudestudio.*`. A third-party import is flagged with a warning (best-effort,
via `sys.stdlib_module_names` on Python 3.10+).

---

## Example 1 — a custom API route

`~/.claudestudio/plugins/my_analytics.py`:

```python
"""Adds GET /api/my-metric returning a custom number from the index."""
import json


def register_routes(handler_class):
    orig = handler_class.do_GET

    def do_GET(self):
        from urllib.parse import urlparse
        if urlparse(self.path).path == "/api/my-metric":
            conn = self._conn_ro()
            try:
                n = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            finally:
                conn.close()
            payload = json.dumps({"sessions_indexed": n}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        return orig(self)

    handler_class.do_GET = do_GET
```

Now `curl http://127.0.0.1:8787/api/my-metric` returns `{"sessions_indexed": 42}`.

---

## Example 2 — a custom CLI command (Obsidian export)

`~/.claudestudio/plugins/obsidian_export.py`:

```python
"""Adds `claudestudio obsidian-export --vault-dir ~/notes`."""
import os
from claudestudio import index, api


def register_cli_commands(subparsers):
    p = subparsers.add_parser("obsidian-export",
                              help="export sessions as Obsidian-linked Markdown")
    p.add_argument("--vault-dir", required=True)
    p.add_argument("--db", default=index.default_db_path())
    p.set_defaults(func=_run)


def _run(args):
    os.makedirs(args.vault_dir, exist_ok=True)
    conn = index.connect(args.db)
    try:
        for s in api.list_sessions(conn, {"limit": 500})["sessions"]:
            path = os.path.join(args.vault_dir, f"{s['session_id'][:12]}.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"# {s['title']}\n\nproject:: [[{s['project_name']}]]\n")
    finally:
        conn.close()
    print(f"  exported to {args.vault_dir}")
    return 0
```

> CLI command hooks require ClaudeStudio to call `plugin_loader.apply_cli_hooks()`
> while building its parser. The server already calls `apply_route_hooks()` at
> startup; CLI command registration is wired the same way.

---

## Loading and inspecting plugins

```bash
claudestudio doctor      # lists loaded plugins and any that failed to load
```

Programmatically:

```python
from claudestudio import plugin_loader
plugin_loader.load_plugins()          # returns list[LoadedPlugin]
plugin_loader.get_loaded_plugins()    # the singleton set
```

Each `LoadedPlugin` carries `name`, `path`, `module`, `hooks`, `error`, `warnings`
and an `ok` flag.

---

## Sharing plugins

There is no central registry — and that is the point. Share a plugin the
local-first way: paste it as a **GitHub Gist** and link it in
[Discussions](https://github.com/ingridtoulotte/claudestudio/discussions). Readers
review the code, drop it in `~/.claudestudio/plugins/`, and restart. Nothing is
uploaded, nothing auto-installs.

See also: [docs/API.md](API.md), [docs/MCP.md](MCP.md), [CONTRIBUTING.md](../CONTRIBUTING.md).
