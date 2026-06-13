# Contributing to ClaudeStudio

Thanks for wanting to make ClaudeStudio better. The bar is simple: it should stay
**fast, local, zero-dependency, and screenshot-worthy**.

## Ground rules

- **No runtime dependencies.** Python standard library only, on the backend; vanilla
  JS + CSS on the frontend (no framework, no build step). If you think you need a
  dependency, open an issue first — the answer is usually "we can do it with stdlib".
- **Local-first, always.** No telemetry, no outbound network calls, no cloud. The
  server binds to `127.0.0.1`.
- **Deterministic.** Pricing and aggregations must be reproducible and covered by the
  self-test.

## Dev loop

```bash
# 1. Correctness — must print ALLPASS
python -m claudestudio --selftest

# 2. Build a synthetic corpus and iterate on the UI (no real data touched)
python -m claudestudio demo --serve

# 3. Diagnose anything weird
python -m claudestudio doctor
```

There is nothing to install and nothing to build. Edit a file, refresh the page.

## Project layout

```
claudestudio/
  pricing.py     model prices (the one place to edit costs)
  parser.py      .jsonl  -> normalized ParsedSession (no I/O policy)
  index.py       SQLite schema + FTS5 + incremental indexing
  analytics.py   aggregations for Analytics / Timeline / Projects
  wrapped.py     Claude Wrapped generator
  api.py         HTTP-agnostic handlers (what the tests exercise)
  server.py      http.server: JSON API + static SPA
  cli.py         command-line interface
  fixtures.py    deterministic synthetic data (selftest + demo)
  selftest.py    exact-assertion checks, zero deps
web/
  index.html · styles.css · app.js   the single-page app
```

## Adding a check

Every behavioral change should come with a check in `selftest.py`. Use the tiny
`Check` helper (`eq`, `close`, `ok`) and assert exact numbers against a fixture in
`fixtures.py`. CI runs the self-test on Windows, macOS, and Linux across several
Python versions — keep it green.

## Style

- Match the surrounding code. Comments explain *why*, not *what*.
- Keep functions in `api.py` pure (connection in, JSON-able out) so they stay testable.
- New views should feel as polished as the existing ones. Every screen is a screenshot.
