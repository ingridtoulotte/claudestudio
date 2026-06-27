"""GitHub Actions session summary (Feature 5, v0.6.3).

A standalone, zero-dependency module that turns one Claude Code session JSONL
file into a compact Markdown summary — cost, tokens, tool success rate, health
score, top files changed, first/last prompt. Teams run Claude Code in CI and
want a digest posted as a PR comment; this prints the Markdown to stdout so a
workflow can pipe it into ``$GITHUB_OUTPUT`` / a comment action.

Runs in CI, never inside the server. Reuses the same deterministic parser,
pricing and health logic as the rest of the app — no model calls, no network.
"""

from __future__ import annotations

import json

from . import health, parser

# Tool calls that change files — used to surface "top files changed".
_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def _changed_files(ps: parser.ParsedSession) -> list[tuple[str, int]]:
    counts: dict = {}
    for m in ps.messages:
        for t in m.tool_calls:
            if t.name not in _EDIT_TOOLS:
                continue
            inp = t.input if isinstance(t.input, dict) else {}
            path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
            if path:
                counts[str(path)] = counts.get(str(path), 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _first_last_prompt(ps: parser.ParsedSession) -> tuple[str, str]:
    prompts = [m.text.strip() for m in ps.messages
               if m.role == "user" and not m.is_meta and m.text.strip()]
    if not prompts:
        return "", ""
    return prompts[0], prompts[-1]


def _one_line(text: str, width: int = 120) -> str:
    s = " ".join((text or "").split())
    return (s[:width] + "…") if len(s) > width else s


def summarize_session(ps: parser.ParsedSession) -> str:
    """A compact Markdown digest of a parsed session."""
    h = health.compute_health_score(ps)
    tool_success = h["components"]["tool_success"]
    tokens = ps.total_input + ps.total_output + ps.total_cache_write + ps.total_cache_read
    first, last = _first_last_prompt(ps)
    files = _changed_files(ps)

    lines = ["### 🧠 ClaudeStudio session summary", ""]
    title = ps.title or "Untitled session"
    lines.append(f"**{_one_line(title, 80)}**")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Cost | ${ps.cost_usd:.4f} |")
    lines.append(f"| Tokens | {tokens:,} |")
    lines.append(f"| Messages | {len(ps.messages):,} |")
    lines.append(f"| Tool calls | {ps.tool_call_count:,} |")
    lines.append(f"| Tool success | {tool_success * 100:.0f}% |")
    lines.append(f"| Health | {h['score']}/100 ({h['grade']}) |")
    lines.append("")
    if files:
        lines.append("**Top files changed**")
        lines.append("")
        for path, n in files[:3]:
            lines.append(f"- `{path}` ({n} edit{'s' if n != 1 else ''})")
        lines.append("")
    if first:
        lines.append(f"**First prompt:** {_one_line(first)}")
        lines.append("")
    if last and last != first:
        lines.append(f"**Last prompt:** {_one_line(last)}")
        lines.append("")
    lines.append("<sub>Generated locally by "
                 "[ClaudeStudio](https://github.com/ingridtoulotte/claudestudio) — "
                 "no model calls.</sub>")
    return "\n".join(lines).rstrip() + "\n"


def summarize_path(path: str) -> str:
    """Parse a session JSONL at `path` and return its Markdown summary.

    A missing/empty/unparseable file yields a clear one-line fallback rather than
    raising — a CI step should degrade to a note, never fail the build.
    """
    try:
        ps = parser.parse_session(path)
    except (OSError, ValueError, json.JSONDecodeError):
        ps = None
    if ps is None or not ps.messages:
        return ("### 🧠 ClaudeStudio session summary\n\n"
                f"_No readable session at `{path}`._\n")
    return summarize_session(ps)
