"""Index: schema creation, version bookkeeping, migration safety, incremental reindex."""

from __future__ import annotations

import pytest

from claudestudio import index


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def test_connect_creates_core_schema(empty_db):
    tables = _tables(empty_db)
    assert {"sessions", "messages", "tool_calls", "user_state", "meta"} <= tables


def test_schema_version_recorded(empty_db):
    assert index.stored_schema_version(empty_db) == index.SCHEMA_VERSION


def test_maybe_migrate_is_idempotent(empty_db):
    index.maybe_migrate(empty_db)
    index.maybe_migrate(empty_db)
    assert index.stored_schema_version(empty_db) == index.SCHEMA_VERSION


def test_pre_versioning_db_upgrades_cleanly(empty_db):
    # Simulate an index created before versioning existed (no schema_version row).
    empty_db.execute("DELETE FROM meta WHERE key='schema_version'")
    assert index.stored_schema_version(empty_db) == 0
    index.maybe_migrate(empty_db)
    assert index.stored_schema_version(empty_db) == index.SCHEMA_VERSION


def test_future_schema_version_raises(db_path):
    conn = index.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
        (str(index.SCHEMA_VERSION + 99),),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer ClaudeStudio"):
        index.connect(db_path)


def test_garbage_schema_version_treated_as_baseline(empty_db):
    empty_db.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','not-a-number')"
    )
    assert index.stored_schema_version(empty_db) == 0


def test_reindex_then_skip_unchanged(known, db_path):
    conn = index.connect(db_path)
    first = index.reindex(conn, known["root"])
    assert first["added"] == 1
    second = index.reindex(conn, known["root"])
    assert second["added"] == 0
    assert second["updated"] == 0
    assert second["skipped"] == 1
    conn.close()


def test_summary_counts_match_known_fixture(populated_db):
    conn, info = populated_db
    summ = index.session_summary(conn)
    assert summ["sessions"] == 1
    assert summ["tool_calls"] == info["tool_calls"]
    assert summ["cost_usd"] == pytest.approx(info["cost"])
