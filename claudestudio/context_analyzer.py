"""Context-window efficiency analyzer.

Shows, turn by turn, how full Claude's context window was when the model was
invoked — a proxy for whether a developer is using the window efficiently or
fragmenting work across many tiny sessions. Pure standard library.
"""

from __future__ import annotations

from . import pricing

# Every current Claude model exposes a 200K-token context window. Kept as a small
# explicit map plus a default so a new slug still gets a sane limit.
MODEL_CONTEXT_LIMITS = {
    "claude-opus-4-8": 200000,
    "claude-opus-4-6": 200000,
    "claude-sonnet-4-6": 200000,
    "claude-haiku-4-5": 200000,
    "claude-3-5-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3-opus": 200000,
}
DEFAULT_CONTEXT_LIMIT = 200000

_WASTE_TURN_PCT = 10.0       # a turn under this % is "wasteful"
_WASTE_SESSION_RATIO = 0.30  # >30% of wasteful turns flags the session


def model_context_limit(model: str | None) -> int:
    norm = pricing.normalize(model)
    return MODEL_CONTEXT_LIMITS.get(norm, DEFAULT_CONTEXT_LIMIT)


def _rating(pct: float) -> str:
    if pct < _WASTE_TURN_PCT:
        return "low"
    if pct < 60.0:
        return "moderate"
    return "high"


def analyze_session(conn, session_id: str) -> dict:
    sess = conn.execute(
        "SELECT primary_model FROM sessions WHERE session_id=?", (session_id,)  # SAFE
    ).fetchone()
    if sess is None:
        return {"error": f"no session with id {session_id!r}"}
    model = sess["primary_model"]
    limit = model_context_limit(model)

    turns = []
    pcts: list[float] = []
    rows = conn.execute(
        "SELECT input_tokens, output_tokens, cache_read FROM messages "
        "WHERE session_id=? AND role='assistant' ORDER BY seq",  # SAFE
        (session_id,),
    ).fetchall()
    for idx, r in enumerate(rows):
        tokens_in = (r["input_tokens"] or 0) + (r["cache_read"] or 0)
        tokens_out = r["output_tokens"] or 0
        pct = round(100.0 * tokens_in / limit, 1) if limit else 0.0
        pcts.append(pct)
        turns.append({
            "turn_index": idx,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "context_pct": pct,
            "model_limit": limit,
            "efficiency_rating": _rating(pct),
        })

    if pcts:
        avg = round(sum(pcts) / len(pcts), 1)
        peak = max(pcts)
        wasteful = sum(1 for p in pcts if p < _WASTE_TURN_PCT)
        waste = (wasteful / len(pcts)) > _WASTE_SESSION_RATIO
    else:
        avg = peak = 0.0
        waste = False

    return {
        "session_id": session_id,
        "model": model,
        "model_limit": limit,
        "turns": turns,
        "avg_utilization_pct": avg,
        "peak_utilization_pct": peak,
        "waste_indicator": waste,
    }


def ascii_chart(turns: list[dict], width: int = 30) -> str:
    """A tiny per-turn bar chart of context utilization."""
    if not turns:
        return "(no turns)"
    lines = []
    for t in turns:
        pct = t.get("context_pct", 0.0)
        filled = int(round(min(pct, 100.0) / 100.0 * width))
        bar = "#" * filled + "-" * (width - filled)
        lines.append(f"t{t.get('turn_index', 0):>3} |{bar}| {pct:5.1f}%")
    return "\n".join(lines)


def context_payload(conn, session_id: str) -> dict:
    return analyze_session(conn, session_id)


def backfill_utilization(conn) -> int:
    """Compute and store each session's average context utilization."""
    updated = 0
    ids = [r["session_id"] for r in conn.execute("SELECT session_id FROM sessions")]
    for sid in ids:
        res = analyze_session(conn, sid)
        if "error" in res:
            continue
        conn.execute(
            "UPDATE sessions SET context_utilization_pct=? WHERE session_id=?",  # SAFE
            (res["avg_utilization_pct"], sid),
        )
        updated += 1
    conn.commit()
    return updated


def efficiency_overview(conn, *, top: int = 5) -> dict:
    rows = []
    for r in conn.execute(
        "SELECT session_id, title, context_utilization_pct FROM sessions"  # SAFE
    ):
        pct = r["context_utilization_pct"]
        if pct is None:
            pct = analyze_session(conn, r["session_id"]).get("avg_utilization_pct", 0.0)
        rows.append({"session_id": r["session_id"],
                     "title": r["title"] or r["session_id"], "avg_pct": pct})
    if not rows:
        return {"avg_utilization_pct": 0.0, "peak_utilization_pct": 0.0,
                "wasted_sessions": []}
    pcts = [r["avg_pct"] for r in rows]
    wasted = sorted(rows, key=lambda r: r["avg_pct"])[:top]
    return {
        "avg_utilization_pct": round(sum(pcts) / len(pcts), 1),
        "peak_utilization_pct": round(max(pcts), 1),
        "wasted_sessions": wasted,
    }


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _mk_session(conn, sid, model, turns):
    """turns = list of (input_tokens, output_tokens, cache_read)."""
    conn.execute("INSERT INTO sessions(session_id,title,primary_model) VALUES(?,?,?)",
                 (sid, f"S {sid}", model))
    for seq, (i, o, cr) in enumerate(turns):
        conn.execute(
            "INSERT INTO messages(uuid,session_id,role,seq,input_tokens,output_tokens,"
            "cache_read) VALUES(?,?,?,?,?,?,?)",
            (f"{sid}-{seq}", sid, "assistant", seq, i, o, cr))
    conn.commit()


