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
