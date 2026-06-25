"""CLAUDE.md generator (Feature 4, v0.5.2).

Every Claude Code user should have a ``CLAUDE.md`` — but most don't, because
writing one from scratch is effort. ClaudeStudio uniquely has the data to
*generate* one: it has watched you work. This module analyses a project's
indexed history and renders a ready-to-paste ``CLAUDE.md`` surfacing your most
-used tools, most-touched files, inferred stack, the errors you keep hitting,
and recurring prompt intents.

Everything is a deterministic read over the local index — no model calls, no
network. ``analyse_project`` returns a plain profile dict; ``render_claude_md``
turns it into Markdown. The same profile backs the MCP ``generate_project_brief``
tool, so a brief and a CLAUDE.md never disagree.
"""

from __future__ import annotations

import json
import re

# File-extension → human stack label. Inferred from the paths Claude touched, so
# the generated CLAUDE.md names the languages actually in play.
_EXT_STACK = {
    "py": "Python", "pyi": "Python", "ipynb": "Jupyter",
    "js": "JavaScript", "mjs": "JavaScript", "cjs": "JavaScript",
    "ts": "TypeScript", "tsx": "TypeScript/React", "jsx": "JavaScript/React",
    "vue": "Vue", "svelte": "Svelte",
    "rs": "Rust", "go": "Go", "rb": "Ruby", "php": "PHP",
    "java": "Java", "kt": "Kotlin", "swift": "Swift", "scala": "Scala",
    "c": "C", "h": "C/C++", "cpp": "C++", "cc": "C++", "hpp": "C++",
    "cs": "C#", "sql": "SQL", "sh": "Shell", "ps1": "PowerShell",
    "html": "HTML", "css": "CSS", "scss": "SCSS",
    "md": "Markdown", "json": "JSON", "toml": "TOML", "yaml": "YAML", "yml": "YAML",
}

_EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Update")
_PROMPT_NORMALISE = re.compile(r"\s+")


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _ext(path: str) -> str:
    base = _basename(path)
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def analyse_project(conn, project: str) -> dict:
    """Deterministic profile of one project from the index.

    `project` matches either the project path or its short name. Returns a dict
    with ``found`` False (and zeroed fields) when nothing matches, so callers
    never have to special-case a missing project.
    """
    from . import ask as ask_engine

    project = (project or "").strip()
    srows = conn.execute(
        "SELECT session_id, title, preview, project, project_name, "
        "       first_ts, last_ts, input_tokens, output_tokens, cost_usd, msg_count "
        "FROM sessions WHERE project = ? OR project_name = ?",
        (project, project),
    ).fetchall()
    if not srows:
        return {
            "project": project, "project_name": project, "found": False,
            "sessions": 0, "date_range": {"first": None, "last": None},
            "total_tokens": 0, "cost_usd": 0.0, "top_tools": [], "top_files": [],
            "tech_stack": [], "error_patterns": [], "prompt_patterns": [],
        }

    sids = [r["session_id"] for r in srows]
    placeholders = ",".join("?" * len(sids))
    project_name = srows[0]["project_name"] or srows[0]["project"] or project
    project_path = srows[0]["project"] or project

    total_tokens = sum((r["input_tokens"] or 0) + (r["output_tokens"] or 0) for r in srows)
    cost = sum(r["cost_usd"] or 0.0 for r in srows)
    first = min((r["first_ts"] for r in srows if r["first_ts"]), default=None)
    last = max((r["last_ts"] for r in srows if r["last_ts"]), default=None)

    # Top tools — straight from the tool_calls table for this project's sessions.
    top_tools = [
        {"name": r["name"], "calls": r["calls"]}
        for r in conn.execute(
            f"SELECT name, COUNT(*) calls FROM tool_calls "
            f"WHERE session_id IN ({placeholders}) "
            f"GROUP BY name ORDER BY calls DESC, name LIMIT 5",
            sids,
        )
    ]

    # Top files + inferred stack — parse paths out of edit-tool inputs.
    file_counts: dict[str, int] = {}
    ext_counts: dict[str, int] = {}
    edit_ph = ",".join("?" * len(_EDIT_TOOLS))
    for r in conn.execute(
        f"SELECT name, input_json FROM tool_calls "
        f"WHERE session_id IN ({placeholders}) AND name IN ({edit_ph})",
        (*sids, *_EDIT_TOOLS),
    ):
        try:
            inp = json.loads(r["input_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        for p in ask_engine.paths_in_tool(r["name"], inp):
            base = _basename(p)
            if not base:
                continue
            file_counts[base] = file_counts.get(base, 0) + 1
            ext = _ext(base)
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
    top_files = [
        {"file": f, "edits": n}
        for f, n in sorted(file_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]
    stack = []
    for ext, _n in sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        label = _EXT_STACK.get(ext)
        if label and label not in stack:
            stack.append(label)
    tech_stack = stack[:6]

    # Common pitfalls — the tools that error most often in this project.
    error_patterns = [
        {"tool": r["name"], "count": r["errors"]}
        for r in conn.execute(
            f"SELECT name, COUNT(*) errors FROM tool_calls "
            f"WHERE session_id IN ({placeholders}) AND is_error = 1 "
            f"GROUP BY name ORDER BY errors DESC, name LIMIT 5",
            sids,
        )
    ]

    # Recurring prompt intents — normalised first lines of user prompts that
    # show up across several sessions (a deterministic, project-local pattern).
    opener_counts: dict[str, int] = {}
    for r in srows:
        opener = _opener(r["preview"] or r["title"] or "")
        if opener:
            opener_counts[opener] = opener_counts.get(opener, 0) + 1
    prompt_patterns = [
        {"text": t, "count": n}
        for t, n in sorted(opener_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if n >= 2
    ][:5]

    return {
        "project": project_path, "project_name": project_name, "found": True,
        "sessions": len(sids),
        "date_range": {"first": first, "last": last},
        "total_tokens": total_tokens, "cost_usd": round(cost, 4),
        "top_tools": top_tools, "top_files": top_files, "tech_stack": tech_stack,
        "error_patterns": error_patterns, "prompt_patterns": prompt_patterns,
    }


def _opener(text: str) -> str:
    """First line of a prompt, whitespace-normalised and trimmed — a cheap,
    deterministic key for 'the same kind of thing I keep asking'."""
    first = (text or "").strip().splitlines()[0] if text and text.strip() else ""
    norm = _PROMPT_NORMALISE.sub(" ", first).strip().lower()
    return norm[:80]


def render_claude_md(profile: dict) -> str:
    """Render a project profile as a clean, paste-ready CLAUDE.md string."""
    name = profile.get("project_name") or "this project"
    lines: list[str] = [f"# CLAUDE.md — {name}", ""]
    lines.append(
        "<!-- Generated by ClaudeStudio from your indexed session history. "
        "Review and edit before committing. -->"
    )
    lines.append("")

    # --- Project Overview ---
    lines.append("## Project Overview")
    lines.append("")
    if profile.get("found"):
        dr = profile.get("date_range") or {}
        stack = ", ".join(profile.get("tech_stack") or []) or "not inferred"
        lines.append(f"- **Stack (observed):** {stack}")
        lines.append(f"- **Sessions analysed:** {profile.get('sessions', 0)}")
        if dr.get("first") and dr.get("last"):
            lines.append(f"- **Active:** {dr['first'][:10]} → {dr['last'][:10]}")
        lines.append(f"- **Total tokens:** {profile.get('total_tokens', 0):,}")
        lines.append(f"- **Approx. spend:** ${profile.get('cost_usd', 0.0):,.2f}")
    else:
        lines.append("- No indexed sessions for this project yet.")
    lines.append("")

    # --- Key Files ---
    lines.append("## Key Files")
    lines.append("")
    files = profile.get("top_files") or []
    if files:
        lines.append("The files this project's sessions touch most:")
        lines.append("")
        for f in files:
            lines.append(f"- `{f['file']}` — edited {f['edits']}×")
    else:
        lines.append("_No edited files detected yet._")
    lines.append("")

    # --- Conventions Observed ---
    lines.append("## Conventions Observed")
    lines.append("")
    tools = profile.get("top_tools") or []
    if tools:
        listed = ", ".join(f"`{t['name']}` ({t['calls']})" for t in tools)
        lines.append(f"- **Most-used tools:** {listed}")
    if profile.get("tech_stack"):
        lines.append(
            "- Keep changes idiomatic to the observed stack "
            f"({', '.join(profile['tech_stack'])})."
        )
    if not tools and not profile.get("tech_stack"):
        lines.append("_Not enough signal yet._")
    lines.append("")

    # --- Common Pitfalls ---
    lines.append("## Common Pitfalls")
    lines.append("")
    errs = profile.get("error_patterns") or []
    if errs:
        lines.append("Tools that have errored here (double-check these):")
        lines.append("")
        for e in errs:
            lines.append(f"- `{e['tool']}` — {e['count']} error(s) observed")
    else:
        lines.append("_No recurring tool errors observed — clean history._")
    lines.append("")

    # --- Preferred Patterns ---
    lines.append("## Preferred Patterns")
    lines.append("")
    pats = profile.get("prompt_patterns") or []
    if pats:
        lines.append("Recurring intents from your prompts:")
        lines.append("")
        for p in pats:
            lines.append(f"- {p['text']} _(×{p['count']})_")
    else:
        lines.append("_No recurring prompt intents detected yet._")
    lines.append("")

    return "\n".join(lines)
