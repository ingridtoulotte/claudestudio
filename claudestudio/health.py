"""Session health score — a deterministic 0..100 read on how productive a
Claude Code session was (Feature 10, v0.5.2).

A health score answers "was this session productive, or did it go in circles?"
at a glance, without reading the whole thing. It is a weighted blend of four
signals, every one computed from the local index — no model calls, no network:

  * tool_success    (35%) — fraction of tool calls that did not error
  * error_density   (25%) — penalised by errors relative to message volume
  * token_efficiency(25%) — output tokens as a share of total, rewarding sessions
                            that produced rather than just consumed context
  * completion_signal(15%) — did the session end cleanly (assistant wrap-up),
                            mid-action (a tool call), or abandoned (a user turn)?

The score is cached on the `sessions.health_score` column at index time and
recomputed (with the component breakdown) on demand in the session detail view.
Pure functions throughout, so the self-test pins exact boundaries.
"""

from __future__ import annotations

from .parser import ParsedSession

# Component weights. They sum to 1.0 — keep it that way so `score` stays a clean
# 0..100 and the breakdown bars in the UI add up.
WEIGHTS = {
    "tool_success": 0.35,
    "error_density": 0.25,
    "token_efficiency": 0.25,
    "completion_signal": 0.15,
}

# Grade thresholds (inclusive lower bound), highest first. A session with no
# signal at all still grades — it just grades low.
_GRADES = [(90, "A"), (80, "B"), (65, "C"), (50, "D"), (0, "F")]
_LABELS = {
    "A": "Productive",
    "B": "Solid",
    "C": "Mixed",
    "D": "Choppy",
    "F": "Stalled",
}


def grade_for(score: int) -> str:
    """Map a 0..100 score to a letter grade."""
    for threshold, letter in _GRADES:
        if score >= threshold:
            return letter
    return "F"


def completion_signal_for(
    last_role: str | None, ended_with_tool: bool, last_had_error: bool
) -> float:
    """How cleanly the session ended.

    1.0  — last turn is an assistant wrap-up with no error (the work landed)
    0.5  — last turn is a tool call (mid-action; the session was cut off working)
    0.0  — last turn is a user prompt (abandoned: the user spoke last, unanswered)
    """
    if ended_with_tool:
        return 0.5
    if last_role == "assistant":
        return 0.0 if last_had_error else 1.0
    if last_role == "user":
        return 0.0
    return 0.5


def components(
    *,
    tool_calls: int,
    tool_errors: int,
    input_tokens: int,
    output_tokens: int,
    msg_count: int,
    completion_signal: float,
) -> dict:
    """The four 0..1 component scores. Every input is a plain aggregate so both
    the index writer (from a ParsedSession) and the detail view (from a stored
    row) compute identical numbers."""
    tool_calls = max(0, int(tool_calls))
    tool_errors = max(0, int(tool_errors))
    input_tokens = max(0, int(input_tokens))
    output_tokens = max(0, int(output_tokens))
    msg_count = max(0, int(msg_count))

    tool_success = 1.0 if tool_calls == 0 else (tool_calls - tool_errors) / tool_calls

    # Errors relative to conversation length. Five errors per message is a total
    # loss (0.0); a clean run is 1.0. Independent of tool_success so a session
    # that errored a lot but eventually recovered is still penalised here.
    density = tool_errors / msg_count if msg_count else 0.0
    error_density = max(0.0, 1.0 - density * 5.0)

    # Output as a share of all (non-cache) tokens. A session that mostly produced
    # tokens (code, answers) scores higher than one that only ingested context.
    total = input_tokens + output_tokens
    share = output_tokens / total if total else 0.0
    token_efficiency = min(1.0, share / 0.3)  # a 30% output share earns full marks

    return {
        "tool_success": round(tool_success, 4),
        "error_density": round(error_density, 4),
        "token_efficiency": round(token_efficiency, 4),
        "completion_signal": round(max(0.0, min(1.0, completion_signal)), 4),
    }


def score_from_components(comp: dict) -> int:
    """Weighted 0..100 score from the four component values."""
    total = sum(comp[k] * WEIGHTS[k] for k in WEIGHTS)
    return int(round(total * 100))


def compute(
    *,
    tool_calls: int,
    tool_errors: int,
    input_tokens: int,
    output_tokens: int,
    msg_count: int,
    completion_signal: float,
) -> dict:
    """Full health record: ``{score, grade, components, label}``."""
    comp = components(
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        msg_count=msg_count,
        completion_signal=completion_signal,
    )
    score = score_from_components(comp)
    grade = grade_for(score)
    return {"score": score, "grade": grade, "components": comp, "label": _LABELS[grade]}


def compute_health_score(ps: ParsedSession) -> dict:
    """Compute the health record for a freshly parsed session."""
    tool_errors = sum(1 for m in ps.messages for t in m.tool_calls if t.is_error)
    last = ps.messages[-1] if ps.messages else None
    if last is None:
        completion = 0.0
    else:
        last_had_error = any(t.is_error for t in last.tool_calls)
        completion = completion_signal_for(
            last.role, bool(last.tool_calls), last_had_error
        )
    return compute(
        tool_calls=ps.tool_call_count,
        tool_errors=tool_errors,
        input_tokens=ps.total_input,
        output_tokens=ps.total_output,
        msg_count=len(ps.messages),
        completion_signal=completion,
    )
