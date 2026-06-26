# Capturing canonical screenshots

The README screenshots are generated from **synthetic** data so no private
session ever appears. Anyone can regenerate them.

## 1. Launch with demo data

```bash
claudestudio demo --serve
```

This builds a deterministic synthetic corpus (seeded — byte-identical every run)
and serves the app. Nothing real is touched.

## 2. Capture each view

Open each view and capture at a consistent window size (the README assets use
**1440×900**, dark theme):

| File (in `docs/screenshots/`) | View | How to reach it |
|---|---|---|
| `sessions.png` | Sessions list | default landing view |
| `replay.png` | Session replay | open any session |
| `search.png` | Search | `⌘K` / Ctrl K, type a query |
| `analytics.png` | Analytics | sidebar → Analytics |
| `efficiency.png` | Efficiency | sidebar → Efficiency |
| `prompts.png` | Prompt library | sidebar → Prompts |
| `patterns.png` | Patterns | sidebar → Patterns |
| `wrapped.png` | Wrapped | sidebar → Wrapped |

For a scripted capture you can drive headless Chrome/Edge:

```bash
# one unique --user-data-dir per shot avoids the singleton-profile collision
msedge --headless=new --user-data-dir="$(mktemp -d)" \
  --window-size=1440,900 --screenshot=docs/screenshots/sessions.png \
  "http://127.0.0.1:8787/#/sessions"
```

## 3. Conventions

- Dark theme, 1440×900, no personal data (always `demo --serve`).
- Keep filenames stable so the README references don't break.
- A missing screenshot is marked in the README with
  `<!-- SCREENSHOT NEEDED: description -->` so contributors know what to capture.
