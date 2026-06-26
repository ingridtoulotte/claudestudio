"""Prompt-pattern extraction — surface the prompts you keep re-typing.

Power users repeat prompt *shapes*: "write tests for X", "explain Y", "refactor
Z". This module clusters near-identical user prompts so the app can offer a
personal prompt library — "you've asked this 12 times, here's a reusable one."

Pure read over the index, deterministic, zero dependencies. Similarity is
word-trigram Jaccard — the same measure the Highlights "recurring prompts"
detector uses (:func:`claudestudio.highlights._trigrams`) — so two surfaces never
disagree about what counts as "the same prompt".
"""

from __future__ import annotations

import sqlite3

from . import parser
from .highlights import _trigrams

# Two prompts belong to the same pattern at or above this trigram-Jaccard overlap.
PATTERN_MIN_SIMILARITY = 0.6
# A pattern needs at least this many prompts to be worth surfacing (override per call).
PATTERN_MIN_COUNT = 3
# Cap the pairwise scan to the most recent N prompts so it stays cheap.
PATTERN_SCAN_LIMIT = 600
# Never return more than this many patterns.
PATTERN_TOP = 50


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _prompts(conn) -> list[tuple]:
    """(session_id, text, last_epoch, trigrams) for recent user prompts.

    One row per substantive user message (real typed prompt: role='user', has
    text). Joined to the session for the activity timestamp used as last-seen.
    """
    rows = conn.execute(
        "SELECT m.session_id, m.text, COALESCE(s.last_epoch,0) AS last_epoch "
        "FROM messages m JOIN sessions s USING(session_id) "
        "WHERE m.role='user' AND m.text<>'' "
        "ORDER BY s.last_epoch DESC, m.session_id, m.seq "
        "LIMIT ?",
        (PATTERN_SCAN_LIMIT,),
    ).fetchall()
    out = []
    for r in rows:
        text = (r["text"] or "").strip()
        g = _trigrams(text)
        if g:
            out.append((r["session_id"], text, r["last_epoch"] or 0.0, g))
    return out


def extract_patterns(conn: sqlite3.Connection, *, min_count: int = PATTERN_MIN_COUNT) -> list[dict]:
    """Cluster recurring prompts and return them, biggest first.

    Greedy single-link clustering: each unused prompt seeds a cluster and absorbs
    every later prompt whose trigram-Jaccard overlap with the seed is at least
    ``PATTERN_MIN_SIMILARITY``. Clusters smaller than ``min_count`` are dropped.

    Each pattern: ``{pattern_id, canonical_text, count, sessions, last_seen_epoch,
    similarity_score}``. ``canonical_text`` is the longest prompt in the cluster
    (the most complete phrasing); ``similarity_score`` is the mean overlap of the
    members with that seed. Capped at the top ``PATTERN_TOP`` by count.
    """
    items = _prompts(conn)
    n = len(items)
    used = [False] * n
    clusters: list[dict] = []
    for i in range(n):
        if used[i]:
            continue
        seed_sid, seed_text, seed_epoch, gi = items[i]
        members = [(i, 1.0)]
        for j in range(i + 1, n):
            if used[j]:
                continue
            jac = _jaccard(gi, items[j][3])
            if jac >= PATTERN_MIN_SIMILARITY:
                members.append((j, jac))
        if len(members) < max(2, min_count):
            continue
        for idx, _ in members:
            used[idx] = True
        texts = [items[idx][1] for idx, _ in members]
        sessions, seen = [], set()
        last_seen = 0.0
        for idx, _ in members:
            sid = items[idx][0]
            if sid not in seen:
                seen.add(sid)
                sessions.append(sid)
            last_seen = max(last_seen, items[idx][2])
        canonical = max(texts, key=len)
        sim = sum(s for _, s in members) / len(members)
        clusters.append({
            "pattern_id": f"p{len(clusters) + 1}",
            "canonical_text": canonical,
            "count": len(members),
            "sessions": sessions,
            "last_seen_epoch": last_seen,
            "similarity_score": round(sim, 3),
        })
    clusters.sort(key=lambda c: (-c["count"], -c["similarity_score"], c["canonical_text"]))
    # renumber after the sort so ids are stable top-down
    for k, c in enumerate(clusters[:PATTERN_TOP], start=1):
        c["pattern_id"] = f"p{k}"
    return clusters[:PATTERN_TOP]


# ===========================================================================
# Session pattern mining (Feature 2.6, v0.6.0) — workflows, debug loops,
# time-of-day, project momentum. All pure reads over the index, deterministic.
# ===========================================================================

# A workflow is a window of this many consecutive tool calls...
WORKFLOW_MIN_LEN = 2
WORKFLOW_MAX_LEN = 4
# ...that recurs at least this many times across all sessions to count.
WORKFLOW_MIN_COUNT = 3
WORKFLOW_TOP = 5
# A debugging loop is the same tool fired at least this many times back-to-back.
DEBUG_LOOP_MIN = 3


def _tool_sequences(conn) -> dict[str, list[str]]:
    """Per-session ordered list of tool names (insertion order = id)."""
    rows = conn.execute(
        "SELECT session_id, name FROM tool_calls ORDER BY session_id, id"
    ).fetchall()
    seqs: dict[str, list[str]] = {}
    for r in rows:
        seqs.setdefault(r["session_id"], []).append(r["name"])
    return seqs


