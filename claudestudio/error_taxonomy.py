"""Error taxonomy & recurring-error tracking (v0.6.2 — the "Insight Engine").

Every tool call that errored carries a ``result_preview``; this module classifies
that text into a small, fixed taxonomy so the UI/MCP can answer "what *kinds* of
errors keep tripping me up, and where?". It is **deterministic** — pure string
heuristics over the parsed session, no model calls, no network — so the same
error string always lands in the same bucket and the self-test pins it exactly.

Classifications are written into the ``session_errors`` table at (re)index time
(derived data, rebuilt on every reindex like the GitHub refs) and aggregated on
demand for the errors dashboard.
"""

from __future__ import annotations

import datetime as _dt
import re

# The fixed taxonomy. ``unknown`` is the fallthrough bucket and always last.
ERROR_TYPES = (
    "permission_error",
    "file_not_found",
    "syntax_error",
    "timeout",
    "api_error",
    "assertion_failure",
    "unknown",
)

# (type, pattern). Order is significant — `classify_error` returns the first hit,
# so the more specific buckets come first. Driven by the error text, which is the
# stable signal across every tool.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("permission_error", re.compile(
        r"permission denied|operation not permitted|\bEACCES\b|\bEPERM\b|"
        r"access is denied|not authoriz|\bforbidden\b|requires? (?:sudo|root|admin)",
        re.I)),
    ("file_not_found", re.compile(
        r"no such file or directory|\bENOENT\b|file not found|"
        r"can(?:no|')?t find|could ?n(?:o|')?t find|could not find|"
        r"does not exist|not a directory|\bENOTDIR\b|no such file",
        re.I)),
    ("timeout", re.compile(
        r"timed out|\btimeout\b|\bETIMEDOUT\b|deadline exceeded|operation timed",
        re.I)),
    ("syntax_error", re.compile(
        r"syntaxerror|syntax error|indentationerror|unexpected token|"
        r"unexpected eof|parse error|invalid syntax|unterminated",
        re.I)),
    ("assertion_failure", re.compile(
        r"assertionerror|assertion failed|\bassert\b|tests? failed|"
        r"\bFAILED\b|expected .* (?:but )?got|did not equal",
        re.I)),
    ("api_error", re.compile(
        r"\bAPI error\b|rate limit|\b429\b|\b50[0-9]\b|status code 5|"
        r"connection (?:refused|reset|error)|network (?:error|unreachable)|"
        r"\bECONNREFUSED\b|\bECONNRESET\b|overloaded",
        re.I)),
]


def classify_error(text: str | None, tool_name: str | None = None) -> str:
    """Map one error string to a taxonomy bucket. Deterministic; never raises.

    ``tool_name`` is accepted for future signal but classification is currently
    driven by the error text (the stable source across tools). Empty text →
    ``unknown``.
    """
    s = str(text or "")
    if not s.strip():
        return "unknown"
    for etype, pat in _PATTERNS:
        if pat.search(s):
            return etype
    return "unknown"


def extract_errors(ps) -> list[dict]:
    """Every errored tool call in a parsed session, classified, in message order.

    One dict per error: ``{error_type, error_text, tool_name, message_idx, ts}``.
    The text is the tool result preview (trimmed); ``message_idx`` is the owning
    message's 0-based seq.
    """
    out: list[dict] = []
    for m in getattr(ps, "messages", []) or []:
        for t in m.tool_calls:
            if not t.is_error:
                continue
            text = (t.result_preview or "")[:2000]
            out.append({
                "error_type": classify_error(text, t.name),
                "error_text": text,
                "tool_name": t.name or "",
                "message_idx": m.seq,
                "ts": t.ts or m.ts or "",
            })
    return out


# ---------------------------------------------------------------------------
# DB-backed aggregation (read-only over the session_errors table)
# ---------------------------------------------------------------------------

def _date_to_epoch(date: str | None) -> float | None:
    """``YYYY-MM-DD`` → epoch seconds (local midnight), or None if unparseable."""
    if not date:
        return None
    try:
        return _dt.datetime.strptime(str(date)[:10], "%Y-%m-%d").timestamp()
    except (ValueError, OverflowError, OSError):
        return None


# A single, constant WHERE snippet. Both filters are optional and applied with
# named bind parameters: a NULL value short-circuits the clause to a no-op. This
# keeps every query string a compile-time constant (no f-string interpolation of
# any value), so there is no SQL-injection surface — the project/since values
# only ever travel as bound parameters.
_WHERE = (
    "(:proj IS NULL OR s.project = :proj OR s.project_name = :proj) "
    "AND (:since_ep IS NULL OR s.last_epoch >= :since_ep)"
)


def _filter(project: str | None, since: str | None) -> dict:
    return {"proj": str(project) if project else None,
            "since_ep": _date_to_epoch(since)}


def by_type(conn, project: str | None = None, since: str | None = None) -> dict:
    """Error counts per taxonomy bucket, every bucket present (zero-filled)."""
    counts = dict.fromkeys(ERROR_TYPES, 0)
    for r in conn.execute(
        "SELECT e.error_type t, COUNT(*) n FROM session_errors e "
        "JOIN sessions s USING(session_id) WHERE " + _WHERE + " GROUP BY e.error_type",
        _filter(project, since),
    ):
        if r["t"] in counts:
            counts[r["t"]] = int(r["n"] or 0)
        else:
            counts["unknown"] += int(r["n"] or 0)
    return counts


