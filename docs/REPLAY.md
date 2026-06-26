# Time-Machine Replay

ClaudeStudio replays a session the way it actually unfolded — message by message,
tool call by tool call — so you can *watch* a debugging session instead of
scrolling a wall of text. Open any session and use the replay bar at the top.

## Controls

| Control | What it does |
|---|---|
| ⏮ Restart | Jump back to the very first message |
| ◀ / ▶ | Step one message back / forward |
| ⏯ Play / pause | Auto-advance through the session |
| ⚠ Jump to first error | Skip straight to the first tool error or exception trace |
| Track | Click anywhere to scrub to that point |
| Speed pills | `0.5×` · `1×` · `2×` · `5×` · `∞` |

### Speed control

The speed is a pill-segmented control next to the transport buttons. `∞` reveals
the whole session instantly; the slower speeds pace the reveal by the real gap
between messages (a 4-second think shows as a brief pause, a 6-minute gap is
capped so you never wait long).

During auto-advance the current message's text is revealed with a CSS-only
**typewriter** animation (it respects `prefers-reduced-motion` — no animation if
you've asked the OS to reduce motion).

### Jump to first error

The **⚠** button (disabled when a session had no errors) scrubs to just after the
first message that contains a tool error or an exception/traceback. It's the
fastest way to get to "where it went wrong" in a long session.

### Session summary card

When playback reaches the end, a summary card appears with the totals —
messages, prompts, tool calls, errors, and output tokens — a clean wrap-up of
what the session accomplished.

## Keyboard shortcuts

The replay is fully keyboard-operable (the timeline slider has
`role="slider"` and is focusable):

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `←` / `→` | Step back / forward |
| `<` / `>` | Slower / faster |
| `e` | Jump to first error |
| `Home` / `End` | First / last message |
| `J` / `K` | Move the read cursor down / up |

All of replay is client-side over data already fetched for the session view — no
extra requests, no model calls.
