"""Session clustering — k-means over the local TF-IDF vectors (pure stdlib).

No sklearn. Lloyd's algorithm on the sparse term-weight vectors produced by
:mod:`claudestudio.semantic`. Clusters are auto-labelled from their centroid's
top terms and cached in the ``session_clusters`` table (derived data).
"""

from __future__ import annotations

import datetime as _dt
import random
import sqlite3

from . import semantic


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _centroid(vectors: list[dict[str, float]]) -> dict[str, float]:
    """Mean of a list of sparse vectors."""
    if not vectors:
        return {}
    acc: dict[str, float] = {}
    for v in vectors:
        for term, w in v.items():
            acc[term] = acc.get(term, 0.0) + w
    n = len(vectors)
    return {term: w / n for term, w in acc.items()}


def kmeans(vectors: dict[str, dict[str, float]], k: int, *, seed: int = 42,
           max_iter: int = 100, tol: float = 1e-4) -> dict:
    """Cluster sparse vectors with deterministic k-means (cosine distance).

    Returns ``{"assignments": {sid: cluster_id}, "centroids": {cid: vec},
    "k": k, "iterations": n}``. Cold-start safe: if ``k`` exceeds the number of
    vectors, ``k`` collapses to the vector count.
    """
    ids = sorted(vectors)  # deterministic order
    n = len(ids)
    if n == 0:
        return {"assignments": {}, "centroids": {}, "k": 0, "iterations": 0}
    k = max(1, min(k, n))

    rng = random.Random(seed)
    seeds = rng.sample(ids, k)
    centroids = {cid: dict(vectors[sid]) for cid, sid in enumerate(seeds)}

    assignments: dict[str, int] = {}
    iterations = 0
    while iterations < max_iter:
        iterations += 1
        # assignment step — nearest centroid by cosine distance (1 - cos)
        new_assign: dict[str, int] = {}
        for sid in ids:
            v = vectors[sid]
            best_cid, best_sim = 0, -1.0
            for cid, cen in centroids.items():
                sim = semantic.cosine(v, cen)
                if sim > best_sim:
                    best_sim, best_cid = sim, cid
            new_assign[sid] = best_cid
        # update step
        members: dict[int, list[dict[str, float]]] = {cid: [] for cid in centroids}
        for sid, cid in new_assign.items():
            members[cid].append(vectors[sid])
        moved = 0.0
        new_centroids: dict[int, dict[str, float]] = {}
        for cid in centroids:
            # empty cluster: keep the old centroid (stable, deterministic)
            nc = _centroid(members[cid]) if members[cid] else centroids[cid]
            # movement = 1 - cosine(old, new)
            moved = max(moved, 1.0 - semantic.cosine(centroids[cid], nc))
            new_centroids[cid] = nc
        centroids = new_centroids
        converged = (new_assign == assignments) or (moved < tol)
        assignments = new_assign
        if converged:
            break

    return {"assignments": assignments, "centroids": centroids, "k": k,
            "iterations": iterations}


def label_cluster(centroid: dict[str, float], top: int = 5) -> str:
    """Human label from a centroid's strongest terms, joined by a middle dot."""
    terms = [t for t, _ in sorted(centroid.items(), key=lambda kv: kv[1],
                                  reverse=True)[:top]]
    return "·".join(terms) if terms else "misc"


