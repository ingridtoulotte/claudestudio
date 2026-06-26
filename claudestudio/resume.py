"""Context-rich session handoff brief (v0.6.2 — the "Insight Engine").

``claudestudio resume`` generates a copy-paste-ready prompt you drop into a *new*
Claude Code window to pick up exactly where a session left off. It is richer than
the one-paragraph ``narrative``: the last few tool calls and their outcomes, the
last few errors, any uncommitted files (via ``git status`` — gracefully skipped
outside a repo), the current branch/SHA, the open questions from the tail of the
session, and a structured ``CONTEXT FOR NEW SESSION`` block.

Deterministic except for the live ``git`` probe (which depends on the working
tree); everything derived from the session file is pure and identical on re-run.
No model calls, no network.
"""

from __future__ import annotations

import os
import re
import subprocess

from . import error_taxonomy

_EDIT_TOOLS = {
    "Edit", "MultiEdit", "Update", "str_replace_based_edit", "str_replace_editor",
    "Write", "create_file", "write_to_file", "NotebookEdit",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")
_QUESTION_RE = re.compile(r"[^.!?\n]*\?")


def _load(conn, session_id: str):
    from . import parser
    row = conn.execute(
        "SELECT file_path, project, git_branch, title FROM sessions WHERE session_id=?",
        (str(session_id),),
    ).fetchone()
    if not row:
        return None, None
    ps = parser.parse_file(row["file_path"]) if row["file_path"] else None
    return ps, row


def _last_tool_calls(ps, n: int = 3) -> list[dict]:
    flat = [t for m in ps.messages for t in m.tool_calls]
    out = []
    for t in flat[-n:]:
        preview = (t.result_preview or "").strip().replace("\n", " ")
        out.append({
            "name": t.name,
            "is_error": bool(t.is_error),
            "result": (preview[:160] + ("…" if len(preview) > 160 else "")),
        })
    return out


def _session_files(ps) -> list[str]:
    """Unique basenames edited/written in the session (first-seen order)."""
    seen: dict[str, None] = {}
    for m in ps.messages:
        for t in m.tool_calls:
            if t.name not in _EDIT_TOOLS:
                continue
            inp = t.input if isinstance(t.input, dict) else {}
            for k in _PATH_KEYS:
                v = inp.get(k)
                if isinstance(v, str) and v.strip():
                    base = v.strip().replace("\\", "/").rsplit("/", 1)[-1] or v.strip()
                    seen.setdefault(base, None)
                    break
    return list(seen.keys())


def _open_questions(ps, n_tail: int = 6) -> list[str]:
    """Question sentences from the tail of the session (most recent first)."""
    qs: list[str] = []
    for m in reversed(ps.messages[-n_tail:]):
        text = (m.text or "").strip()
        if not text:
            continue
        for match in _QUESTION_RE.findall(text):
            q = " ".join(match.split())
            if len(q) > 8 and q not in qs:
                qs.append(q)
    return qs[:3]


def git_state(project_path: str | None) -> dict:
    """Probe ``git`` for branch, short SHA and uncommitted files. All-None on any
    failure (not a repo, git missing, timeout) — never raises, never networks."""
    blank: dict = {"branch": None, "sha": None, "uncommitted": []}
    if not project_path or not os.path.isdir(project_path):
        return blank

    def _git(*cmd: str) -> str | None:
        try:
            r = subprocess.run(
                ["git", "-C", project_path, *cmd],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        return r.stdout.strip()

    inside = _git("rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return blank
    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or None
    sha = _git("rev-parse", "--short", "HEAD") or None
    status = _git("status", "--porcelain")
    files: list[str] = []
    if status:
        for line in status.splitlines():
            name = line[3:].strip() if len(line) > 3 else line.strip()
            if name:
                files.append(name)
    return {"branch": branch, "sha": sha, "uncommitted": files[:50]}


def build_brief(conn, session_id: str) -> dict:
    """Assemble the resume payload for one indexed session.

    Returns ``{brief, files_changed, open_questions, last_errors, branch, sha,
    title, tool_count, session_id}`` (or ``{error: ...}`` if the session/file is
    gone), where ``brief`` is the ready-to-paste text block.
    """
    ps, row = _load(conn, session_id)
    if row is None:
        return {"error": "not found", "session_id": session_id}
    if ps is None:
        return {"error": "session file unavailable", "session_id": session_id}

    title = ps.title or (row["title"] or "")
    last_tools = _last_tool_calls(ps)
    errors = error_taxonomy.extract_errors(ps)
    last_errors = [
        f"{e['tool_name'] or 'tool'} → {e['error_type']}: "
        f"{(e['error_text'] or '').strip()[:120]}"
        for e in errors[-3:]
    ]
    questions = _open_questions(ps)
    git = git_state(row["project"])
    files_changed = git["uncommitted"] or _session_files(ps)
    tool_count = ps.tool_call_count

    brief = _format_brief(
        title=title, project=row["project"], branch=git["branch"], sha=git["sha"],
        last_tools=last_tools, last_errors=last_errors, questions=questions,
        files_changed=files_changed, tool_count=tool_count,
    )
    return {
        "session_id": str(session_id),
        "title": title,
        "brief": brief,
        "files_changed": files_changed,
        "open_questions": questions,
        "last_errors": last_errors,
        "branch": git["branch"],
        "sha": git["sha"],
        "tool_count": tool_count,
    }


def _format_brief(*, title, project, branch, sha, last_tools, last_errors,
                  questions, files_changed, tool_count) -> str:
    lines: list[str] = []
    lines.append("=== CONTEXT FOR NEW SESSION ===")
    lines.append(f"Resuming work from a previous Claude Code session: {title or '(untitled)'}")
    lines.append("")
    lines.append(f"Project: {project or '(unknown)'}")
    if branch:
        loc = f"Git: branch {branch}"
        if sha:
            loc += f" @ {sha}"
        lines.append(loc)
    lines.append(f"Tool calls so far: {tool_count}")
    lines.append("")
    if files_changed:
        lines.append("Files in play:")
        for f in files_changed[:12]:
            lines.append(f"  - {f}")
        lines.append("")
    if last_tools:
        lines.append("Most recent actions:")
        for t in last_tools:
            mark = "✗" if t["is_error"] else "✓"
            note = f" — {t['result']}" if t["result"] else ""
            lines.append(f"  {mark} {t['name']}{note}")
        lines.append("")
    if last_errors:
        lines.append("Recent errors to be aware of:")
        for e in last_errors:
            lines.append(f"  - {e}")
        lines.append("")
    if questions:
        lines.append("Open questions / where I left off:")
        for q in questions:
            lines.append(f"  - {q}")
        lines.append("")
    lines.append("Please pick up from here. Start by confirming the current state, "
                 "then continue the work above.")
    lines.append("=== END CONTEXT ===")
    return "\n".join(lines)
