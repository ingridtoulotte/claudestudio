# The Claude Code session format — and how to parse it

Claude Code records every session to a newline-delimited JSON file under
`~/.claude/projects/<encoded-project>/<session-id>.jsonl`. Each line is one JSON
record; a file is one session.

This document is a reference implementation's notes on that wire format, plus the
small, stable public API ClaudeStudio exposes so **you don't have to
reverse-engineer it yourself**. Both are zero-dependency, pure Python standard
library.

> The format is observed from real logs, not an official spec — it can change
> between Claude Code versions. The parser is deliberately tolerant: unknown
> record types and fields are ignored, malformed lines are skipped.

---

## Quick start

```python
from claudestudio import parse_session, iter_session_files, default_projects_root

for path in iter_session_files(default_projects_root()):
    s = parse_session(path)          # -> ParsedSession | None  (None = no messages)
    if not s:
        continue
    print(s.title, s.user_msgs, "prompts", round(s.cost_usd, 4), "USD")
    for m in s.messages:
        print(f"  [{m.role}] {m.text[:60]}  ({len(m.tool_calls)} tools)")
```

`parse_session(path)` is the documented entry point. (`parse_file` is the same
function under its original name; both are supported.)

---

## Public API

Importable directly from the top-level package:

| Name | What it is |
|---|---|
| `parse_session(path) -> ParsedSession \| None` | Parse one `.jsonl` session file. `None` if it has no messages or can't be read. |
| `parse_file(path)` | Alias of `parse_session`. |
| `iter_session_files(root) -> Iterable[str]` | Yield every `.jsonl` path under a projects root. |
| `default_projects_root() -> str` | `~/.claude/projects`. |
| `ParsedSession`, `Message`, `ToolCall` | The dataclasses below. |

### `ParsedSession`

| Field | Type | Notes |
|---|---|---|
| `session_id` | `str` | From the filename. |
| `file_path`, `file_mtime`, `file_size` | | Source file stats. |
| `title` | `str` | From an `ai-title` record, else first prompt. |
| `cwd` / `project` | `str` | Working directory of the session. |
| `git_branch`, `version`, `entrypoint` | `str` | Session metadata. |
| `first_ts`, `last_ts` | `str` | ISO-8601 timestamps. |
| `messages` | `list[Message]` | In file order. |
| `models` | `list[str]` | Distinct models seen. |

Derived properties: `user_msgs` (real prompts only — turns carrying only
`tool_result` blocks are excluded), `assistant_msgs`, `tool_call_count`,
`total_input`, `total_output`, `total_cache_write`, `total_cache_read`,
`cost_usd`, `duration_seconds`.

### `Message`

`uuid`, `parent_uuid`, `role` (`user`/`assistant`), `ts`, `seq`, `model`,
`text`, `thinking`, `tool_calls: list[ToolCall]`, the four token counts
(`input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens`),
`is_meta`, `is_sidechain`, `skill`, `plugin`, and a `cost_usd` property.

### `ToolCall`

`tool_use_id`, `name`, `input: dict`, `ts`, `is_error`, `result_preview`. The
result/error is back-filled from the matching `tool_result` block in a later
turn (paired by `tool_use_id`).

---

## The records

Each line has a `type`. The ones that matter:

| `type` | Meaning |
|---|---|
| `user` | A user turn. `message.content` is a string (a typed prompt) **or** a list of blocks (often just `tool_result` blocks returning tool output). |
| `assistant` | A model turn. `message.content` is a list of `text`, `thinking`, and `tool_use` blocks; `message.usage` holds token counts; `message.model` the model id. |
| `ai-title` | The auto-generated session title (`aiTitle`). |
| `system` | System events (e.g. `durationMs`, `subtype`). |
| other | `attachment`, `mode`, `permission-mode`, `last-prompt`, `file-history-snapshot`, … — session metadata, safely ignored by ClaudeStudio. |

### Content blocks (inside `message.content`)

- `{"type": "text", "text": …}` — visible message text.
- `{"type": "thinking", "thinking": …}` — extended-thinking content.
- `{"type": "tool_use", "id", "name", "input"}` — a tool invocation.
- `{"type": "tool_result", "tool_use_id", "is_error", "content"}` — the outcome of an earlier `tool_use`, delivered on a following `user` turn.

### `message.usage`

```json
{ "input_tokens": …, "output_tokens": …,
  "cache_creation_input_tokens": …, "cache_read_input_tokens": … }
```

These feed cost estimation. See [`pricing.py`](../claudestudio/pricing.py): an
auditable USD-per-1M-token table (cache writes bill at 1.25×, reads at 0.10×;
models with no public price are flagged and counted as `$0`, never guessed).

---

## Design notes for re-implementers

- **One file = one session.** Parse line by line; tolerate bad lines.
- **Pair tools across turns.** Keep a `tool_use_id -> ToolCall` map; attach
  `is_error`/result when the later `tool_result` arrives.
- **A prompt is text the user typed.** A `user` record whose content is only
  `tool_result` blocks is *not* a prompt — exclude it from prompt counts.
- **Metadata can appear on any record.** `cwd`, `gitBranch`, `version` may show
  up on the first record that has them; capture the first seen.

PRs that extend this reference are welcome — see
[CONTRIBUTING](../CONTRIBUTING.md).