def by_project(conn, project: str | None = None, since: str | None = None) -> dict:
    rows = conn.execute(
        "SELECT COALESCE(s.project_name, s.project, '(unknown)') p, COUNT(*) n "
        "FROM session_errors e JOIN sessions s USING(session_id) WHERE " + _WHERE + " "
        "GROUP BY p ORDER BY n DESC, p",
        _filter(project, since),
    ).fetchall()
    return {r["p"]: int(r["n"] or 0) for r in rows}


def worst_sessions(conn, project: str | None = None, since: str | None = None,
                   limit: int = 10) -> list[dict]:
    params = {**_filter(project, since), "lim": max(1, int(limit))}
    rows = conn.execute(
        "SELECT e.session_id id, COALESCE(s.title,'') title, COUNT(*) count, "
        "       COALESCE(s.last_ts,'') last_ts "
        "FROM session_errors e JOIN sessions s USING(session_id) WHERE " + _WHERE + " "
        "GROUP BY e.session_id ORDER BY count DESC, MAX(s.last_epoch) DESC, e.session_id "
        "LIMIT :lim",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def trend(conn, weeks: int = 12, now: _dt.datetime | None = None) -> list[dict]:
    """Weekly error counts for the last ``weeks`` calendar weeks (oldest first).

    ``now`` is injectable so the self-test is deterministic. Each entry is
    ``{week: 'YYYY-MM-DD' (Monday), errors: int}``.
    """
    now = now or _dt.datetime.now()
    weeks = max(1, min(int(weeks), 520))
    out: list[dict] = []
    monday = (now - _dt.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    for i in range(weeks - 1, -1, -1):
        start = monday - _dt.timedelta(days=7 * i)
        end = start + _dt.timedelta(days=7)
        row = conn.execute(
            "SELECT COUNT(*) n FROM session_errors e JOIN sessions s USING(session_id) "
            "WHERE s.last_epoch >= ? AND s.last_epoch < ?",
            (start.timestamp(), end.timestamp()),
        ).fetchone()
        out.append({"week": start.strftime("%Y-%m-%d"), "errors": int(row["n"] or 0)})
    return out


def taxonomy(conn, project: str | None = None, since: str | None = None) -> dict:
    """The full errors dashboard payload."""
    return {
        "by_type": by_type(conn, project, since),
        "by_project": by_project(conn, project, since),
        "trend": trend(conn),
        "worst_sessions": worst_sessions(conn, project, since),
        "total": sum(by_type(conn, project, since).values()),
    }


def sessions_by_error_type(conn, error_type: str, limit: int = 20) -> list[dict]:
    """Sessions containing errors of one taxonomy type, most recent first."""
    rows = conn.execute(
        "SELECT e.session_id id, COALESCE(s.title,'') title, COALESCE(s.last_ts,'') ts, "
        "       COUNT(*) error_count "
        "FROM session_errors e JOIN sessions s USING(session_id) "
        "WHERE e.error_type = ? "
        "GROUP BY e.session_id ORDER BY MAX(s.last_epoch) DESC, e.session_id LIMIT ?",
        (str(error_type), max(1, int(limit))),
    ).fetchall()
    return [dict(r) for r in rows]


def error_rate(conn, days: int = 7, now: _dt.datetime | None = None) -> dict:
    """Errors-per-session over the last ``days`` vs the preceding window.

    Used by ``doctor`` for a one-line "your error rate rose/fell" recommendation.
    """
    now = now or _dt.datetime.now()
    days = max(1, int(days))
    end = now.timestamp()
    start = (now - _dt.timedelta(days=days)).timestamp()
    prev_start = (now - _dt.timedelta(days=2 * days)).timestamp()

    def _window(a: float, b: float) -> tuple[int, int]:
        errs = conn.execute(
            "SELECT COUNT(*) n FROM session_errors e JOIN sessions s USING(session_id) "
            "WHERE s.last_epoch >= ? AND s.last_epoch < ?", (a, b)).fetchone()["n"]
        sess = conn.execute(
            "SELECT COUNT(*) n FROM sessions WHERE last_epoch >= ? AND last_epoch < ?",
            (a, b)).fetchone()["n"]
        return int(errs or 0), int(sess or 0)

    cur_err, cur_sess = _window(start, end)
    prev_err, prev_sess = _window(prev_start, start)
    cur_rate = (cur_err / cur_sess) if cur_sess else 0.0
    prev_rate = (prev_err / prev_sess) if prev_sess else 0.0
    if prev_rate > 0:
        change = round((cur_rate - prev_rate) / prev_rate * 100.0, 1)
    else:
        change = 0.0 if cur_rate == 0 else 100.0
    return {
        "days": days,
        "errors": cur_err,
        "sessions": cur_sess,
        "per_session": round(cur_rate, 3),
        "prev_per_session": round(prev_rate, 3),
        "change_pct": change,
    }
