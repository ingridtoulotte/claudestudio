"""Prompt effectiveness scoring (Feature 2.3, v0.6.0).

A blunt but honest question: *which of my prompts actually work?* For every user
message we compute a deterministic 0–100 "effectiveness" score from what happened
right after it:

  * **Tool success** — did the immediate assistant response run tool calls, and
    did they succeed? (a prompt that triggers a clean Read→Edit beats one that
    triggers nothing or a wall of errors)
  * **Productive continuation** — did the session keep producing output after this
    prompt? (token output in the following turns, log-scaled so one giant turn
    doesn't dominate)
  * **Error/retry penalty** — tool errors and retry-shaped follow-ups drag it down.
  * **Precision signal** — short, specific prompts are *reported* (not penalised):
    length is surfaced so the UI can show "tight vs rambling", but it never moves
    the score on its own.

Pure read over the index, deterministic, zero dependencies, no model calls. The
weights live in one table so the scoring is fully auditable and reproducible.
"""

from __future__ import annotations

import math
import re
import sqlite3

# Component weights (sum to 1.0). Surfaced so the score is explainable.
W_TOOL_SUCCESS = 0.45
W_CONTINUATION = 0.35
W_LOW_ERRORS = 0.20

# Output-token amount that counts as "fully productive" continuation (log-scaled).
_SATURATION_TOKENS = 4000.0
# Retry-shaped openers in the *next* user turn — a soft signal the prompt missed.
_RETRY_RE = re.compile(
    r"\b(that(?:'s| is)? (?:wrong|not right)|try again|no,? |still (?:broken|failing|"
    r"wrong)|didn'?t work|doesn'?t work|that broke|undo|revert that)\b",
    re.IGNORECASE,
)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def score_components(
    *,
    tool_calls: int,
    tool_errors: int,
    follow_output_tokens: int,
    retry_followup: bool,
) -> dict:
    """The three 0..1 component scores for one prompt. Pure, fully testable."""
    # no tools fired → neutral midpoint (neither rewarded nor punished hard)
    tool_success = _clamp01((tool_calls - tool_errors) / tool_calls) if tool_calls > 0 else 0.5
    continuation = _clamp01(
        math.log1p(max(0, follow_output_tokens)) / math.log1p(_SATURATION_TOKENS)
    )
    low_errors = 1.0
    if tool_calls > 0 and tool_errors > 0:
        low_errors = _clamp01(1.0 - tool_errors / tool_calls)
    if retry_followup:
        low_errors *= 0.4
    return {
        "tool_success": round(tool_success, 4),
        "continuation": round(continuation, 4),
        "low_errors": round(low_errors, 4),
    }


def score_from_components(comp: dict) -> int:
    """Blend the components into a 0..100 integer score."""
    raw = (
        W_TOOL_SUCCESS * comp["tool_success"]
        + W_CONTINUATION * comp["continuation"]
        + W_LOW_ERRORS * comp["low_errors"]
    )
    return int(round(_clamp01(raw) * 100))


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def score_prompt_at(conn: sqlite3.Connection, session_id: str, seq: int) -> dict | None:
    """Score the user prompt at (session_id, seq). None if there is no such prompt.

    Looks one assistant turn ahead for tool outcomes, sums output tokens of the
    turns until the *next* user prompt for the continuation signal, and inspects
    the next user prompt for a retry shape.
    """
    prompt = conn.execute(
        "SELECT seq, role, text FROM messages WHERE session_id=? AND seq=?",
        (session_id, seq),
    ).fetchone()
    if not prompt or prompt["role"] != "user":
        return None

    later = conn.execute(
        "SELECT seq, role, text, COALESCE(output_tokens,0) AS out "
        "FROM messages WHERE session_id=? AND seq>? ORDER BY seq",
        (session_id, seq),
    ).fetchall()

    follow_output = 0
    next_user_text = ""
    for m in later:
        if m["role"] == "user":
            next_user_text = m["text"] or ""
            break
        follow_output += m["out"] or 0

    tc = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(is_error),0) e FROM tool_calls "
        "WHERE session_id=? AND seq > ? AND seq <= ?",
        (session_id, seq, (later[0]["seq"] if later else seq)),
    ).fetchone()
    # tools fired by the immediate assistant turn (the first turn after the prompt)
    tool_calls = tc["n"] or 0
    tool_errors = tc["e"] or 0

    comp = score_components(
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        follow_output_tokens=follow_output,
        retry_followup=bool(_RETRY_RE.search(next_user_text)),
    )
    return {
        "session_id": session_id,
        "seq": seq,
        "score": score_from_components(comp),
        "components": comp,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "follow_output_tokens": follow_output,
        "word_count": _word_count(prompt["text"]),
    }


def effectiveness_for_text(conn: sqlite3.Connection, text: str, *, limit: int = 80) -> dict:
    """Aggregate effectiveness for every user prompt equal to `text` (trimmed).

    This is what the Prompt Library bar shows: a library prompt's text is matched
    against the history and the per-occurrence scores are averaged. Deterministic.
    """
    needle = (text or "").strip()
    if not needle:
        return {"count": 0, "avg_score": None, "samples": []}
    rows = conn.execute(
        "SELECT session_id, seq FROM messages "
        "WHERE role='user' AND TRIM(text)=? ORDER BY session_id, seq LIMIT ?",
        (needle, max(1, int(limit))),
    ).fetchall()
    samples = []
    for r in rows:
        sc = score_prompt_at(conn, r["session_id"], r["seq"])
        if sc:
            samples.append(sc)
    if not samples:
        return {"count": 0, "avg_score": None, "samples": []}
    avg = sum(s["score"] for s in samples) / len(samples)
    return {
        "count": len(samples),
        "avg_score": int(round(avg)),
        "samples": samples[:10],
    }
