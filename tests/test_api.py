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


def test_list_sessions_date_range_filter(populated_db):
    conn, info = populated_db
    sid = info["session_id"]
    # The known fixture session is dated 2026-06-01. Month-margin bounds keep the
    # assertions immune to the runner's local timezone offset.
    assert api.list_sessions(conn, {"since": "2026-05-01"})["total"] == 1
    assert api.list_sessions(conn, {"since": "2026-07-01"})["total"] == 0
    assert api.list_sessions(conn, {"until": "2026-07-01"})["total"] == 1
    assert api.list_sessions(conn, {"until": "2026-05-01"})["total"] == 0
    win = api.list_sessions(conn, {"since": "2026-05-01", "until": "2026-07-01"})
    assert win["total"] == 1 and win["sessions"][0]["session_id"] == sid
    # garbage bounds are ignored, not fatal (same as search())
    assert api.list_sessions(conn, {"since": "not-a-date"})["total"] == 1


def test_as_epoch_until_covers_whole_day():
    # A bare upper-bound date must stretch to the day's last instant, otherwise
    # `<= until` silently drops everything after midnight on the selected day.
    base = api._as_epoch("2026-06-01")
    end = api._as_epoch("2026-06-01", end_of_day=True)
    assert end > base
    assert abs((end - base) - (86_400 - 1)) < 1  # ~one day minus a second
    # values that already carry a time (or are raw epochs) are untouched
    assert api._as_epoch("2026-06-01T08:30", end_of_day=True) == api._as_epoch("2026-06-01T08:30")
    assert api._as_epoch("1700000000", end_of_day=True) == 1700000000.0


def test_list_until_includes_the_selected_end_day(populated_db):
    # Regression: picking the session's own day as the `until` bound must keep it.
    # Bound dates are derived from the stored epoch in *local* time so the check
    # is immune to the runner's timezone (mirrors _as_epoch's local parsing).
    import datetime as dt

    conn, info = populated_db
    sid = info["session_id"]
    row = conn.execute(
        "SELECT first_epoch, last_epoch FROM sessions WHERE session_id=?", (sid,)
    ).fetchone()
    start_day = dt.datetime.fromtimestamp(row["first_epoch"]).strftime("%Y-%m-%d")
    end_day = dt.datetime.fromtimestamp(row["last_epoch"]).strftime("%Y-%m-%d")
    # end day as upper bound — was excluded before the end_of_day fix
    assert api.list_sessions(conn, {"until": end_day})["total"] == 1
    # single-day window (since == until on the active day) keeps the session
    win = api.list_sessions(conn, {"since": start_day, "until": end_day})
    assert win["total"] == 1 and win["sessions"][0]["session_id"] == sid


def test_search_until_includes_the_selected_end_day(populated_db):
    import datetime as dt

    conn, _ = populated_db
    row = conn.execute("SELECT MAX(epoch) e FROM messages").fetchone()
    end_day = dt.datetime.fromtimestamp(row["e"]).strftime("%Y-%m-%d")
    assert len(api.search(conn, {"q": "parser", "until": end_day})["results"]) >= 1


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
