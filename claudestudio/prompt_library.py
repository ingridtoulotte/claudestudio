"""Prompt Pattern Library intelligence (Feature 8, v0.5.2).

Power users evolve great prompting strategies, but those live scattered across
hundreds of sessions. This module turns them into a first-class, reusable asset:
it extracts the most *distinctive and reusable* prompt patterns from history
(reusing the trigram clustering in :mod:`claudestudio.patterns`) and scores how
reusable each one is, so the library surfaces the keepers and not the one-offs.

The library rows themselves (star/add/delete) live in the index — see
``index.upsert_prompt`` / ``list_prompts`` / ``delete_prompt``. This module is
the pure intelligence layer: scoring + extraction. Deterministic, no model calls.
"""

from __future__ import annotations

import hashlib
import re

from . import patterns

# Signals that a prompt is tied to one moment and won't generalise.
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\-]+[\\/][\w.\-/\\]+")  # a/b/c paths
_TIME_WORD_RE = re.compile(r"\b(today|yesterday|tomorrow|just now|earlier|this (?:session|morning|afternoon))\b", re.I)
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b")
_LONG_NUM_RE = re.compile(r"\b\d{4,}\b")  # ids, line counts, big magic numbers
# Instruction-like openers reward a prompt as a reusable template.
_IMPERATIVE = {
    "write", "add", "fix", "refactor", "explain", "implement", "create", "review",
    "test", "debug", "optimize", "optimise", "document", "summarize", "summarise",
    "generate", "build", "convert", "migrate", "update", "remove", "rename",
    "make", "find", "list", "check", "run", "analyze", "analyse",
}


def score_prompt_reusability(text: str) -> float:
    """0..1 estimate of how reusable a prompt is as a template.

    Penalises session-specific references (concrete file paths, timestamps,
    "today/yesterday", long numeric ids); rewards instruction-like structure (an
    imperative opener) and a sensible length. Deterministic.
    """
    t = (text or "").strip()
    if not t:
        return 0.0
    score = 0.5

    # Reward an imperative opening verb — the hallmark of a reusable instruction.
    first = re.split(r"[^\w]+", t.lower(), maxsplit=1)[0]
    if first in _IMPERATIVE:
        score += 0.25

    # Reward a workable length; punish trivially short and runaway-long prompts.
    n = len(t)
    if 25 <= n <= 240:
        score += 0.1
    elif n < 12 or n > 600:
        score -= 0.2

    # Penalise concrete, one-off references.
    if _PATH_RE.search(t):
        score -= 0.2
    if _TIME_WORD_RE.search(t):
        score -= 0.2
    if _DATE_RE.search(t):
        score -= 0.15
    if _LONG_NUM_RE.search(t):
        score -= 0.1

    return round(max(0.0, min(1.0, score)), 3)


def _stable_id(text: str) -> str:
    """A deterministic id for an extracted prompt so re-extraction updates the
    same row instead of duplicating it."""
    h = hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"ex-{h}"


def extract(conn, top_n: int = 50, *, min_count: int = 3) -> list[dict]:
    """Extract reusable prompt patterns from history, best first.

    Clusters recurring prompts (via :func:`patterns.extract_patterns`), scores
    each cluster's canonical text for reusability, and returns up to ``top_n``
    candidates as dicts ready for ``index.upsert_prompt``:
    ``{id, text, frequency, score, source, sessions}``.
    """
    clusters = patterns.extract_patterns(conn, min_count=min_count)
    out = []
    for cl in clusters:
        text = cl["canonical_text"]
        out.append({
            "id": _stable_id(text),
            "text": text,
            "frequency": cl["count"],
            "score": score_prompt_reusability(text),
            "source": "extracted",
            "sessions": cl.get("sessions", []),
        })
    # Most reusable first, then most frequent, for a stable, useful ordering.
    out.sort(key=lambda d: (-d["score"], -d["frequency"], d["text"]))
    return out[: max(0, int(top_n))]
