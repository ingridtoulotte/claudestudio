"""Analytics & Wrapped robustness: time-bucketed views must survive a corrupt or
far-future session epoch identically on every OS.

A timestamp parses to a far-future instant when a log carries a valid ISO-8601
date near year 9999, or a millisecond value mistaken for seconds. That epoch sits
outside ``datetime.fromtimestamp``'s range: Windows raises ``OSError`` (an HTTP
500 on ``/api/analytics`` with a leaked traceback), while POSIX silently buckets
it under year 9999 and skews the timeline. ``parser.local_datetime`` bounds the
window so both the crash and the per-OS skew are gone — the row still counts in
SQL totals, but is dropped from the daily chart, heatmap, and Wrapped years.
"""

from __future__ import annotations

import datetime as dt

from claudestudio import analytics, parser, wrapped

# Year-9999, expressed tz-aware so the constant itself never touches local time.
FAR_FUTURE = dt.datetime(9999, 12, 31, tzinfo=dt.timezone.utc).timestamp()
GOOD = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()


def _insert(conn, session_id, epoch):
    conn.execute(
        "INSERT INTO sessions(session_id,project,last_epoch,first_epoch,"
        "msg_count,tool_calls,cost_usd,input_tokens,output_tokens,"
        "cache_write,cache_read,duration_s) "
        "VALUES(?,?,?,?,1,0,0,0,0,0,0,0)",
        (session_id, "/p", epoch, epoch),
    )


def test_local_datetime_rejects_out_of_range():
    assert parser.local_datetime(GOOD) is not None
    assert parser.local_datetime(FAR_FUTURE) is None  # far future -> None on every OS
    assert parser.local_datetime(-1) is None  # pre-epoch -> None
    assert parser.local_datetime(None) is None
    assert parser.local_datetime("not-a-number") is None
    assert parser.local_datetime(0) is None


def test_analytics_overview_survives_far_future_epoch(empty_db):
    conn = empty_db
    _insert(conn, "good", GOOD)
    _insert(conn, "far", FAR_FUTURE)
    conn.commit()

    ov = analytics.overview(conn)  # must not raise (was OSError on Windows)
    assert ov["sessions"] == 2  # SQL totals still see both rows
    # Time-bucketed views drop the corrupt row identically on every OS.
    assert sum(sum(row) for row in ov["heatmap"]) == 1
    assert all(not d["date"].startswith("9999") for d in ov["daily"])
    # The direct helpers are safe too.
    assert analytics.daily_activity(conn)
    assert len(analytics.heatmap(conn)) == 7


def test_wrapped_survives_far_future_epoch(empty_db):
    conn = empty_db
    _insert(conn, "good", GOOD)
    _insert(conn, "far", FAR_FUTURE)
    conn.commit()

    w = wrapped.generate(conn)  # must not raise
    assert any(card["label"] == "Sessions" for card in w["cards"])
    years = wrapped.available_years(conn)
    assert 9999 not in years  # far-future year never offered as a filter
    assert 2026 in years
