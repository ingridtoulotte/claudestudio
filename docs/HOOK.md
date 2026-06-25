# Auto-indexing with Claude Code Hooks

ClaudeStudio is only as useful as it is *current*. By default you refresh the
index with `claudestudio index` (or the **Sync** button in the app). The hook
integration removes that step entirely: **every time Claude Code finishes a
session, the index updates itself.**

```bash
claudestudio hook install
```

That's it. Open the app whenever you like and your latest sessions are already
there.

---

## What a Claude Code hook is

Claude Code can run a shell command when certain events happen. These *hooks*
live in your `~/.claude/settings.json` under a `hooks` key, grouped by event
name. ClaudeStudio wires a single command to the **`SessionEnd`** event:

```json
{
  "hooks": {
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "claudestudio index" } ] }
    ]
  }
}
```

`claudestudio index` is **incremental** — it only re-reads `.jsonl` files whose
modification time or size changed since the last run — so firing it on every
session end is cheap (typically well under a second).

---

## Commands

| Command | What it does |
|---------|--------------|
| `claudestudio hook install` | Adds the hook to `~/.claude/settings.json` and prints the exact JSON it wrote. |
| `claudestudio hook status` | Shows whether the hook is installed and when the index last ran. |
| `claudestudio hook uninstall` | Removes **only** our hook entry and prunes empty containers. Fully reversible. |

`claudestudio doctor` also reports hook status and nudges you to install it if
you haven't.

---

## How the merge is safe

- **Never clobbers.** Install reads your existing `settings.json`, keeps every
  other hook and event, and appends ours. Other `SessionEnd` commands are left
  in place.
- **Idempotent.** Installing twice does nothing the second time — there is never
  a duplicate entry.
- **Reversible.** Uninstall strips only the entry whose command is exactly
  `claudestudio index`, then removes the now-empty hook group / event key so the
  file returns to its original shape.
- **Tolerant.** A missing or malformed `settings.json` is treated as empty; the
  hook is laid on top of a clean object rather than throwing.

---

## Pair it with live updates

The hook keeps the index fresh; [`claudestudio watch`](../README.md#-live-updates)
(or the in-app Server-Sent-Events notification) tells the open app the moment it
changes:

```bash
# Terminal 1 — keep the app open
claudestudio serve

# Terminal 2 — optional: a foreground watcher that also reindexes on file change
claudestudio watch
```

With the hook installed you usually don't even need `watch` — the app's live
notification picks up the hook-driven reindex on its own and offers a one-click
refresh.

---

## Troubleshooting

- **`claudestudio` not found when the hook fires.** The hook runs `claudestudio
  index` via your shell. If ClaudeStudio isn't on the `PATH` Claude Code uses,
  edit the command in `settings.json` to an absolute path or to
  `python -m claudestudio index`.
- **Index didn't update.** Run `claudestudio hook status` — it shows the last
  index run time. Then run `claudestudio index` manually to confirm it works
  outside the hook.
- **I want it gone.** `claudestudio hook uninstall`. Your `settings.json` is left
  exactly as it was before install.