def recurring_workflows(conn: sqlite3.Connection, *, min_count: int = WORKFLOW_MIN_COUNT,
                        top: int = WORKFLOW_TOP) -> list[dict]:
    """Top recurring tool-sequence workflows (≥`min_count` occurrences).

    Slides a 2..4-tool window over every session's tool order and counts identical
    windows across the whole index. Longer windows win ties (more specific), and a
    window that's merely a prefix/suffix of an already-chosen longer one is
    dropped so the list reads as distinct workflows. Each entry:
    ``{steps, count, sessions, length}``.
    """
    seqs = _tool_sequences(conn)
    counts: dict[tuple, dict] = {}
    for sid, names in seqs.items():
        for length in range(WORKFLOW_MIN_LEN, WORKFLOW_MAX_LEN + 1):
            for i in range(len(names) - length + 1):
                window = tuple(names[i:i + length])
                # skip a window that's just one tool repeated — that's a debug loop
                if len(set(window)) == 1:
                    continue
                slot = counts.setdefault(window, {"count": 0, "sessions": set()})
                slot["count"] += 1
                slot["sessions"].add(sid)
    candidates = [
        {"steps": list(w), "count": d["count"],
         "sessions": sorted(d["sessions"]), "length": len(w)}
        for w, d in counts.items() if d["count"] >= max(2, min_count)
    ]
    candidates.sort(key=lambda c: (-c["count"], -c["length"], c["steps"]))
    chosen: list[dict] = []
    for cand in candidates:
        joined = ">".join(cand["steps"])
        if any(joined in ">".join(c["steps"]) for c in chosen):
            continue  # subsumed by an already-chosen longer workflow
        chosen.append(cand)
        if len(chosen) >= top:
            break
    return chosen


def debug_loops(conn: sqlite3.Connection, *, min_repeat: int = DEBUG_LOOP_MIN,
                top: int = 10) -> list[dict]:
    """Runs where one tool fired ≥`min_repeat` times in a row (a debugging loop).

    The classic Edit→Bash→Edit→Bash grind shows up as a long run of one tool.
    Each entry: ``{session_id, tool, length}``, longest run first.
    """
    seqs = _tool_sequences(conn)
    loops: list[dict] = []
    for sid, names in seqs.items():
        i = 0
        n = len(names)
        while i < n:
            j = i
            while j + 1 < n and names[j + 1] == names[i]:
                j += 1
            run = j - i + 1
            if run >= min_repeat:
                loops.append({"session_id": sid, "tool": names[i], "length": run})
            i = j + 1
    loops.sort(key=lambda x: (-x["length"], x["session_id"], x["tool"]))
    return loops[:top]


_HOUR_EMOJI = [
    "🌙", "🌙", "🌙", "🌙", "🌅", "🌅", "🌅", "☀️", "☀️", "☀️", "☀️", "☀️",
    "🌤", "🌤", "🌤", "🌤", "🌆", "🌆", "🌆", "🌃", "🌃", "🌙", "🌙", "🌙",
]


def time_of_day(conn: sqlite3.Connection, *, top: int = 3) -> list[dict]:
    """Most productive hours (local), as a ranked list with emoji context."""
    counts = [0] * 24
    for r in conn.execute("SELECT last_epoch FROM sessions WHERE last_epoch>0"):
        d = parser.local_datetime(r["last_epoch"])
        if d is None:
            continue
        counts[d.hour] += 1
    ranked = sorted(range(24), key=lambda h: (-counts[h], h))
    out = []
    for h in ranked[:top]:
        if counts[h] == 0:
            break
        out.append({
            "hour": h,
            "label": f"{h:02d}:00–{(h + 1) % 24:02d}:00",
            "sessions": counts[h],
            "emoji": _HOUR_EMOJI[h],
        })
    return out


def project_momentum(conn: sqlite3.Connection, *, weeks: int = 4) -> list[dict]:
    """Per-project session frequency over the last `weeks` weeks vs the prior span.

    A project is ``rising`` when its recent half has more sessions than its older
    half, ``stalling`` when it has fewer (and the older half wasn't empty), else
    ``steady``. Deterministic — buckets by ISO week off the stored epoch.
    """
    rows = conn.execute(
        "SELECT project, project_name, last_epoch FROM sessions WHERE last_epoch>0"
    ).fetchall()
    if not rows:
        return []
    newest = max((r["last_epoch"] for r in rows), default=0.0)
    span = max(1, weeks) * 7 * 86400
    half = span / 2.0
    cutoff = newest - span
    mid = newest - half
    agg: dict[str, dict] = {}
    for r in rows:
        e = r["last_epoch"] or 0.0
        if e < cutoff:
            continue
        key = r["project"] or ""
        a = agg.setdefault(key, {
            "project": key, "project_name": r["project_name"] or key,
            "recent": 0, "older": 0,
        })
        if e >= mid:
            a["recent"] += 1
        else:
            a["older"] += 1
    out = []
    for a in agg.values():
        if a["recent"] > a["older"]:
            momentum = "rising"
        elif a["recent"] < a["older"] and a["older"] > 0:
            momentum = "stalling"
        else:
            momentum = "steady"
        a["momentum"] = momentum
        a["total"] = a["recent"] + a["older"]
        out.append(a)
    out.sort(key=lambda x: (-(x["recent"] - x["older"]), -x["total"], x["project"]))
    return out
