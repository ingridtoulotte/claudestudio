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


def test_as_epoch_out_of_range_dates_return_none():
    # Regression: `_as_epoch` parses a YYYY-MM-DD bound with strptime, then calls
    # `.timestamp()` — which routes through the platform's local-time conversion.
    # On Windows a pre-epoch date (1900-01-01) or a far-future one makes that
    # conversion raise OSError (and years beyond datetime's range OverflowError),
    # which used to escape `except ValueError` and surface as an HTTP 500 on
    # ?since=/?until=. An unrepresentable bound must degrade to None instead.
    for bad in ("1900-01-01", "1969-12-31", "9999-12-31"):
        assert api._as_epoch(bad) is None, bad
        assert api._as_epoch(bad, end_of_day=True) is None, bad
    # a representable date still resolves to an epoch
    assert isinstance(api._as_epoch("2026-06-01"), float)


def test_date_range_filters_tolerate_out_of_range_bounds(populated_db):
    # The endpoints that accept since/until must not 500 on an unrepresentable
    # bound — the bound is simply dropped, so the fixture still comes back.
    conn, _ = populated_db
    assert api.list_sessions(conn, {"since": "1900-01-01"})["total"] == 1
    assert api.list_sessions(conn, {"until": "9999-12-31"})["total"] == 1
    assert len(api.search(conn, {"q": "parser", "since": "1900-01-01"})["results"]) >= 1
    assert len(api.search(conn, {"q": "parser", "until": "9999-12-31"})["results"]) >= 1


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


def test_int_param_coerces_clamps_and_never_raises():
    # Bad/missing values fall back to the default instead of raising.
    assert api._int_param("abc", 60) == 60
    assert api._int_param("", 60) == 60
    assert api._int_param(None, 60) == 60
    # A negative page size must clamp to the floor, not pass through — a raw
    # `LIMIT -1` is unbounded in SQLite and would dump the whole table.
    assert api._int_param("-1", 40, lo=1, hi=200) == 1
    # Over-cap values clamp to the ceiling; in-range values pass through.
    assert api._int_param("9999", 40, lo=1, hi=200) == 200
    assert api._int_param("25", 40, lo=1, hi=200) == 25
    # Missing default may itself be None (used for the optional `year` param).
    assert api._int_param("nope", None) is None


def test_list_sessions_bad_pagination_is_safe(populated_db):
    # Regression: query-string limit/offset arrive as raw text, so non-numeric
    # values used to crash list_sessions (HTTP 500). They must degrade to the
    # defaults and still return the fixture.
    conn, info = populated_db
    sid = info["session_id"]
    assert api.list_sessions(conn, {"limit": "abc"})["sessions"][0]["session_id"] == sid
    assert api.list_sessions(conn, {"limit": ""})["total"] == 1
    assert api.list_sessions(conn, {"offset": "abc"})["total"] == 1
    # negative limit no longer bypasses the cap (it clamps, never goes unbounded)
    res = api.list_sessions(conn, {"limit": "-1"})
    assert res["limit"] >= 1 and len(res["sessions"]) == 1


def test_search_bad_limit_is_safe(populated_db):
    conn, _ = populated_db
    res = api.search(conn, {"q": "parser", "limit": "abc"})
    assert len(res["results"]) >= 1


def test_wrapped_payload_tolerates_bad_year(populated_db):
    # ?year=abc must yield the all-time view, not a 500.
    conn, _ = populated_db
    assert api.wrapped_payload(conn, "abc") is not None
    assert api.wrapped_payload(conn, "2026") is not None


def test_wrapped_payload_tolerates_out_of_range_year(populated_db):
    # Regression: `_int_param` clamps year to lo=0 but has no upper bound, so a
    # numeric-but-huge ?year used to reach dt.datetime(year+1, ...) and crash —
    # year=9999 raised OSError (Windows mktime), year>=10000 raised ValueError
    # (year out of range) — surfacing as an HTTP 500 with a leaked message.
    conn, _ = populated_db
    for bad in ("9999", "10000", "999999999"):
        payload = api.wrapped_payload(conn, bad)
        assert payload is not None, bad
        # out-of-range falls back to the all-time view, like ?year=abc
        assert payload["label"] == "All time", bad
        assert payload["year"] is None, bad
    # a representable year is still honoured
    assert api.wrapped_payload(conn, "2026")["label"] == "2026"


def test_export_filename_has_no_path_separators(populated_db):
    # The slug that becomes the download filename must never contain a separator,
    # so it can't redirect a write outside the intended directory.
    conn, info = populated_db
    out = api.export_session(conn, info["session_id"], "md")
    assert "/" not in out["filename"] and "\\" not in out["filename"]
