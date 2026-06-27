# Context-window efficiency analyzer

See, turn by turn, how full Claude's context window was when the model was invoked
— a proxy for whether you're using the window efficiently or fragmenting work
across too many tiny sessions.

## How it works

- For each assistant turn, the context size ≈ `input_tokens + cache_read` (the
  tokens fed to the model that turn). `context_pct = 100 * context / model_limit`.
- Model limits are inferred from the model slug (all current Claude models are
  200K); unknown slugs default to 200K.
- Each turn gets an efficiency rating: `low` (<10%), `moderate` (<60%), `high`.
- A session's `waste_indicator` is true when >30% of turns use <10% of the window.
- The per-session average is stored in the derived `sessions.context_utilization_pct`
  column (backfilled on reindex).

## CLI

```console
$ claudestudio context-analysis <session_id> --last
  Context utilization — claude-opus-4-8 (limit 200,000 tokens)
  t  0 |###---------------------------|   9.6%
  t  1 |#-----------------------------|   3.6%
  avg 6.6%  peak 15.6%
```

`--json` emits the full per-turn breakdown.

## API

```
GET /api/session/{id}/context-analysis
    → {turns: [{turn_index, tokens_in, tokens_out, context_pct, model_limit,
                efficiency_rating}], avg_utilization_pct, peak_utilization_pct,
       waste_indicator}
GET /api/efficiency/context
    → {avg_utilization_pct, peak_utilization_pct, wasted_sessions: [...]}
```

## MCP

Tool **#35 `get_context_analysis(session_id)`**.

<!-- TODO screenshot: docs/screenshots/v070_context_analyzer.png -->
