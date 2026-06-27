"""Local semantic session search — zero-dependency TF-IDF vectors.

Pure standard library: no embeddings API, no sentence-transformers, no faiss.
Every session is reduced to a sparse TF-IDF term vector (top-200 terms) stored in
the ``session_vectors`` table; similarity is plain cosine over those sparse dicts.

The vectors are *derived* data — safe to ``DELETE FROM session_vectors`` and
rebuild without losing anything a user owns.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
import sqlite3
from typing import Any

# A compact, hand-picked set of English stop words. Kept inline (no data file) so
# the module stays self-contained. ~100 of the highest-frequency tokens that carry
# no topical signal in a coding session.
STOPWORDS = frozenset(["a", "an", "the", "and", "or", "but", "if", "then", "else", "of", "to", "in", "on", "at", "by", "for", "with", "from", "into", "over", "under", "again", "further", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "doing", "this", "that", "these", "those", "it", "its", "as", "so", "not", "no", "nor", "only", "own", "same", "than", "too", "very", "can", "will", "just", "i", "you", "he", "she", "they", "we", "me", "him", "her", "them", "my", "your", "his", "their", "our", "us", "my", "mine", "ok", "yes", "what", "which", "who", "whom", "whose", "where", "when", "why", "how", "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "here", "there", "about", "above", "below", "up", "down", "out", "off", "again", "once", "also", "got", "get", "would", "could", "should", "may", "might", "must", "shall", "how's", "let's", "would've", "i'm", "i've", "you're", "it's"])

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-word boundaries, drop stop words and noise.

    Tokens shorter than 2 chars or purely numeric are dropped, as are stop words.
    """
    if not text:
        return []
    out = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) < 2 or tok.isdigit() or tok in STOPWORDS:
            continue
        out.append(tok)
    return out


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _session_texts(conn) -> dict[str, str]:
    """Combined user+assistant text per session id (the corpus documents)."""
    docs: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT session_id, text FROM messages WHERE text IS NOT NULL"  # SAFE: no params
    ):
        if not row["text"]:
            continue
        docs.setdefault(row["session_id"], []).append(row["text"])
    return {sid: " ".join(parts) for sid, parts in docs.items()}


def compute_vectors(texts: dict[str, str]) -> dict[str, dict[str, float]]:
    """Pure TF-IDF over a ``{session_id: text}`` corpus (top-200 terms each).

    No I/O — the deterministic maths shared by the persisting and on-demand paths.
    """
    if not texts:
        return {}
    toks = {sid: tokenize(t) for sid, t in texts.items()}
    n_docs = sum(1 for v in toks.values() if v)
    if n_docs == 0:
        return {}
    df: dict[str, int] = {}
    for terms in toks.values():
        for term in set(terms):
            df[term] = df.get(term, 0) + 1

    out: dict[str, dict[str, float]] = {}
    for sid, terms in toks.items():
        if not terms:
            continue
        counts: dict[str, int] = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        length = len(terms)
        weights: dict[str, float] = {}
        for term, cnt in counts.items():
            tf = cnt / length
            idf = math.log(n_docs / (1 + df.get(term, 0)))
            w = tf * idf
            if w > 0:
                weights[term] = w
        out[sid] = dict(sorted(weights.items(), key=lambda kv: kv[1],
                               reverse=True)[:200])
    return out


def build_vectors(conn, *, rebuild: bool = False) -> int:
    """(Re)compute TF-IDF vectors and store the top-200 terms per session.

    Incremental by default: only sessions without a stored vector are computed.
    Pass ``rebuild=True`` to recompute every session. Returns how many vectors
    were written this call. A no-op on a read-only connection.
    """
    texts = _session_texts(conn)
    if not texts:
        return 0
    existing = {r["session_id"] for r in conn.execute(
        "SELECT session_id FROM session_vectors")}  # SAFE: no params
    if not rebuild:
        texts = {sid: t for sid, t in texts.items() if sid not in existing}
        if not texts:
            return 0
    # IDF must be computed over the full corpus, even for an incremental write.
    vectors = compute_vectors(_session_texts(conn)) if not rebuild else compute_vectors(texts)
    written = 0
    now = _now_iso()
    try:
        for sid in texts:
            vec = vectors.get(sid)
            if not vec:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO session_vectors(session_id, vector_json, updated_at) "
                "VALUES(?,?,?)",  # SAFE: parameterized
                (sid, json.dumps(vec), now),
            )
            written += 1
        conn.commit()
    except sqlite3.OperationalError:
        return 0  # read-only connection: persistence is best-effort
    return written


