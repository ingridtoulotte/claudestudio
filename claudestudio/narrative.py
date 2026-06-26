"""Smart session narrative — a one-paragraph, human-readable story of a session.

A ClaudeStudio session is dense; a *narrative* answers "what actually happened
here?" in plain English: what was asked, what Claude did, what changed, what
errors arose and how they resolved, and what is left open. It is **deterministic**
— pure heuristics over the parsed session, no model calls — so it is fast, free,
and identical on every run. Perfect for stand-up notes and PR descriptions.

Usage::

    from claudestudio import parser
    from claudestudio.narrative import generate_narrative
    n = generate_narrative(parser.parse_file(path))
    print(n["headline"])     # "✅ Successful: Refactor auth module to use JWT…"
"""

from __future__ import annotations

import re

# Tools that mutate a file on disk — used to derive `files_changed`. Mirrors the
# set api.tool_diff recognises, inlined to keep this module import-light.
_EDIT_TOOLS = {
    "Edit", "MultiEdit", "Update", "str_replace_based_edit", "str_replace_editor",
    "Write", "create_file", "write_to_file", "NotebookEdit",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
_NEXT_STEP_RE = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|next step|still need|left to do|remaining|"
    r"follow[- ]?up|not yet|to be done)\b",
    re.IGNORECASE,
)

_QUALITY = {
    "successful": ("✅", "Successful"),
    "partial": ("⚠️", "Partial"),
    "abandoned": ("⛔", "Abandoned"),
    "exploratory": ("🔍", "Exploratory"),
}


def generate_narrative(session, health_score: int | None = None) -> dict:
    """Return a structured narrative dict for one parsed session.

    `session` is a :class:`claudestudio.parser.ParsedSession`. `health_score`
    (0..100) may be supplied from the cached column; when omitted it is computed.
    The returned dict is JSON-serialisable and stable across runs.
    """
    msgs = list(getattr(session, "messages", []) or [])
    prompts = [m for m in msgs if m.role == "user" and not m.is_meta and m.text]

    goal = _goal_text(prompts)
    tool_calls = [t for m in msgs for t in m.tool_calls]
    total_tools = len(tool_calls)
    errors = sum(1 for t in tool_calls if t.is_error)
    files_changed = _files_changed(msgs)

    if health_score is None:
        from . import health
        health_score = health.compute_health_score(session)["score"]

    ends_mid = _ends_mid_tool_call(msgs)
    unanswered = _has_unanswered_prompt(msgs)
    thinking_ratio = _thinking_ratio(msgs)
    quality = _classify(health_score, errors, ends_mid, unanswered,
                         total_tools, thinking_ratio)

    recovery = _recovery(msgs) if errors else None
    next_steps = _next_steps(msgs)

    emoji, label = _QUALITY[quality]
    short_goal = goal[:60] + ("…" if len(goal) > 60 else "")
    headline = f"{emoji} {label}: {short_goal}" if goal else f"{emoji} {label} session"
    approach = _approach(tool_calls, total_tools, session)
    outcome = _outcome(total_tools, errors, files_changed, ends_mid, unanswered)

    parts = [headline, goal, approach, outcome, recovery or "", next_steps or ""]
    word_count = sum(len(p.split()) for p in parts)

    return {
        "headline": headline,
        "goal": goal,
        "approach": approach,
        "outcome": outcome,
        "files_changed": files_changed,
        "errors_encountered": errors,
        "recovery": recovery,
        "next_steps": next_steps,
        "quality": quality,
        "word_count": word_count,
    }


# ---------------------------------------------------------------------------
# heuristics
# ---------------------------------------------------------------------------

def _goal_text(prompts) -> str:
    """First user prompt, stripped of code blocks, trimmed to 200 chars."""
    if not prompts:
        return ""
    raw = prompts[0].text or ""
    raw = _CODE_FENCE.sub(" ", raw)
    raw = _INLINE_CODE.sub(lambda m: m.group(0).strip("`"), raw)
    raw = " ".join(raw.split())
    return raw[:200].strip()


def _files_changed(msgs) -> list[str]:
    """Unique file basenames touched by edit/create tool calls, first-seen order."""
    seen: dict[str, None] = {}
    for m in msgs:
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


