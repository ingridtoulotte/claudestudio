# Jupyter notebook export

Export any session as a runnable `.ipynb` — perfect for sharing a reproducible
Claude Code workflow. nbformat v4 is plain JSON, so this needs **no dependencies**.

## CLI

```console
$ claudestudio export --format ipynb <session_id> --out session.ipynb
```

## What you get

- Each **user prompt** → a markdown cell beginning `> 💬 Prompt`.
- Each **assistant response** → a markdown cell with the text.
- Each **tool call** (bash, file read, edit) → a **code cell** whose source is the
  command/input and whose output carries the tool's result preview.
- Notebook metadata records `session_id`, `cost_usd`, `tokens` and `health_score`.

The cell count equals `user_turns + assistant_turns + tool_calls`.

## API

```
GET /api/session/{id}/export.ipynb
    → Content-Type application/json, downloads as <id>.ipynb
```

<!-- TODO screenshot: docs/screenshots/v070_notebook_export.png -->
