# Semantic search (local TF-IDF)

Find the sessions most similar to a given one — by meaning, not just keywords —
with **zero dependencies**. No embeddings API, no `sentence-transformers`, no
`faiss`. Just `math` and `collections`.

## How it works

- At index time, every session is reduced to a sparse **TF-IDF** vector over the
  combined text of its prompts and responses. The top-200 terms are stored as a
  compact JSON dict in the `session_vectors` table.
- `tf = count(term, doc) / len(doc)`, `idf = log(N / (1 + df(term)))`.
- Similarity is cosine of the two sparse vectors.
- Vectors are **derived** data — safe to delete and rebuild. They are recomputed
  incrementally on `reindex`, and on demand if missing.

## CLI

```console
$ claudestudio similar <session_id> --top 10
  0.930  Debug N+1 query in orders endpoint   shares: join, eager, p95
  0.867  Tune retrieval prompt for accuracy   shares: index, cache, latency
```

`--last` uses the most recent session; `--json` emits raw JSON.

## API

```
GET /api/session/{id}/semantic?top=10
    → {session_id, similar: [{session_id, title, score, reason}]}
```

(The existing `GET /api/session/{id}/similar` endpoint is unchanged.)

## MCP

Tool **#32 `find_similar_sessions(session_id, top=10)`**.

<!-- TODO screenshot: docs/screenshots/v070_similar_sessions.png -->
