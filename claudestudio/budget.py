"""Budget tracker & spend alerts (Feature 3, v0.5.2).

Claude Code power users lose track of spend. This is a local, deterministic
spend ceiling: set "$50/month" and ClaudeStudio tells you where you stand —
computed entirely from the indexed ``sessions`` table, zero model calls, zero
network. The active budget lives in the ``budgets`` table (user-owned, survives
reindexing); status is recomputed live from session costs in the current
calendar period.
"""

from __future__ import annotations

import datetime as _dt
import time

# Fraction of the ceiling at which we start warning. Crossing it flips the
# ``alert`` flag the UI uses to raise a sticky banner.
ALERT_THRESHOLD = 0.75
_PERIODS = ("monthly", "weekly")


def _normalise_period(period: str | None) -> str:
    p = (period or "monthly").strip().lower()
    return p if p in _PERIODS else "monthly"


def set_budget(conn, period: str | None, ceiling_usd) -> dict:
    """Replace the active budget with a new ceiling. Returns the stored record."""
    period = _normalise_period(period)
    try:
        ceiling = max(0.0, float(ceiling_usd))
    except (TypeError, ValueError):
        ceiling = 0.0
    # One active budget: clear prior rows so `get_budget` is unambiguous. History
    # isn't needed for a single-user local tool, and it keeps status simple.
    conn.execute("DELETE FROM budgets")
    conn.execute(
        "INSERT INTO budgets(period, ceiling_usd, created_at) VALUES(?,?,?)",
        (period, ceiling, time.time()),
    )
    conn.commit()
    return {"period": period, "ceiling_usd": ceiling}


def get_budget(conn) -> dict | None:
    """The active budget, or None if none is set."""
    row = conn.execute(
        "SELECT period, ceiling_usd, created_at FROM budgets "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "period": _normalise_period(row["period"]),
        "ceiling_usd": float(row["ceiling_usd"] or 0.0),
        "created_at": row["created_at"],
    }


def clear_budget(conn) -> dict:
    """Remove the budget. Returns ``{cleared: bool}``."""
    cur = conn.execute("DELETE FROM budgets")
    conn.commit()
    return {"cleared": cur.rowcount > 0}


