# Session clustering

Automatically group your sessions by topic with **k-means** over the local TF-IDF
vectors (see [Semantic search](SEMANTIC_SEARCH.md)). No scikit-learn — pure
standard-library maths.

## How it works

- Lloyd's algorithm on the sparse term-weight vectors, cosine distance.
- **Deterministic**: random initialization is seeded with `42`, so the same corpus
  always produces the same clusters (great for tests and reproducibility).
- Each cluster is auto-labelled from its centroid's top-5 terms, e.g.
  `debugging·auth·jwt` or `refactor·database·migration`.
- Cold-start safe: if `k` exceeds the number of sessions, `k` collapses to the
  session count.
- Assignments are cached in the `session_clusters` table.

## CLI

```console
$ claudestudio clusters --k 8
  ▸ join·eager·p95·dropped·ms   (22 sessions · avg $1.002 · health 77.4)
      Debug N+1 query in orders endpoint        health 85
      ...
```

`--json` emits the full report.

## API

```
GET /api/clusters?k=8&refresh=false
    → {k, clusters: [{id, label, terms, count, avg_cost, avg_health,
                      sessions: [{id, title, health}]}]}
```

## MCP

Tool **#33 `get_session_clusters(k=8)`**.

<!-- TODO screenshot: docs/screenshots/v070_clusters.png -->
