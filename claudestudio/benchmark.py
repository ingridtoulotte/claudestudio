"""Benchmark — am I getting more efficient with Claude Code over time?

Compares the current period against the previous one (week / month / quarter) and
reports a signed delta per metric, an overall trend, and a plain-English verdict.
The headline metric is **output tokens per dollar** (efficiency): more useful work
per unit spend means you're getting better at driving Claude Code.

Pure read over the local index — no model calls, no network.

Usage::

    from claudestudio.benchmark import compute_benchmark
    b = compute_benchmark(conn, mode="week")
    print(b["verdict"])   # "🚀 Your best week yet: +23% output per dollar…"
"""

from __future__ import annotations

import json
import time

_SPAN_DAYS = {"week": 7, "month": 30, "quarter": 90}
_EDIT_TOOLS = {
    "Edit", "MultiEdit", "Update", "str_replace_based_edit", "str_replace_editor",
    "Write", "create_file", "write_to_file", "NotebookEdit",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")

_IMPROVE_THRESHOLD = 5.0   # % change in output_per_dollar for "improving"
_DECLINE_THRESHOLD = -5.0


def compute_benchmark(conn, mode: str = "week", *, now: float | None = None) -> dict:
    """Current vs previous period comparison. `mode` ∈ {week, month, quarter}.

    `now` overrides the reference time (the self-test pins it for determinism);
    in normal use it defaults to wall-clock time.
    """
    mode = (mode or "week").strip().lower()
    if mode not in _SPAN_DAYS:
        mode = "week"
    span = _SPAN_DAYS[mode] * 86400.0
    ref = time.time() if now is None else float(now)

    cur = _period_metrics(conn, ref - span, ref)
    prev = _period_metrics(conn, ref - 2 * span, ref - span)
    delta = {k: _pct(cur[k], prev[k]) for k in cur}
    trend = _trend(delta["output_per_dollar"])
    verdict, highlights = _verdict(mode, cur, prev, delta, trend)

    return {
        "mode": mode,
        "current": cur,
        "previous": prev,
        "delta": delta,
        "trend": trend,
        "verdict": verdict,
        "highlights": highlights,
    }


def _period_metrics(conn, lo: float, hi: float) -> dict:
    """All nine period metrics for sessions whose last activity is in [lo, hi)."""
    rows = conn.execute(
        "SELECT session_id, project, input_tokens, output_tokens, cost_usd, "
        "       health_score FROM sessions WHERE last_epoch >= ? AND last_epoch < ?",
        (lo, hi),
    ).fetchall()
    sessions = len(rows)
    tokens_input = sum(r["input_tokens"] or 0 for r in rows)
    tokens_output = sum(r["output_tokens"] or 0 for r in rows)
    cost = sum(r["cost_usd"] or 0.0 for r in rows)
    projects = {r["project"] for r in rows if r["project"]}
    healths = [r["health_score"] for r in rows if r["health_score"] is not None]
    avg_health = round(sum(healths) / len(healths), 1) if healths else 0.0
    output_per_dollar = round(tokens_output / cost, 1) if cost > 0 else 0.0

    success_rate, files_touched = _tool_metrics(conn, [r["session_id"] for r in rows])

    return {
        "sessions": sessions,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_usd": round(cost, 4),
        "output_per_dollar": output_per_dollar,
        "tool_success_rate": success_rate,
        "avg_health_score": avg_health,
        "unique_projects": len(projects),
        "files_touched": files_touched,
    }


def _tool_metrics(conn, session_ids: list) -> tuple[float, int]:
    """(tool_success_rate, distinct files touched) over the given sessions."""
    if not session_ids:
        return 0.0, 0
    placeholders = ",".join("?" * len(session_ids))
    rows = conn.execute(
        f"SELECT name, is_error, input_json FROM tool_calls "
        f"WHERE session_id IN ({placeholders})",
        session_ids,
    ).fetchall()
    total = len(rows)
    errors = sum(1 for r in rows if r["is_error"])
    success_rate = round((total - errors) / total, 4) if total else 0.0
    files = set()
    for r in rows:
        if r["name"] not in _EDIT_TOOLS:
            continue
        try:
            inp = json.loads(r["input_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(inp, dict):
            continue
        for k in _PATH_KEYS:
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                files.add(v.strip().replace("\\", "/").rsplit("/", 1)[-1])
                break
    return success_rate, len(files)


def _pct(cur, prev) -> float:
    """Signed percentage change from prev → cur. New-from-zero → +100; both 0 → 0."""
    if prev == 0:
        return 100.0 if cur else 0.0
    return round((cur - prev) / abs(prev) * 100.0, 1)


def _trend(opd_delta: float) -> str:
    if opd_delta > _IMPROVE_THRESHOLD:
        return "improving"
    if opd_delta < _DECLINE_THRESHOLD:
        return "declining"
    return "stable"


def _verdict(mode, cur, prev, delta, trend):
    opd = delta["output_per_dollar"]
    label = {"week": "week", "month": "month", "quarter": "quarter"}[mode]
    if prev["sessions"] == 0 and cur["sessions"] == 0:
        return (f"No activity this {label} or last — nothing to compare yet.", [])
    if prev["sessions"] == 0:
        return (f"🌱 First tracked {label}: {cur['sessions']} sessions, "
                f"{cur['output_per_dollar']:,.0f} output tokens per dollar. "
                f"Baseline set.", [])
    if trend == "improving":
        verdict = (f"🚀 Stronger {label}: {_signed(opd)}% output per dollar "
                   f"vs the previous {label}.")
    elif trend == "declining":
        verdict = (f"📉 Softer {label}: {_signed(opd)}% output per dollar "
                   f"vs the previous {label}.")
    else:
        verdict = (f"→ Steady {label}: output per dollar is roughly flat "
                   f"({_signed(opd)}%).")
    highlights = []
    for metric, nice in (("sessions", "sessions"), ("cost_usd", "spend"),
                         ("avg_health_score", "avg health"),
                         ("tool_success_rate", "tool success"),
                         ("files_touched", "files touched")):
        d = delta[metric]
        if abs(d) >= 10:
            highlights.append(f"{nice}: {_signed(d)}%")
    return verdict, highlights


def _signed(v) -> str:
    return f"+{v}" if v >= 0 else f"{v}"