def _period_bounds(period: str, now: _dt.datetime) -> tuple[float, float, int]:
    """(start_epoch, end_epoch, days_remaining) for the calendar period at `now`."""
    if period == "weekly":
        start_day = (now - _dt.timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_day = start_day + _dt.timedelta(days=7)
    else:  # monthly
        start_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start_day.month == 12:
            end_day = start_day.replace(year=start_day.year + 1, month=1)
        else:
            end_day = start_day.replace(month=start_day.month + 1)
    days_remaining = max(0, (end_day - now).days)
    return start_day.timestamp(), end_day.timestamp(), days_remaining


def _spend_in_window(conn, start_epoch: float, end_epoch: float) -> tuple[float, int]:
    """(cost_usd, session_count) for sessions active within [start, end)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd),0) c, COUNT(*) n FROM sessions "
        "WHERE last_epoch >= ? AND last_epoch < ?",
        (start_epoch, end_epoch),
    ).fetchone()
    return float(row["c"] or 0.0), int(row["n"] or 0)


def budget_status(conn, now: _dt.datetime | None = None) -> dict:
    """Current spend vs the active budget.

    Returns a stable shape whether or not a budget is set, so the UI/MCP never
    branch on missing keys. `now` is injectable for deterministic tests.
    """
    now = now or _dt.datetime.now()
    budget = get_budget(conn)
    period = budget["period"] if budget else "monthly"
    ceiling = budget["ceiling_usd"] if budget else 0.0

    start_epoch, end_epoch, days_remaining = _period_bounds(period, now)
    spent, sessions = _spend_in_window(conn, start_epoch, end_epoch)

    percent = (spent / ceiling * 100.0) if ceiling > 0 else 0.0
    remaining = (ceiling - spent) if ceiling > 0 else 0.0
    alert = bool(ceiling > 0 and percent >= ALERT_THRESHOLD * 100.0)
    return {
        "has_budget": budget is not None,
        "period": period,
        "ceiling_usd": round(ceiling, 4),
        "spent_usd": round(spent, 4),
        "percent": round(percent, 2),
        "remaining_usd": round(remaining, 4),
        "sessions_this_period": sessions,
        "days_remaining": days_remaining,
        "alert": alert,
    }


# ---------------------------------------------------------------------------
# spend forecasting (v0.6.2) — project where this pace lands, and where the
# waste is. All deterministic, all from the local `sessions` table.
# ---------------------------------------------------------------------------

_FORECAST_WINDOW_DAYS = 30


def forecast(conn, now: _dt.datetime | None = None) -> dict:
    """Project end-of-month spend and surface the biggest cost driver & waste.

    Based on the trailing 30 days of session cost:
      * ``projected_month_usd`` — this month's spend if the current daily pace holds
      * ``biggest_driver``      — the project that cost the most in the window
      * ``sessions_until_limit``— how many average-cost sessions until the active
                                  budget ceiling is hit (-1 when no budget is set)
      * ``opportunity``         — the project with the worst cost-per-health-point
                                  (expensive *and* low-health = the wasteful pattern)
    """
    now = now or _dt.datetime.now()
    window_start = (now - _dt.timedelta(days=_FORECAST_WINDOW_DAYS)).timestamp()

    rows = conn.execute(
        "SELECT COALESCE(project_name, project, '(unknown)') p, "
        "       COALESCE(cost_usd,0) c, COALESCE(health_score,0) h "
        "FROM sessions WHERE last_epoch >= ? AND last_epoch < ?",
        (window_start, now.timestamp()),
    ).fetchall()

    empty = {
        "projected_month_usd": 0.0, "biggest_driver": "", "sessions_until_limit": -1,
        "daily_rate_usd": 0.0, "window_days": _FORECAST_WINDOW_DAYS,
        "window_sessions": 0,
        "opportunity": {"project": "", "cost_per_health_point": 0.0,
                        "avg_health": 0.0, "cost_usd": 0.0},
    }
    if not rows:
        return empty

    total_cost = sum(float(r["c"] or 0.0) for r in rows)
    by_project: dict[str, dict] = {}
    for r in rows:
        p = r["p"]
        d = by_project.setdefault(p, {"cost": 0.0, "health_sum": 0.0, "n": 0})
        d["cost"] += float(r["c"] or 0.0)
        d["health_sum"] += float(r["h"] or 0.0)
        d["n"] += 1

    daily = total_cost / _FORECAST_WINDOW_DAYS
    _, _, days_in_month = _period_bounds("monthly", now)
    days_in_month_total = days_in_month + now.day  # remaining + elapsed
    projected_month = daily * days_in_month_total

    biggest_driver = max(by_project, key=lambda k: (by_project[k]["cost"], k))

    # cost-per-health-point: expensive sessions that scored low are the waste.
    def _cphp(d: dict) -> float:
        avg_health = (d["health_sum"] / d["n"]) if d["n"] else 0.0
        return d["cost"] / max(1.0, avg_health)

    worst = max(by_project, key=lambda k: (_cphp(by_project[k]), k))
    wd = by_project[worst]
    avg_health = round((wd["health_sum"] / wd["n"]) if wd["n"] else 0.0, 1)

    # sessions until the active ceiling is hit, at the window's average cost.
    sessions_until = -1
    status = budget_status(conn, now)
    if status["has_budget"] and status["ceiling_usd"] > 0:
        avg_cost = total_cost / len(rows) if rows else 0.0
        remaining = max(0.0, status["remaining_usd"])
        sessions_until = int(remaining / avg_cost) if avg_cost > 0 else -1

    return {
        "projected_month_usd": round(projected_month, 2),
        "biggest_driver": biggest_driver,
        "sessions_until_limit": sessions_until,
        "daily_rate_usd": round(daily, 4),
        "window_days": _FORECAST_WINDOW_DAYS,
        "window_sessions": len(rows),
        "opportunity": {
            "project": worst,
            "cost_per_health_point": round(_cphp(wd), 4),
            "avg_health": avg_health,
            "cost_usd": round(wd["cost"], 4),
        },
    }
