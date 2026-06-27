"""Per-model analytics — cost, speed and quality, broken down by Claude model.

Side-by-side aggregates by exact model slug, plus a small deterministic
"recommender" that suggests which model fits short vs. long tasks based on the
user's own history, and a dependency-free SVG bar chart. Pure standard library.
"""

from __future__ import annotations

import datetime as _dt


def _this_month_prefix() -> str:
    now = _dt.datetime.now()
    return f"{now.year:04d}-{now.month:02d}"


def model_breakdown(conn) -> dict:
    """Aggregate every metric per ``primary_model``, sorted by total spend."""
    base = {}
    for r in conn.execute(
        "SELECT primary_model AS model, COUNT(*) AS n, "
        "COALESCE(SUM(cost_usd),0) AS cost, "
        "COALESCE(SUM(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)),0) AS toks, "
        "AVG(health_score) AS health "
        "FROM sessions WHERE primary_model IS NOT NULL AND primary_model<>'' "
        "GROUP BY primary_model"  # SAFE: no params
    ):
        base[r["model"]] = {
            "model": r["model"],
            "session_count": int(r["n"]),
            "total_cost_usd": round(float(r["cost"]), 4),
            "avg_cost_usd": round(float(r["cost"]) / r["n"], 4) if r["n"] else 0.0,
            "avg_health_score": round(float(r["health"]), 1) if r["health"] is not None else 0.0,
            "avg_tokens_per_session": round(float(r["toks"]) / r["n"], 1) if r["n"] else 0.0,
            "tool_success_rate": 1.0,
            "sessions_this_month": 0,
        }

    # tool success per model
    for r in conn.execute(
        "SELECT s.primary_model AS model, "
        "SUM(CASE WHEN t.is_error THEN 1 ELSE 0 END) AS errs, COUNT(*) AS total "
        "FROM tool_calls t JOIN sessions s ON t.session_id=s.session_id "
        "WHERE s.primary_model IS NOT NULL AND s.primary_model<>'' "
        "GROUP BY s.primary_model"  # SAFE
    ):
        if r["model"] in base and r["total"]:
            base[r["model"]]["tool_success_rate"] = round(
                1.0 - (int(r["errs"]) / int(r["total"])), 3)

    # sessions this calendar month
    prefix = _this_month_prefix() + "%"
    for r in conn.execute(
        "SELECT primary_model AS model, COUNT(*) AS n FROM sessions "
        "WHERE last_ts LIKE ? AND primary_model IS NOT NULL AND primary_model<>'' "
        "GROUP BY primary_model",  # SAFE: parameterized
        (prefix,),
    ):
        if r["model"] in base:
            base[r["model"]]["sessions_this_month"] = int(r["n"])

    models = sorted(base.values(), key=lambda m: m["total_cost_usd"], reverse=True)
    return {"models": models}


def recommender(conn) -> str:
    """A deterministic, history-grounded model recommendation sentence."""
    models = model_breakdown(conn)["models"]
    if not models:
        return ("Not enough history yet — run more sessions and ClaudeStudio will "
                "recommend a model for short vs. long tasks.")
    # cheapest by avg cost, and the highest-health model
    cheapest = min(models, key=lambda m: m["avg_cost_usd"])
    healthiest = max(models, key=lambda m: m["avg_health_score"])
    if cheapest["model"] == healthiest["model"]:
        return (f"Your data favours {cheapest['model']}: it is both your cheapest "
                f"(${cheapest['avg_cost_usd']:.4f}/session avg) and your "
                f"highest-quality model (health {healthiest['avg_health_score']}).")
    return (f"For short tasks, {cheapest['model']} costs "
            f"${cheapest['avg_cost_usd']:.4f}/session on average; for demanding work, "
            f"{healthiest['model']} scores highest on health "
            f"({healthiest['avg_health_score']}). Match the model to the task size.")


