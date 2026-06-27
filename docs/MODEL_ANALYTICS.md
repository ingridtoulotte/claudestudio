# Per-model analytics

A side-by-side breakdown of cost, speed and quality by Claude model version — and a
history-grounded recommendation for which model fits short vs. long tasks.

## CLI

```console
$ claudestudio model-stats
  model                        sessions    total $    avg $  health  tool ok
  claude-opus-4-8                    10    10.4836   1.0484    77.1    0.979
  claude-sonnet-4-6                  10     6.3704   0.6370    76.9    0.985
  claude-haiku-4-5                   14     3.6602   0.2614    76.6    0.962

  For short tasks, claude-haiku-4-5 costs $0.2614/session on average; for demanding
  work, claude-opus-4-8 scores highest on health. Match the model to the task size.
```

`--json` emits raw JSON.

## Metrics per model

`session_count`, `total_cost_usd`, `avg_cost_usd`, `avg_health_score`,
`avg_tokens_per_session`, `tool_success_rate` (1 − errored/total tool calls), and
`sessions_this_month`. Sorted by total spend.

## API

```
GET /api/analytics/models
    → {models: [{model, session_count, total_cost_usd, avg_cost_usd,
                 avg_health_score, avg_tokens_per_session, tool_success_rate,
                 sessions_this_month}], recommendation}
```

A dependency-free SVG bar chart is available via `model_analytics.svg_bar_chart()`.

## MCP

Tool **#36 `get_model_analytics()`**.

<!-- TODO screenshot: docs/screenshots/v070_model_stats.png -->
