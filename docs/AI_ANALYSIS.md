# AI Analysis (opt-in)

ClaudeStudio is 100% local by default and makes **zero model calls**. The AI
analysis layer is the *only* feature that can reach the network, and only when you
explicitly set `ANTHROPIC_API_KEY`. With no key, every entry point returns a clear
402-style message and nothing leaves your machine.

## What it does

| Command | Purpose |
|---|---|
| `claudestudio ai-summary <session_id> [--last]` | Goal, approach, quality, what worked / didn't, and 3 concrete improvement suggestions for one session. |
| `claudestudio ai-coach [-n 20]` | A personal coaching report over your recent sessions — your top recurring inefficiency patterns and how to fix them. |
| `claudestudio ai-prompt "<raw prompt>"` | Rewrites a prompt for better Claude Code results, with a projected effectiveness delta. |

Flags: `--copy` (clipboard), `--out FILE` (write to disk), `--json` (raw JSON).

## Model & cost

- Default model: `claude-haiku-4-5-20251001` (cheapest, fastest).
- Every call's token usage and cost (computed at public Anthropic prices) is
  recorded in the `ai_usage` table.
- Summaries are **cached** — a re-fetch is instant and free.
- Rate limited to **one real call per session per hour**.

## API

```
GET  /api/ai/status
     → {enabled, model, api_key_set, total_ai_calls, total_ai_cost_usd}

GET  /api/session/{id}/ai-summary
     → {summary, coaching_tips, improvement_suggestions, model_used, tokens_used, cost_usd}
     → HTTP 402 {error: "ANTHROPIC_API_KEY not set", status: 402} when no key
```

## MCP

Tool **#31 `get_ai_session_summary(session_id)`** — same payload, usable from Claude
Code itself.

## Privacy

The request targets a fixed `https://api.anthropic.com/v1/messages` host via
`urllib` (standard library). It is reached *only* when a key is present. No key →
no network, ever.

<!-- TODO screenshot: docs/screenshots/v070_ai_insights.png -->
