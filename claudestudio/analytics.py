"""Aggregations for the Analytics, Timeline, and Projects views.

Everything here is a pure read over the SQLite index — deterministic, no I/O
beyond the query, so the same functions back the HTTP API, the CLI, and tests.
"""

from __future__ import annotations

import sqlite3

from . import parser, pricing


def overview(conn: sqlite3.Connection) -> dict:
    base = conn.execute(
        """SELECT COUNT(*) sessions,
                  COALESCE(SUM(msg_count),0) messages,
                  COALESCE(SUM(tool_calls),0) tool_calls,
                  COALESCE(SUM(input_tokens),0) input_tokens,
                  COALESCE(SUM(output_tokens),0) output_tokens,
                  COALESCE(SUM(cache_write),0) cache_write,
                  COALESCE(SUM(cache_read),0) cache_read,
                  COALESCE(SUM(cost_usd),0) cost_usd,
                  COALESCE(SUM(duration_s),0) duration_s,
                  COUNT(DISTINCT project) projects
           FROM sessions"""
    ).fetchone()
    out = dict(base)
    out["tokens"] = (
        out["input_tokens"] + out["output_tokens"]
        + out["cache_write"] + out["cache_read"]
    )

    out["by_model"] = by_model(conn)
    out["by_tool"] = by_tool(conn, limit=15)
    out["daily"] = daily_activity(conn)
    out["heatmap"] = heatmap(conn)
    out["top_projects"] = top_projects(conn, limit=8)
    out["unpriced_models"] = [
        r["primary_model"]
        for r in conn.execute(
            "SELECT DISTINCT primary_model FROM sessions WHERE primary_model<>''"
        )
        if not pricing.is_priced(r["primary_model"])
    ]
    return out


def by_model(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT model,
                  COUNT(*) messages,
                  COALESCE(SUM(input_tokens),0) input_tokens,
                  COALESCE(SUM(output_tokens),0) output_tokens,
                  COALESCE(SUM(cache_write),0) cache_write,
                  COALESCE(SUM(cache_read),0) cache_read,
                  COALESCE(SUM(cost_usd),0) cost_usd
           FROM messages WHERE model IS NOT NULL AND model<>''
           GROUP BY model ORDER BY cost_usd DESC"""
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["family"] = pricing.family_of(r["model"])
        d["priced"] = pricing.is_priced(r["model"])
        d["tokens"] = (
            d["input_tokens"] + d["output_tokens"]
            + d["cache_write"] + d["cache_read"]
        )
        result.append(d)
    return result


def by_tool(conn, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT name,
                  COUNT(*) calls,
                  COALESCE(SUM(is_error),0) errors
           FROM tool_calls GROUP BY name ORDER BY calls DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def daily_activity(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT last_epoch, msg_count, cost_usd, tool_calls FROM sessions "
        "WHERE last_epoch > 0"
    ).fetchall()
    buckets: dict[str, dict] = {}
    for r in rows:
        d = parser.local_datetime(r["last_epoch"])
        if d is None:  # corrupt / far-future epoch: leave it off the time chart
            continue
        day = d.strftime("%Y-%m-%d")
        b = buckets.setdefault(
            day, {"date": day, "sessions": 0, "messages": 0, "cost_usd": 0.0, "tool_calls": 0}
        )
        b["sessions"] += 1
        b["messages"] += r["msg_count"] or 0
        b["cost_usd"] += r["cost_usd"] or 0.0
        b["tool_calls"] += r["tool_calls"] or 0
    return [buckets[k] for k in sorted(buckets)]


def heatmap(conn) -> list[list[int]]:
    """7x24 grid (weekday x hour) of session activity."""
    grid = [[0] * 24 for _ in range(7)]
    for r in conn.execute("SELECT last_epoch FROM sessions WHERE last_epoch>0"):
        d = parser.local_datetime(r["last_epoch"])
        if d is None:  # corrupt / far-future epoch: skip, don't crash the grid
            continue
        grid[d.weekday()][d.hour] += 1
    return grid


def top_projects(conn, limit: int = 12) -> list[dict]:
    rows = conn.execute(
        """SELECT project, project_name,
                  COUNT(*) sessions,
                  COALESCE(SUM(msg_count),0) messages,
                  COALESCE(SUM(tool_calls),0) tool_calls,
                  COALESCE(SUM(cost_usd),0) cost_usd,
                  COALESCE(SUM(input_tokens+output_tokens+cache_write+cache_read),0) tokens,
                  MAX(last_epoch) last_epoch,
                  MIN(first_epoch) first_epoch
           FROM sessions GROUP BY project
           ORDER BY sessions DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def projects(conn) -> list[dict]:
    return top_projects(conn, limit=10_000)


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile of an already-sorted list (p in 0..1)."""
    if not sorted_vals:
        return 0.0
    import math
    k = max(1, math.ceil(p * len(sorted_vals)))
    return sorted_vals[min(k, len(sorted_vals)) - 1]


def tool_latency(conn) -> dict:
    """Per-tool latency (ms), derived from message timestamps in the index.

    A tool call's start is its (assistant) message's timestamp; its end is the
    next timestamped message in the same session — the turn that carried the
    tool result. Calls without a usable start/end pair are skipped (never
    crashes). Returns ``{tool_name: {count, p50_ms, p95_ms, p99_ms, max_ms,
    mean_ms}}`` so the Tools dashboard can rank tools by how slow they are.
    """
    rows = conn.execute(
        """SELECT t.name AS name, m.epoch AS start_epoch,
                  (SELECT MIN(m2.epoch) FROM messages m2
                   WHERE m2.session_id = t.session_id
                     AND m2.seq > t.seq AND m2.epoch > 0) AS end_epoch
           FROM tool_calls t
           JOIN messages m ON m.session_id = t.session_id AND m.seq = t.seq
           WHERE m.epoch > 0"""
    ).fetchall()
    buckets: dict[str, list] = {}
    for r in rows:
        start, end = r["start_epoch"], r["end_epoch"]
        if not start or not end or end <= start:
            continue
        buckets.setdefault(r["name"], []).append((end - start) * 1000.0)
    out: dict[str, dict] = {}
    for name, vals in buckets.items():
        vals.sort()
        out[name] = {
            "count": len(vals),
            "p50_ms": round(_percentile(vals, 0.50), 1),
            "p95_ms": round(_percentile(vals, 0.95), 1),
            "p99_ms": round(_percentile(vals, 0.99), 1),
            "max_ms": round(vals[-1], 1),
            "mean_ms": round(sum(vals) / len(vals), 1),
        }
    # slowest p95 first — the order the dashboard wants
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["p95_ms"]))