def vectors(conn) -> dict[str, dict[str, float]]:
    """Every session's vector — stored where available, computed in memory for
    any session that has text but no stored vector. Never writes (read-only safe).
    """
    stored = load_vectors(conn)
    texts = _session_texts(conn)
    missing = [sid for sid in texts if sid not in stored]
    if missing:
        # recompute the whole corpus in memory (IDF is global) and fill gaps
        computed = compute_vectors(texts)
        for sid in missing:
            if computed.get(sid):
                stored[sid] = computed[sid]
    return stored


def load_vectors(conn) -> dict[str, dict[str, float]]:
    """All stored session vectors as ``{session_id: {term: weight}}``."""
    out: dict[str, dict[str, float]] = {}
    for row in conn.execute("SELECT session_id, vector_json FROM session_vectors"):  # SAFE
        try:
            out[row["session_id"]] = json.loads(row["vector_json"])
        except (ValueError, TypeError):
            continue
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity of two sparse term-weight dicts (0.0 if either empty)."""
    if not a or not b:
        return 0.0
    # iterate the smaller dict for the dot product
    if len(a) > len(b):
        a, b = b, a
    dot = 0.0
    for term, wa in a.items():
        wb = b.get(term)
        if wb is not None:
            dot += wa * wb
    if dot == 0.0:
        return 0.0
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _shared_terms(a: dict[str, float], b: dict[str, float], top: int = 3) -> list[str]:
    """The terms two vectors share, ranked by the product of their weights."""
    shared = [(t, a[t] * b[t]) for t in a if t in b]
    shared.sort(key=lambda kv: kv[1], reverse=True)
    return [t for t, _ in shared[:top]]


def _title_for(conn, session_id: str) -> str:
    row = conn.execute(
        "SELECT title FROM sessions WHERE session_id=?", (session_id,)  # SAFE
    ).fetchone()
    return (row["title"] if row and row["title"] else session_id)


def similar(conn, session_id: str, top: int = 10) -> list[dict]:
    """Top-N sessions most semantically similar to ``session_id`` (cosine).

    Read-only safe: uses stored vectors, computing any missing ones in memory.
    """
    vecs = vectors(conn)
    query = vecs.get(session_id)
    if not query:
        return []
    scored: list[dict[str, Any]] = []
    for sid, vec in vecs.items():
        if sid == session_id:
            continue
        score = cosine(query, vec)
        if score <= 0.0:
            continue
        shared = _shared_terms(query, vec)
        reason = ("shares: " + ", ".join(shared)) if shared else "weak overlap"
        scored.append({
            "session_id": sid,
            "title": _title_for(conn, sid),
            "score": round(score, 4),
            "reason": reason,
        })
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top]


def similar_payload(conn, session_id: str, params: dict | None = None) -> dict:
    """Server/MCP-facing wrapper for :func:`similar`."""
    params = params or {}
    try:
        top = int(params.get("top", 10))
    except (TypeError, ValueError):
        top = 10
    top = max(1, min(50, top))
    return {"session_id": session_id, "similar": similar(conn, session_id, top)}


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _mk_corpus(conn) -> dict:
    """Insert 5 synthetic sessions with distinct topics; return their ids."""
    docs = {
        "auth1": "debugging auth jwt token login session expiry middleware verify",
        "auth2": "auth login jwt token refresh middleware verify expiry debugging",
        "test1": "writing unit tests pytest assert coverage fixtures mocking parametrize",
        "test2": "pytest tests coverage assert fixtures parametrize mocking unit writing",
        "refac": "refactoring database migration schema sql index normalize tables",
    }
    for sid, text in docs.items():
        conn.execute(
            "INSERT INTO sessions(session_id, title, msg_count) VALUES(?,?,?)",
            (sid, f"session {sid}", 1),
        )
        conn.execute(
            "INSERT INTO messages(uuid, session_id, role, seq, text) VALUES(?,?,?,?,?)",
            (sid + "-m", sid, "user", 0, text),
        )
    conn.commit()
    return docs


def selftest(c) -> None:
    import os
    import tempfile

    from . import index

    # --- tokenize -------------------------------------------------------
    toks = tokenize("The QUICK brown Fox, fox2 a an 42 jumps!")
    c.ok("the" not in toks, "tokenize drops stop word 'the'")
    c.ok("a" not in toks and "an" not in toks, "tokenize drops 1-char/stop words")
    c.ok("42" not in toks, "tokenize drops pure numbers")
    c.ok("quick" in toks, "tokenize lowercases content words")
    c.ok("fox" in toks and "fox2" in toks, "tokenize keeps alnum content words")
    c.eq(tokenize(""), [], "tokenize empty -> []")

    # --- cosine ---------------------------------------------------------
    v = {"a": 1.0, "b": 2.0}
    c.close(cosine(v, v), 1.0, "cosine of a vector with itself is 1.0")
    c.eq(cosine({"a": 1.0}, {"b": 1.0}), 0.0, "cosine of disjoint vectors is 0.0")
    c.eq(cosine({}, {"a": 1.0}), 0.0, "cosine with empty vector is 0.0")
    c.eq(cosine({"a": 1.0}, {}), 0.0, "cosine with empty vector (other side) is 0.0")
    half = cosine({"a": 1.0, "b": 1.0}, {"a": 1.0, "c": 1.0})
    c.ok(0.0 < half < 1.0, "cosine of partial overlap is between 0 and 1")

    with tempfile.TemporaryDirectory() as tmp:
        conn = index.connect(os.path.join(tmp, "s.db"))
        try:
            docs = _mk_corpus(conn)

            n = build_vectors(conn)
            c.eq(n, 5, "build_vectors computes all 5 vectors")
            rows = conn.execute("SELECT COUNT(*) FROM session_vectors").fetchone()[0]
            c.eq(rows, 5, "5 vectors stored")
            n2 = build_vectors(conn)
            c.eq(n2, 0, "incremental build recomputes nothing on second call")
            n3 = build_vectors(conn, rebuild=True)
            c.eq(n3, 5, "rebuild=True recomputes every vector")

            vecs = load_vectors(conn)
            c.eq(len(vecs), 5, "load_vectors returns 5")
            for sid in docs:
                c.ok(sid in vecs, f"vector present for {sid}")
                c.ok(len(vecs[sid]) <= 200, f"vector for {sid} capped at 200 terms")
                c.ok(len(vecs[sid]) > 0, f"vector for {sid} non-empty")

            s_auth1 = similar(conn, "auth1", top=10)
            c.ok(len(s_auth1) >= 1, "similar(auth1) returns results")
            c.eq(s_auth1[0]["session_id"], "auth2", "auth1 top-1 similar is auth2")
            c.ok(all(r["session_id"] != "auth1" for r in s_auth1),
                 "similar excludes the query session")
            top3_ids = {r["session_id"] for r in s_auth1[:3]}
            c.ok("refac" not in top3_ids, "refactor session not in auth1 top-3")
            c.ok(s_auth1[0]["score"] <= 1.0 and s_auth1[0]["score"] > 0.0,
                 "score in (0,1]")
            c.ok("shares:" in s_auth1[0]["reason"], "reason names shared terms")

            s_auth2 = similar(conn, "auth2", top=10)
            c.eq(s_auth2[0]["session_id"], "auth1", "auth2 top-1 similar is auth1")

            s_test1 = similar(conn, "test1", top=10)
            c.eq(s_test1[0]["session_id"], "test2", "test1 top-1 similar is test2")

            # descending order
            scores = [r["score"] for r in s_auth1]
            c.ok(all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)),
                 "similar results sorted by score desc")

            # payload wrapper
            pay = similar_payload(conn, "auth1", {"top": 2})
            c.eq(pay["session_id"], "auth1", "payload echoes session id")
            c.ok(len(pay["similar"]) <= 2, "payload honours top clamp")
            c.eq(similar(conn, "does-not-exist"), [], "similar of unknown id -> []")
        finally:
            conn.close()
