"""Prompt-to-outcome tracing (v0.6.2 — the "Insight Engine").

For any user prompt, trace the full causal chain it set off: which tools ran, in
what order, which files changed, what errors occurred, and what the final
assistant message said. Surfaced as a collapsible "Trace" tree in the replay
view — not a raw log dump. Deterministic: assembled purely from the parsed
session, no model calls.
"""

from __future__ import annotations

_EDIT_TOOLS = {
    "Edit", "MultiEdit", "Update", "str_replace_based_edit", "str_replace_editor",
    "Write", "create_file", "write_to_file", "NotebookEdit",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")


def _load(conn, session_id: str):
    from . import parser
    row = conn.execute(
        "SELECT file_path FROM sessions WHERE session_id=?", (str(session_id),)
    ).fetchone()
    if not row or not row["file_path"]:
        return None
    return parser.parse_file(row["file_path"])


def _is_prompt(m) -> bool:
    return m.role == "user" and not m.is_meta and bool(m.text)


def _files_of(tc) -> list[str]:
    if tc.name not in _EDIT_TOOLS:
        return []
    inp = tc.input if isinstance(tc.input, dict) else {}
    for k in _PATH_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return [v.strip().replace("\\", "/").rsplit("/", 1)[-1] or v.strip()]
    return []


def _find_prompt_index(msgs, message_idx: int) -> int:
    """Index into ``msgs`` of the prompt to trace: the message at ``message_idx``
    if it is a real prompt, else the nearest real prompt at or before it, else the
    first prompt in the session. Returns -1 if there are no prompts at all."""
    by_seq = {m.seq: i for i, m in enumerate(msgs)}
    if message_idx in by_seq and _is_prompt(msgs[by_seq[message_idx]]):
        return by_seq[message_idx]
    # nearest prompt at or before the requested seq
    best = -1
    for i, m in enumerate(msgs):
        if _is_prompt(m) and m.seq <= message_idx:
            best = i
    if best >= 0:
        return best
    for i, m in enumerate(msgs):
        if _is_prompt(m):
            return i
    return -1


def trace(conn, session_id: str, message_idx: int = 0) -> dict:
    """Build the causal trace tree rooted at one prompt.

    Returns ``{session_id, prompt, tools, files, errors, outcome, empty}``. An
    empty/promptless session returns ``empty=True`` with zeroed lists.
    """
    ps = _load(conn, session_id)
    empty: dict = {
        "session_id": str(session_id), "prompt": None, "tools": [],
        "files": [], "errors": [], "outcome": "", "empty": True,
    }
    if ps is None or not ps.messages:
        return empty

    try:
        idx = int(message_idx)
    except (TypeError, ValueError):
        idx = 0
    root = _find_prompt_index(ps.messages, idx)
    if root < 0:
        return empty

    prompt_msg = ps.messages[root]
    tools: list[dict] = []
    files: list[str] = []
    errors: list[dict] = []
    outcome = ""
    # walk forward until the next real prompt — that span is this prompt's reach
    for m in ps.messages[root + 1:]:
        if _is_prompt(m):
            break
        for t in m.tool_calls:
            tfiles = _files_of(t)
            tools.append({
                "name": t.name,
                "is_error": bool(t.is_error),
                "files": tfiles,
                "result": (t.result_preview or "").strip().replace("\n", " ")[:160],
            })
            for f in tfiles:
                if f not in files:
                    files.append(f)
            if t.is_error:
                from . import error_taxonomy
                errors.append({
                    "tool_name": t.name,
                    "error_type": error_taxonomy.classify_error(t.result_preview, t.name),
                    "text": (t.result_preview or "").strip()[:160],
                })
        if m.role == "assistant" and m.text:
            outcome = " ".join(m.text.split())[:280]

    return {
        "session_id": str(session_id),
        "prompt": {"seq": prompt_msg.seq,
                   "text": " ".join((prompt_msg.text or "").split())[:280]},
        "tools": tools,
        "files": files,
        "errors": errors,
        "outcome": outcome,
        "empty": False,
    }
