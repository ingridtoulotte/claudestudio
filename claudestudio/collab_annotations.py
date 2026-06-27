"""Collaborative annotations — export/import the annotation layer as portable JSON.

100% local and file-based: team members share a tiny annotation file (session
notes + message notes), never the sessions themselves. The annotation layer is
the ``annotations`` table (``message_idx == -1`` is a session-level note).
"""

from __future__ import annotations

import datetime as _dt

EXPORT_VERSION = "v0.7.0"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def export_annotations(conn) -> dict:
    """All annotations as a portable, deterministically ordered payload."""
    rows = conn.execute(
        "SELECT session_id, message_idx, note, created_at, updated_at "
        "FROM annotations ORDER BY session_id, message_idx, created_at"  # SAFE
    ).fetchall()
    return {
        "version": EXPORT_VERSION,
        "exported_at": _now_iso(),
        "annotations": [
            {
                "session_id": r["session_id"],
                "message_idx": r["message_idx"],
                "body": r["note"] or "",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ],
    }


def _local_session_ids(conn) -> set:
    return {r["session_id"] for r in conn.execute("SELECT session_id FROM sessions")}


def _coerce(entry: dict):
    """Pull (session_id, message_idx, body, created_at) from an entry, or None."""
    if not isinstance(entry, dict):
        return None
    sid = entry.get("session_id")
    if not sid:
        return None
    body = entry.get("body")
    if body is None:
        body = entry.get("note")
    if body is None:
        return None
    try:
        idx = int(entry.get("message_idx", -1))
    except (TypeError, ValueError):
        idx = -1
    created = entry.get("created_at")
    return sid, idx, body, created


def import_annotations(conn, data: dict, strategy: str = "merge") -> dict:
    """Import an annotation payload. ``merge`` adds only what's missing for
    locally-present sessions; ``replace`` upserts by (session, message_idx),
    keeping the newest by ``created_at``."""
    strategy = (strategy or "merge").lower()
    if strategy not in ("merge", "replace"):
        strategy = "merge"
    entries = (data or {}).get("annotations", [])
    local = _local_session_ids(conn)
    imported = skipped = 0
    now = _now_iso()

    for entry in entries:
        coerced = _coerce(entry)
        if coerced is None:
            skipped += 1
            continue
        sid, idx, body, created = coerced
        if sid not in local:
            skipped += 1  # can't annotate a session we don't have
            continue

        if strategy == "merge":
            dup = conn.execute(
                "SELECT 1 FROM annotations WHERE session_id=? AND message_idx=? "
                "AND note=? LIMIT 1",  # SAFE: parameterized
                (sid, idx, body),
            ).fetchone()
            if dup:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO annotations(session_id,message_idx,note,created_at,updated_at)"
                " VALUES(?,?,?,?,?)",  # SAFE
                (sid, idx, body, created if created is not None else now, now))
            imported += 1
        else:  # replace: upsert by (session_id, message_idx), newest wins
            existing = conn.execute(
                "SELECT id, created_at FROM annotations WHERE session_id=? "
                "AND message_idx=? ORDER BY created_at DESC LIMIT 1",  # SAFE
                (sid, idx),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO annotations(session_id,message_idx,note,created_at,"
                    "updated_at) VALUES(?,?,?,?,?)",  # SAFE
                    (sid, idx, body, created if created is not None else now, now))
                imported += 1
            else:
                cur_created = existing["created_at"]
                if created is not None and cur_created is not None and created > cur_created:
                    conn.execute(
                        "UPDATE annotations SET note=?, created_at=?, updated_at=? "
                        "WHERE id=?",  # SAFE
                        (body, created, now, existing["id"]))
                    imported += 1
                else:
                    skipped += 1
    conn.commit()
    return {"imported": imported, "skipped": skipped}


def export_payload(conn) -> dict:
    return export_annotations(conn)


def import_payload(conn, body: dict) -> dict:
    body = body or {}
    data = body.get("data", body)
    strategy = body.get("strategy", "merge")
    return import_annotations(conn, data, strategy)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _add_anno(conn, sid, idx, note, created):
    conn.execute(
        "INSERT INTO annotations(session_id,message_idx,note,created_at,updated_at) "
        "VALUES(?,?,?,?,?)", (sid, idx, note, created, created))


def selftest(c) -> None:
    import os
    import tempfile

    from . import index

    with tempfile.TemporaryDirectory() as tmp:
        conn = index.connect(os.path.join(tmp, "a.db"))
        try:
            for sid in ("s1", "s2", "s3"):
                conn.execute("INSERT INTO sessions(session_id,title) VALUES(?,?)",
                             (sid, sid))
            # 5 annotations: mix of session-level (-1) and message-level
            _add_anno(conn, "s1", -1, "gold auth approach", 100.0)
            _add_anno(conn, "s1", 3, "note on message 3", 101.0)
            _add_anno(conn, "s2", -1, "tests session", 102.0)
            _add_anno(conn, "s2", 7, "flaky here", 103.0)
            _add_anno(conn, "s3", -1, "refactor notes", 104.0)
            conn.commit()

            exp = export_annotations(conn)
            c.eq(exp["version"], "v0.7.0", "export version v0.7.0")
            c.ok("exported_at" in exp, "export has timestamp")
            c.eq(len(exp["annotations"]), 5, "export has all 5 annotations")
            c.eq(exp["annotations"][0]["session_id"], "s1", "export sorted by session")
            c.eq(exp["annotations"][0]["body"], "gold auth approach", "body == note")
            # deterministic ordering
            keys = [(a["session_id"], a["message_idx"]) for a in exp["annotations"]]
            c.eq(keys, sorted(keys), "export deterministically ordered")

            # delete 2, re-import (merge) -> all 5 present again
            conn.execute("DELETE FROM annotations WHERE session_id='s2'")
            conn.commit()
            c.eq(conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0], 3,
                 "2 annotations deleted (s2)")
            res = import_annotations(conn, exp, "merge")
            c.eq(res["imported"], 2, "merge re-imports the 2 missing")
            c.eq(res["skipped"], 3, "merge skips the 3 already present")
            c.eq(conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0], 5,
                 "all 5 annotations present after merge")

            # merge skips annotations whose session doesn't exist locally
            bogus = {"version": "v0.7.0", "annotations": [
                {"session_id": "ghost", "message_idx": -1, "body": "x", "created_at": 1.0}]}
            r2 = import_annotations(conn, bogus, "merge")
            c.eq(r2["imported"], 0, "merge imports nothing for missing session")
            c.eq(r2["skipped"], 1, "merge skips the missing-session entry")

            # idempotent merge: re-importing the same export adds nothing
            r3 = import_annotations(conn, exp, "merge")
            c.eq(r3["imported"], 0, "merge is idempotent")
            c.eq(r3["skipped"], 5, "merge skips all already-present")

            # replace: newer created_at wins
            newer = {"annotations": [
                {"session_id": "s1", "message_idx": -1, "body": "UPDATED approach",
                 "created_at": 999.0}]}
            rr = import_annotations(conn, newer, "replace")
            c.eq(rr["imported"], 1, "replace updates on newer created_at")
            row = conn.execute(
                "SELECT note FROM annotations WHERE session_id='s1' AND message_idx=-1"
            ).fetchone()
            c.eq(row["note"], "UPDATED approach", "replace overwrote with newer body")

            # replace: older created_at does NOT win
            older = {"annotations": [
                {"session_id": "s1", "message_idx": -1, "body": "STALE",
                 "created_at": 1.0}]}
            ro = import_annotations(conn, older, "replace")
            c.eq(ro["imported"], 0, "replace ignores older created_at")
            c.eq(ro["skipped"], 1, "replace skips the stale entry")
            row2 = conn.execute(
                "SELECT note FROM annotations WHERE session_id='s1' AND message_idx=-1"
            ).fetchone()
            c.eq(row2["note"], "UPDATED approach", "body unchanged by stale import")

            # replace inserts a brand-new (session,idx)
            fresh = {"annotations": [
                {"session_id": "s3", "message_idx": 5, "body": "new msg note",
                 "created_at": 200.0}]}
            rf = import_annotations(conn, fresh, "replace")
            c.eq(rf["imported"], 1, "replace inserts a new (session,idx)")

            # defensive: malformed entries are skipped, never crash
            bad = {"annotations": [{"message_idx": 1}, "not a dict", {"session_id": "s1"}]}
            rb = import_annotations(conn, bad, "merge")
            c.eq(rb["imported"], 0, "malformed entries import nothing")
            c.eq(rb["skipped"], 3, "malformed entries are all skipped")

            # payload wrappers
            c.eq(export_payload(conn)["version"], "v0.7.0", "export_payload works")
            pr = import_payload(conn, {"data": exp, "strategy": "merge"})
            c.ok("imported" in pr and "skipped" in pr, "import_payload returns counts")
            c.eq(import_annotations(conn, {}, "merge"), {"imported": 0, "skipped": 0},
                 "empty payload -> nothing imported")
            c.eq(import_annotations(conn, exp, "bogus-strategy")["imported"], 0,
                 "unknown strategy falls back to merge (idempotent here)")
        finally:
            conn.close()