def svg_bar_chart(models: list[dict], *, metric: str = "total_cost_usd") -> str:
    """A self-contained SVG bar chart — one <rect> + <text> per model."""
    if not models:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    vals = [max(0.0, float(m.get(metric, 0.0))) for m in models]
    peak = max(vals) or 1.0
    bar_h, gap, left, top = 22, 10, 140, 20
    width = 520
    height = top + len(models) * (bar_h + gap) + 10
    chart_w = width - left - 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" font-family="sans-serif" font-size="12">']
    for i, m in enumerate(models):
        y = top + i * (bar_h + gap)
        w = int(chart_w * (vals[i] / peak))
        parts.append(f'<text x="8" y="{y + 15}">{_esc(m.get("model", "?"))}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{w}" height="{bar_h}" '
                     f'rx="3" fill="#9a8cff"></rect>')
        parts.append(f'<text x="{left + w + 6}" y="{y + 15}">'
                     f'{_fmt(vals[i], metric)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt(v: float, metric: str) -> str:
    if "cost" in metric:
        return f"${v:.4f}"
    if "rate" in metric:
        return f"{v:.3f}"
    return f"{v:.0f}"


def models_payload(conn, params: dict | None = None) -> dict:
    out = model_breakdown(conn)
    out["recommendation"] = recommender(conn)
    return out


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def selftest(c) -> None:
    import os
    import tempfile

    from . import index

    with tempfile.TemporaryDirectory() as tmp:
        conn = index.connect(os.path.join(tmp, "m.db"))
        try:
            # 10 sessions across 3 models with known cost/health/tokens
            specs = [
                ("opus", "claude-opus-4-8", 4, 1.00, 90, 5000),
                ("son", "claude-sonnet-4-6", 3, 0.30, 80, 3000),
                ("hai", "claude-haiku-4-5", 3, 0.05, 70, 1000),
            ]
            total_cost = 0.0
            n_total = 0
            for prefix, model, count, cost, health, toks in specs:
                for i in range(count):
                    sid = f"{prefix}{i}"
                    conn.execute(
                        "INSERT INTO sessions(session_id,title,primary_model,cost_usd,"
                        "health_score,input_tokens,output_tokens,msg_count,last_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (sid, sid, model, cost, health, toks, 0, 10, "2026-06-01T00:00:00"))
                    total_cost += cost
                    n_total += 1
            # tool calls for opus0: 4 total, 1 error -> success rate 0.75
            for k in range(4):
                conn.execute(
                    "INSERT INTO tool_calls(session_id,seq,name,is_error) VALUES(?,?,?,?)",
                    ("opus0", k, "Read", 1 if k == 0 else 0))
            conn.commit()

            bd = model_breakdown(conn)
            models = bd["models"]
            c.eq(len(models), 3, "3 distinct models")
            c.eq(sum(m["session_count"] for m in models), 10, "session counts sum to 10")
            c.close(sum(m["total_cost_usd"] for m in models), round(total_cost, 4),
                    "per-model cost sums to grand total")

            by = {m["model"]: m for m in models}
            opus = by["claude-opus-4-8"]
            c.eq(opus["session_count"], 4, "opus has 4 sessions")
            c.close(opus["total_cost_usd"], 4.0, "opus total cost 4.0")
            c.close(opus["avg_cost_usd"], 1.0, "opus avg cost 1.0")
            c.close(opus["avg_health_score"], 90.0, "opus avg health 90")
            c.close(opus["avg_tokens_per_session"], 5000.0, "opus avg tokens 5000")
            c.close(opus["tool_success_rate"], 0.75, "opus tool success 0.75 (1/4 errored)")
            c.eq(by["claude-haiku-4-5"]["tool_success_rate"], 1.0,
                 "model with no tool calls -> success 1.0")

            # sorted by spend desc
            costs = [m["total_cost_usd"] for m in models]
            c.ok(all(costs[i] >= costs[i + 1] for i in range(len(costs) - 1)),
                 "models sorted by total spend desc")
            c.eq(models[0]["model"], "claude-opus-4-8", "opus is top spender")

            # recommender
            rec = recommender(conn)
            c.ok(isinstance(rec, str) and len(rec) > 0, "recommender returns non-empty str")
            c.ok("haiku" in rec or "claude-haiku-4-5" in rec, "recommender cites cheapest")

            # svg
            svg = svg_bar_chart(models)
            c.ok("<svg" in svg, "svg has root element")
            c.eq(svg.count("<rect"), 3, "one rect per model")
            c.ok("claude-opus-4-8" in svg, "svg labels models")
            c.ok("$" in svg, "svg renders cost values")
            c.ok("<svg" in svg_bar_chart([]), "empty svg still valid")

            # payload
            pay = models_payload(conn)
            c.ok("models" in pay and "recommendation" in pay,
                 "payload has models + recommendation")
            c.eq(len(pay["models"]), 3, "payload lists 3 models")

            # empty index
            empty = index.connect(os.path.join(tmp, "e.db"))
            try:
                c.eq(model_breakdown(empty)["models"], [], "empty index -> no models")
                c.ok(len(recommender(empty)) > 0, "recommender non-empty on empty index")
            finally:
                empty.close()
        finally:
            conn.close()