def _approach(tool_calls, total_tools, session) -> str:
    """'Used Read, Edit, Bash across 12 tool calls over 8m.'"""
    if not total_tools:
        return "No tools were used — a conversational session."
    counts: dict[str, int] = {}
    for t in tool_calls:
        counts[t.name] = counts.get(t.name, 0) + 1
    top = sorted(counts, key=lambda k: (-counts[k], k))[:3]
    dur = _human_duration(getattr(session, "duration_seconds", 0.0) or 0.0)
    return (f"Used {', '.join(top)} across {total_tools} tool "
            f"call{'s' if total_tools != 1 else ''} over {dur}.")


def _outcome(total_tools, errors, files_changed, ends_mid, unanswered) -> str:
    bits: list[str] = []
    if total_tools:
        succ = total_tools - errors
        bits.append(f"{succ}/{total_tools} tool calls succeeded")
        if errors:
            bits.append(f"{errors} error{'s' if errors != 1 else ''} encountered")
    if files_changed:
        n = len(files_changed)
        bits.append(f"{n} file{'s' if n != 1 else ''} changed")
    sentence = ("; ".join(bits) + ".") if bits else "A short session with no edits."
    if ends_mid:
        sentence += " The session ended mid-action."
    elif unanswered:
        sentence += " It ended on an open prompt awaiting a reply."
    else:
        sentence += " It ended on a completed response."
    return sentence[0].upper() + sentence[1:]


def _recovery(msgs):
    """If an error happened, did a later tool call succeed? 'Recovered via Bash'.

    Finds the last erroring tool call, then the first clean tool call that comes
    after it (in the same message, after the error, or any later message). That
    next success is the recovery.
    """
    flat = [(mi, ti, t) for mi, m in enumerate(msgs)
            for ti, t in enumerate(m.tool_calls)]
    last_err = max((pos for pos, (_, _, t) in enumerate(flat) if t.is_error),
                   default=-1)
    if last_err < 0:
        return None
    for _, _, t in flat[last_err + 1:]:
        if not t.is_error:
            return f"Recovered via {t.name}."
    return None


def _next_steps(msgs):
    """Scan the last 3 messages for an explicit TODO / open-question pattern."""
    for m in reversed(msgs[-3:]):
        text = (m.text or "")
        match = _NEXT_STEP_RE.search(text)
        if match:
            line = _line_around(text, match.start())
            return line[:200].strip()
    return None


def _line_around(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    return " ".join(text[start:end].split())


def _ends_mid_tool_call(msgs) -> bool:
    """True if the last message is an assistant turn whose tool calls never got a
    result back (no following message) — the session stopped mid-action."""
    if not msgs:
        return False
    last = msgs[-1]
    return last.role == "assistant" and bool(last.tool_calls)


def _has_unanswered_prompt(msgs) -> bool:
    """True if the very last message is a real user prompt with no reply after."""
    if not msgs:
        return False
    last = msgs[-1]
    return last.role == "user" and not last.is_meta and bool(last.text)


def _thinking_ratio(msgs) -> float:
    think = sum(len(m.thinking or "") for m in msgs)
    text = sum(len(m.text or "") for m in msgs)
    total = think + text
    return (think / total) if total else 0.0


def _classify(health, errors, ends_mid, unanswered, total_tools, thinking_ratio) -> str:
    """Bucket the session. Precedence: abandoned → exploratory → successful → partial."""
    if ends_mid:
        return "abandoned"
    if total_tools < 5 and thinking_ratio > 0.5:
        return "exploratory"
    if health >= 70 and not unanswered:
        return "successful"
    return "partial"


def _human_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    h, rem = divmod(s, 3600)
    return f"{h}h{rem // 60:02d}m"


# ---------------------------------------------------------------------------
# DB-backed convenience  (loads the session file, then narrates)
# ---------------------------------------------------------------------------

def narrative_for_session(conn, session_id: str) -> dict:
    """Narrate one indexed session by id. Reads its source file and the cached
    health score. Returns ``{"error": …}`` if the session or its file is gone."""
    from . import parser
    row = conn.execute(
        "SELECT file_path, health_score FROM sessions WHERE session_id=?",
        (str(session_id),),
    ).fetchone()
    if not row:
        return {"error": "not found", "session_id": session_id}
    ps = parser.parse_file(row["file_path"]) if row["file_path"] else None
    if ps is None:
        return {"error": "session file unavailable", "session_id": session_id}
    out = generate_narrative(ps, health_score=row["health_score"])
    out["session_id"] = session_id
    return out