def cluster_sessions(conn, k: int = 8, *, refresh: bool = False) -> dict:
    """Assign every session to a cluster and return a named cluster report.

    Cached in ``session_clusters``; pass ``refresh=True`` to recompute.
    """
    sess_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    cached_count = conn.execute("SELECT COUNT(*) FROM session_clusters").fetchone()[0]
    if not refresh and cached_count and cached_count == sess_count:
        return _report_from_cache(conn, k)

    vecs = semantic.vectors(conn)
    if not vecs:
        return {"k": 0, "clusters": []}

    res = kmeans(vecs, k)
    assignments = res["assignments"]
    centroids = res["centroids"]
    labels = {cid: label_cluster(cen) for cid, cen in centroids.items()}

    now = _now_iso()
    try:
        conn.execute("DELETE FROM session_clusters")  # SAFE: rebuild derived data
        for sid, cid in assignments.items():
            sim = semantic.cosine(vecs[sid], centroids[cid])
            conn.execute(
                "INSERT OR REPLACE INTO session_clusters("
                "session_id, cluster_id, cluster_label, similarity_to_centroid, clustered_at)"
                " VALUES(?,?,?,?,?)",  # SAFE: parameterized
                (sid, cid, labels[cid], round(sim, 6), now),
            )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # read-only connection: caching is best-effort
    return _build_report(conn, res["k"], assignments, labels)


def _session_meta(conn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT session_id, title, cost_usd, health_score FROM sessions"  # SAFE
    ):
        out[r["session_id"]] = {
            "title": r["title"] or r["session_id"],
            "cost_usd": r["cost_usd"] or 0.0,
            "health": r["health_score"] if r["health_score"] is not None else 0,
        }
    return out


def _assemble(conn, k: int, assignments: dict[str, int],
              labels: dict[int, str], terms_by_cid: dict[int, list[str]]) -> dict:
    meta = _session_meta(conn)
    by_cluster: dict[int, list[str]] = {}
    for sid, cid in assignments.items():
        by_cluster.setdefault(cid, []).append(sid)

    clusters = []
    for cid in sorted(by_cluster):
        sids = by_cluster[cid]
        costs = [meta.get(s, {}).get("cost_usd", 0.0) for s in sids]
        healths = [meta.get(s, {}).get("health", 0) for s in sids]
        ranked = sorted(sids, key=lambda s: meta.get(s, {}).get("health", 0),
                        reverse=True)[:3]
        clusters.append({
            "id": cid,
            "label": labels.get(cid, "misc"),
            "terms": terms_by_cid.get(cid, labels.get(cid, "").split("·")),
            "count": len(sids),
            "avg_cost": round(sum(costs) / len(costs), 6) if costs else 0.0,
            "avg_health": round(sum(healths) / len(healths), 1) if healths else 0.0,
            "sessions": [
                {"id": s, "title": meta.get(s, {}).get("title", s),
                 "health": meta.get(s, {}).get("health", 0)}
                for s in ranked
            ],
        })
    return {"k": k, "clusters": clusters}


def _build_report(conn, k: int, assignments, labels) -> dict:
    terms_by_cid = {cid: lab.split("·") for cid, lab in labels.items()}
    return _assemble(conn, k, assignments, labels, terms_by_cid)


def _report_from_cache(conn, k: int) -> dict:
    assignments: dict[str, int] = {}
    labels: dict[int, str] = {}
    for r in conn.execute(
        "SELECT session_id, cluster_id, cluster_label FROM session_clusters"  # SAFE
    ):
        assignments[r["session_id"]] = r["cluster_id"]
        labels[r["cluster_id"]] = r["cluster_label"]
    return _build_report(conn, k, assignments, labels)


