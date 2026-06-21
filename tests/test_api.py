"""API layer: list/search/detail/export over a known fixture."""

from __future__ import annotations

from claudestudio import api


def test_list_sessions_returns_the_fixture(populated_db):
    conn, info = populated_db
    res = api.list_sessions(conn, {})
    assert res["total"] == 1
    assert res["sessions"][0]["session_id"] == info["session_id"]


def test_list_sessions_query_filters_by_title(populated_db):
    conn, _ = populated_db
    assert api.list_sessions(conn, {"q": "Known fixture"})["total"] == 1
    assert api.list_sessions(conn, {"q": "zzz-not-present-zzz"})["total"] == 0


def test_get_session_builds_timeline_with_tools(populated_db):
    conn, info = populated_db
    detail = api.get_session(conn, info["session_id"])
    assert detail is not None
    assert len(detail["timeline"]) == info["messages"]
    asst = [m for m in detail["timeline"] if m["role"] == "assistant"][0]
    assert len(asst["tools"]) == info["tool_calls"]
    assert any(t["is_error"] for t in asst["tools"])


def test_get_session_unknown_is_none(populated_db):
    conn, _ = populated_db
    assert api.get_session(conn, "does-not-exist") is None


def test_search_finds_tool_text(populated_db):
    conn, _ = populated_db
    res = api.search(conn, {"q": "parser"})
    assert len(res["results"]) >= 1


def test_search_kind_filter_restricts_rows(populated_db):
    conn, _ = populated_db
    res = api.search(conn, {"q": "parser", "kind": "tool"})
    assert res["results"]
    assert all(r["kind"] == "tool" for r in res["results"])


def test_export_session_markdown_and_html(populated_db):
    conn, info = populated_db
    md = api.export_session(conn, info["session_id"], "md")
    assert md is not None and md["filename"].endswith(".md")
    assert "# Known fixture session" in md["text"]
    html = api.export_session(conn, info["session_id"], "html")
    assert html["filename"].endswith(".html")
    assert "<!doctype html>" in html["text"].lower()


def test_export_filename_has_no_path_separators(populated_db):
    # The slug that becomes the download filename must never contain a separator,
    # so it can't redirect a write outside the intended directory.
    conn, info = populated_db
    out = api.export_session(conn, info["session_id"], "md")
    assert "/" not in out["filename"] and "\\" not in out["filename"]
