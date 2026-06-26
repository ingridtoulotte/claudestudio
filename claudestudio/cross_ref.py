"""Cross-session reference detection (Feature 2.2, v0.6.0).

People treat Claude Code as if it remembers: "as we did last time", "like in the
refactor session", "remember when you helped me with the parser". Those phrases
are a goldmine — they point at an *earlier* session the user is mentally linking
to. This module finds them and proposes the candidate sessions being referenced,
so the UI can offer a one-click jump to "the session you probably mean".

Pure read over the index, deterministic, zero dependencies. No model calls — the
phrase set is a fixed, auditable regex and the candidate ranking is a transparent
recency-and-overlap score.
"""

from __future__ import annotations

import re
import sqlite3

# Phrases that signal "I'm pointing at an earlier conversation". Ordered roughly
# strongest-signal first; each is matched case-insensitively as a whole-ish chunk.
# Kept deliberately conservative — a false positive costs a user a wasted glance,
# so we favour precision over recall.
_PHRASES = [
    r"as we did last time",
    r"like (?:we did )?last time",
    r"(?:like |as )?in the \w+(?:[ \-]\w+)? session",
    r"remember when you helped me",
    r"remember (?:when|that|how) we",
    r"(?:continue|continuing|pick up) (?:from |where )",
    r"same (?:as|approach as|way as) (?:before|last time|the \w+)",
    r"like (?:we|i) (?:talked about|discussed) (?:before|earlier|last time)",
    r"(?:as|like) (?:before|earlier|previously)",
    r"the (?:other|previous|last|earlier) (?:session|time|chat|conversation)",
    r"you helped me (?:with|fix|build|refactor|debug)",
    r"we (?:already )?(?:fixed|built|did|solved|refactored|set up) (?:this|that|it) (?:before|earlier|last time)",
    r"from (?:the )?(?:other|previous|last|earlier) (?:session|time)",
]
_PHRASE_RE = re.compile("|".join(f"(?:{p})" for p in _PHRASES), re.IGNORECASE)

# Cap the scan so a huge index stays cheap; newest user prompts first.
_SCAN_LIMIT = 4000
# How many candidate sessions to propose per reference.
_CANDIDATES = 3
# Stopwords stripped before keyword-overlap scoring of candidates.
_STOP = frozenset(
    ["the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with", "at", "by", "from", "this", "that", "it", "is", "was", "we", "i", "you", "me", "my", "our", "your", "they", "them", "then", "do", "did", "done", "can", "could", "would", "should", "please", "like", "as", "so", "if", "when", "how", "what", "which", "last", "time", "session", "again"]
)


def _keywords(text: str) -> set:
    return {
        w for w in re.findall(r"[a-z][a-z0-9_]{2,}", (text or "").lower())
        if w not in _STOP
    }


def matched_phrase(text: str) -> str | None:
    """The first reference phrase in `text`, normalized, or None. Public for tests."""
    m = _PHRASE_RE.search(text or "")
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(0).strip().lower())


def _candidate_sessions(conn, project: str, before_epoch: float, keywords: set) -> list[dict]:
    """Earlier sessions (same project first) that the reference might point at.

    Ranked by a transparent score: keyword overlap with the referring prompt,
    then recency. Only sessions strictly older than the referring one qualify, so
    a reference never "points forward".
    """
    rows = conn.execute(
        "SELECT session_id, title, project, last_ts, last_epoch, preview "
        "FROM sessions WHERE last_epoch < ? AND last_epoch > 0 "
        "ORDER BY last_epoch DESC LIMIT 400",
        (before_epoch,),
    ).fetchall()
    scored = []
    for r in rows:
        same_project = 1 if (project and r["project"] == project) else 0
        overlap = len(keywords & _keywords((r["title"] or "") + " " + (r["preview"] or "")))
        # recency rank is implicit in row order; encode as a small tiebreaker
        score = same_project * 3 + overlap
        if score <= 0 and not same_project:
            continue
        scored.append((score, r))
    scored.sort(key=lambda sr: (-sr[0], -(sr[1]["last_epoch"] or 0.0), sr[1]["session_id"]))
    out = []
    for score, r in scored[:_CANDIDATES]:
        out.append({
            "session_id": r["session_id"],
            "title": r["title"] or "Untitled",
            "last_ts": r["last_ts"] or "",
            "score": score,
        })
    return out


def find_cross_refs(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict]:
    """Find user prompts that reference an earlier session.

    Returns a deterministic list of ``{session_id, message_index, matched_phrase,
    text, candidate_sessions}`` — newest referring prompt first. ``message_index``
    is the 0-based seq inside the session (the timeline/replay coordinate), so the
    UI can deep-link straight to the prompt.
    """
    rows = conn.execute(
        "SELECT m.session_id, m.seq, m.text, "
        "       COALESCE(s.project,'') AS project, COALESCE(s.last_epoch,0) AS last_epoch "
        "FROM messages m JOIN sessions s USING(session_id) "
        "WHERE m.role='user' AND m.text<>'' "
        "ORDER BY s.last_epoch DESC, m.session_id, m.seq "
        "LIMIT ?",
        (_SCAN_LIMIT,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        phrase = matched_phrase(r["text"])
        if not phrase:
            continue
        kws = _keywords(r["text"])
        out.append({
            "session_id": r["session_id"],
            "message_index": r["seq"],
            "matched_phrase": phrase,
            "text": (r["text"] or "")[:240],
            "candidate_sessions": _candidate_sessions(
                conn, r["project"], r["last_epoch"] or 0.0, kws
            ),
        })
        if len(out) >= max(1, int(limit)):
            break
    return out