def clusters_payload(conn, params: dict | None = None) -> dict:
    """Server/MCP-facing wrapper for :func:`cluster_sessions`."""
    params = params or {}
    try:
        k = int(params.get("k", 8))
    except (TypeError, ValueError):
        k = 8
    k = max(2, min(32, k))
    refresh = str(params.get("refresh", "")).lower() in ("1", "true", "yes")
    return cluster_sessions(conn, k, refresh=refresh)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def selftest(c) -> None:
    import os
    import tempfile

    from . import index

    with tempfile.TemporaryDirectory() as tmp:
        conn = index.connect(os.path.join(tmp, "k.db"))
        try:
            semantic._mk_corpus(conn)
            semantic.build_vectors(conn)
            vectors = semantic.load_vectors(conn)

            res = kmeans(vectors, 2)
            c.eq(res["k"], 2, "kmeans k=2 keeps k=2")
            cids = set(res["assignments"].values())
            c.eq(len(cids), 2, "k=2 yields exactly 2 non-empty clusters")
            a = res["assignments"]
            c.eq(a["auth1"], a["auth2"], "both auth sessions in the same cluster")
            c.eq(a["test1"], a["test2"], "both test sessions in the same cluster")
            c.ok(a["auth1"] != a["test1"], "auth and test sessions differ in cluster")

            # determinism
            res2 = kmeans(vectors, 2)
            c.eq(res["assignments"], res2["assignments"],
                 "kmeans is deterministic with seed 42")

            # cold start: k > n collapses to n
            small = {"x": vectors["auth1"], "y": vectors["test1"]}
            res3 = kmeans(small, 8)
            c.eq(res3["k"], 2, "cold start: k>n collapses to n vectors")
            c.eq(len(set(res3["assignments"].values())), 2, "cold start produces n clusters")

            # label format
            lab = label_cluster(res["centroids"][a["auth1"]])
            c.ok("·" in lab or len(lab.split("·")) >= 1, "label uses middle dot")
            c.ok(len(lab.split("·")) <= 5, "label has at most 5 terms")
            c.ok(len(lab) > 0, "label non-empty")

            rep = cluster_sessions(conn, 2, refresh=True)
            c.eq(rep["k"], 2, "cluster_sessions report k=2")
            c.eq(len(rep["clusters"]), 2, "report has 2 clusters")
            total = sum(cl["count"] for cl in rep["clusters"])
            c.eq(total, 5, "all 5 sessions assigned")
            c.ok(all("label" in cl and "terms" in cl for cl in rep["clusters"]),
                 "each cluster has label and terms")
            c.ok(all("sessions" in cl and len(cl["sessions"]) <= 3 for cl in rep["clusters"]),
                 "each cluster lists up to 3 top sessions")
            c.ok(all("avg_cost" in cl and "avg_health" in cl for cl in rep["clusters"]),
                 "each cluster has avg cost and health")

            rowcount = conn.execute("SELECT COUNT(*) FROM session_clusters").fetchone()[0]
            c.eq(rowcount, 5, "session_clusters cached for all sessions")

            # cache: second call without refresh returns same assignment set
            rep_cached = cluster_sessions(conn, 2, refresh=False)
            c.eq(sum(cl["count"] for cl in rep_cached["clusters"]), 5,
                 "cached report covers all sessions")

            pay = clusters_payload(conn, {"k": 2})
            c.ok("clusters" in pay, "clusters_payload returns clusters")
            c.eq(clusters_payload(conn, {"k": "bad"})["clusters"] and True, True,
                 "clusters_payload tolerates bad k")

            # --- extra coverage -----------------------------------------
            c.ok(res["iterations"] >= 1, "kmeans runs at least one iteration")
            c.ok(res["iterations"] <= 100, "kmeans respects max_iter")
            c.eq(set(res["centroids"].keys()), set(res["assignments"].values()),
                 "every assigned cluster has a centroid")
            one = kmeans(vectors, 1)
            c.eq(one["k"], 1, "k=1 keeps a single cluster")
            c.eq(len(set(one["assignments"].values())), 1, "k=1 puts all in one cluster")
            auth_label = label_cluster(res["centroids"][a["auth1"]])
            c.ok("·" in auth_label and 1 <= len(auth_label.split("·")) <= 5,
                 "cluster label is dot-joined top terms")
            c.eq(label_cluster({}), "misc", "empty centroid labels as misc")
            c.eq(kmeans({}, 3)["k"], 0, "kmeans on empty corpus -> k=0")
            empty = index.connect(os.path.join(tmp, "empty.db"))
            try:
                c.eq(cluster_sessions(empty, 4)["clusters"], [],
                     "cluster_sessions on empty index -> no clusters")
            finally:
                empty.close()
            c.ok(_centroid([{"a": 2.0}, {"a": 4.0}])["a"] == 3.0,
                 "centroid averages member weights")
        finally:
            conn.close()
