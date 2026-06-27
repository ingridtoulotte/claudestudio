# Collaborative annotations

Share your annotation layer (session notes and message notes) with a teammate —
still **100% local and file-based**. The sessions never move; only the tiny
annotation layer does.

## Export

```console
$ claudestudio annotations export --out annotations.json
  ✓ exported 42 annotations to annotations.json
```

Format:

```json
{
  "version": "v0.7.0",
  "exported_at": "2026-06-27T11:39:08+00:00",
  "annotations": [
    {"session_id": "...", "message_idx": -1, "body": "gold auth approach",
     "created_at": 1700000000.0, "updated_at": 1700000000.0}
  ]
}
```

`message_idx == -1` is a session-level note; `>= 0` is a message note.

## Import

```console
$ claudestudio annotations import annotations.json            # merge (default)
$ claudestudio annotations import annotations.json --replace  # upsert, newest wins
  Imported 2 annotations, skipped 40 (strategy: merge).
```

- **merge** (default): only imports annotations for sessions you have locally;
  identical annotations are skipped (idempotent).
- **replace**: upserts by `(session_id, message_idx)`, keeping the newer
  `created_at`.

## API

```
GET  /api/annotations/export   → the payload above
POST /api/annotations/import   → {"data": {...}, "strategy": "merge"|"replace"}
                               → {imported, skipped}
```

## MCP

Tools **#37 `export_annotations()`** and **#38 `import_annotations(data, strategy)`**.

<!-- TODO screenshot: docs/screenshots/v070_annotations.png -->