def selftest(c) -> None:
    import os
    import tempfile

    from . import index

    # --- model limits ---------------------------------------------------
    c.eq(model_context_limit("claude-haiku-4-5-20251001"), 200000, "haiku limit 200k")
    c.eq(model_context_limit("claude-opus-4-8"), 200000, "opus limit 200k")
    c.eq(model_context_limit("claude-sonnet-4-6"), 200000, "sonnet limit 200k")
    c.eq(model_context_limit("claude-unknown-99"), DEFAULT_CONTEXT_LIMIT,
         "unknown model gets default limit")
    c.eq(model_context_limit(None), DEFAULT_CONTEXT_LIMIT, "None model gets default")

    c.eq(_rating(5.0), "low", "rating <10 is low")
    c.eq(_rating(30.0), "moderate", "rating <60 is moderate")
    c.eq(_rating(90.0), "high", "rating >=60 is high")

    with tempfile.TemporaryDirectory() as tmp:
        conn = index.connect(os.path.join(tmp, "ctx.db"))
        try:
            # 5 small turns: 1000 in, 0 cache -> 0.5% each (all < 10%)
            _mk_session(conn, "small", "claude-opus-4-8",
                        [(1000, 100, 0)] * 5)
            res = analyze_session(conn, "small")
            c.eq(len(res["turns"]), 5, "5 assistant turns analyzed")
            c.close(res["turns"][0]["context_pct"], 0.5, "1000/200000 = 0.5% to 1dp")
            c.eq(res["turns"][0]["tokens_in"], 1000, "tokens_in captured")
            c.eq(res["turns"][0]["tokens_out"], 100, "tokens_out captured")
            c.eq(res["turns"][0]["efficiency_rating"], "low", "small turn rated low")
            c.close(res["avg_utilization_pct"], 0.5, "avg utilization 0.5%")
            c.close(res["peak_utilization_pct"], 0.5, "peak utilization 0.5%")
            c.eq(res["waste_indicator"], True, "all-small session flags waste")
            c.eq(res["model"], "claude-opus-4-8", "model echoed")
            c.eq(res["model_limit"], 200000, "limit echoed")

            # cache_read counts toward context
            _mk_session(conn, "cache", "claude-opus-4-8", [(1000, 50, 19000)])
            rc = analyze_session(conn, "cache")
            c.close(rc["turns"][0]["context_pct"], 10.0,
                    "input+cache_read = 20000 -> 10.0%")

            # large turns -> not wasteful
            _mk_session(conn, "big", "claude-opus-4-8",
                        [(100000, 2000, 0), (120000, 3000, 0)])
            rb = analyze_session(conn, "big")
            c.close(rb["turns"][0]["context_pct"], 50.0, "100000/200000 = 50%")
            c.eq(rb["turns"][0]["efficiency_rating"], "moderate", "50% rated moderate")
            c.eq(rb["waste_indicator"], False, "large-turn session not wasteful")
            c.close(rb["peak_utilization_pct"], 60.0, "peak is 60% (120000)")

            # ascii chart
            chart = ascii_chart(res["turns"])
            c.eq(len(chart.splitlines()), 5, "chart has one line per turn")
            c.ok("0.5%" in chart, "chart labels each turn's pct")
            c.ok("#" in ascii_chart(rb["turns"]), "chart draws bars for big turns")
            c.eq(ascii_chart([]), "(no turns)", "empty chart message")

            # backfill + overview
            n = backfill_utilization(conn)
            c.eq(n, 3, "backfill updates all 3 sessions")
            col = conn.execute(
                "SELECT context_utilization_pct FROM sessions WHERE session_id='small'"
            ).fetchone()[0]
            c.close(col, 0.5, "backfill writes avg to the column")
            ov = efficiency_overview(conn, top=2)
            c.ok("avg_utilization_pct" in ov, "overview has avg")
            c.ok("peak_utilization_pct" in ov, "overview has peak")
            c.eq(len(ov["wasted_sessions"]), 2, "overview lists top-2 wasted")
            c.eq(ov["wasted_sessions"][0]["session_id"], "small",
                 "lowest-utilization session ranked first")

            # unknown session
            c.ok("error" in analyze_session(conn, "nope"), "unknown session -> error")
            c.eq(context_payload(conn, "small")["session_id"], "small",
                 "context_payload returns analysis")
        finally:
            conn.close()
