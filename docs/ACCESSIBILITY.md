# Accessibility

ClaudeStudio targets **WCAG 2.1 AA**. This page documents what's in place and how
to verify it.

## Keyboard

- Every view is reachable and operable from the keyboard; no action requires a
  mouse.
- The replay **timeline slider** has `role="slider"`, is focusable (`tabindex`),
  and responds to `←` / `→` (step) and `Home` / `End` (first / last).
- Replay transport: `Space` play/pause, `<` / `>` speed, `e` jump-to-error.
- Global: `⌘K` / `Ctrl K` command palette, `?` keyboard cheat-sheet,
  `Shift+D` developer view.
- A visible **focus ring** (`:focus-visible`, 2px brand outline) is shown for
  keyboard users on every interactive element.

## Screen readers & semantics

- Landmark roles: `navigation`, `main`, `complementary`.
- Every icon-only `<button>` has an `aria-label` (enforced by a check in
  `selftest.py` that parses `index.html` and fails CI if any button lacks an
  accessible name).
- The live-update toast host is `role="status"` with `aria-live="polite"`, so new
  sessions and confirmations are announced.
- The document `<title>` updates per route (e.g. *"Session: my-project —
  ClaudeStudio"*), so history and screen-reader navigation announce where you are.
- The replay summary and developer status use `role="status"` / `aria-live`.

## Color & motion

- Text/background pairs target the 4.5:1 contrast ratio; muted text tokens are
  tuned to stay legible on the dark surface.
- `color-scheme` is declared so form controls and scrollbars match the theme.
- All animations (typewriter reveal, fades, spinners) are gated behind
  `@media (prefers-reduced-motion: reduce)`.

## Verifying

- `python -m claudestudio --selftest` includes the button-accessible-name audit
  and checks the a11y wiring (landmark roles, `role="status"`, per-route title).
- For a manual pass, run `claudestudio demo --serve` and navigate the whole app
  with the keyboard only, then with a screen reader.

Found a gap? Please [open an issue](https://github.com/ingridtoulotte/claudestudio/issues)
— accessibility regressions are treated as bugs.
