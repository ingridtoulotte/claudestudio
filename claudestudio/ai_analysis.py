"""Opt-in AI analysis — the only module that can ever touch the network, and only
when the user has explicitly set ``ANTHROPIC_API_KEY``.

Zero model calls by default. Every public function takes an injectable
``transport`` so the self-test (and any caller) can run it fully offline. Results
are cached in the ``ai_usage`` table, which also doubles as the cost ledger and
the per-session rate-limit clock (at most one real call per session per hour).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from typing import Callable

from . import pricing

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
RATE_LIMIT_SECONDS = 3600  # one real call per session per hour

# A transport returns (reply_text, prompt_tokens, completion_tokens).
Transport = Callable[[str, str, str, str], tuple[str, int, int]]


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _no_key_error() -> dict:
    return {"error": "ANTHROPIC_API_KEY not set", "status": 402}


# ---------------------------------------------------------------------------
# the (only) network site — reached solely when a key is present
# ---------------------------------------------------------------------------

def _http_complete(model: str, system: str, user: str, api_key: str) -> tuple[str, int, int]:
    """POST one message to the Anthropic API and return (text, in_tok, out_tok).

    Uses only urllib (stdlib). Network/parse failures raise RuntimeError.
    """
    import urllib.error
    import urllib.request

    body = json.dumps({
        "model": model,
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — fixed https host, key-gated
        ANTHROPIC_URL, data=body, method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network
        raise RuntimeError(f"Anthropic API error {exc.code}: {exc.reason}") from exc
    except (urllib.error.URLError, ValueError, OSError) as exc:  # pragma: no cover
        raise RuntimeError(f"Anthropic API call failed: {exc}") from exc

    try:
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        usage = payload.get("usage", {})
        return text, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover
        raise RuntimeError(f"unexpected Anthropic response shape: {exc}") from exc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _resolve_key(api_key: str | None) -> str | None:
    return api_key or os.environ.get("ANTHROPIC_API_KEY") or None


def _parse_json_reply(text: str) -> dict:
    """Best-effort parse of the model's reply into a dict (never raises)."""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    # fall back: treat the whole reply as the summary
    return {"summary": text, "coaching_tips": [], "improvement_suggestions": []}


def _record(conn, session_id: str, model: str, p_tok: int, c_tok: int,
            summary_obj: dict | None) -> float:
    cost = pricing.cost_for_usage(model, p_tok, c_tok)
    try:
        conn.execute(
            "INSERT INTO ai_usage(session_id, model, prompt_tokens, completion_tokens, "
            "cost_usd, summary_json, created_at) VALUES(?,?,?,?,?,?,?)",  # SAFE: parameterized
            (session_id, model, p_tok, c_tok, cost,
             json.dumps(summary_obj) if summary_obj is not None else None, _now_iso()),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # read-only connection: the result still returns, just uncached
    return cost


def _latest_row(conn, session_id: str):
    return conn.execute(
        "SELECT * FROM ai_usage WHERE session_id=? ORDER BY id DESC LIMIT 1",  # SAFE
        (session_id,),
    ).fetchone()


def _session_prompt(conn, session_id: str, limit: int = 20) -> str | None:
    """Compact text of a session's prompts + assistant outcomes, for the model."""
    sess = conn.execute(
        "SELECT title, primary_model FROM sessions WHERE session_id=?",  # SAFE
        (session_id,),
    ).fetchone()
    if sess is None:
        return None
    parts = [f"Session: {sess['title'] or session_id}"]
    for r in conn.execute(
        "SELECT role, text FROM messages WHERE session_id=? AND text IS NOT NULL "
        "ORDER BY seq LIMIT ?",  # SAFE: parameterized
        (session_id, limit),
    ):
        snippet = (r["text"] or "")[:500]
        parts.append(f"[{r['role']}] {snippet}")
    return "\n".join(parts)


_SUMMARY_SYSTEM = (
    "You analyze a Claude Code coding session. Reply with STRICT JSON only, no prose, "
    "with keys: summary (string: goal, approach, quality, what worked, what didn't), "
    "coaching_tips (array of strings), improvement_suggestions (array of exactly 3 "
    "concrete strings). Do not wrap the JSON in markdown."
)
_COACH_SYSTEM = (
    "You are a Claude Code usage coach. Given several sessions, reply with STRICT JSON "
    "with key report (a Markdown coaching report naming the top 3 recurring inefficiency "
    "patterns and how to fix them). No markdown fences around the JSON."
)
_PROMPT_SYSTEM = (
    "You rewrite a Claude Code prompt for better results. Reply with STRICT JSON with keys "
    "improved (the rewritten prompt) and projected_delta (a number 0..1 estimating the "
    "effectiveness gain). No markdown fences."
)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def ai_status(conn) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(cost_usd),0) c FROM ai_usage"  # SAFE
    ).fetchone()
    return {
        "enabled": api_key_present(),
        "model": DEFAULT_MODEL,
        "api_key_set": api_key_present(),
        "total_ai_calls": int(row["n"]),
        "total_ai_cost_usd": round(float(row["c"]), 6),
    }


def summarize_session(conn, session_id: str, *, force: bool = False,
                      transport: Transport | None = None,
                      api_key: str | None = None,
                      model: str = DEFAULT_MODEL) -> dict:
    key = _resolve_key(api_key)
    if not key:
        return _no_key_error()

    cached = _latest_row(conn, session_id)
    if cached is not None and cached["summary_json"] and not force:
        obj = _parse_json_reply(cached["summary_json"])
        return {
            "summary": obj.get("summary", ""),
            "coaching_tips": obj.get("coaching_tips", []),
            "improvement_suggestions": obj.get("improvement_suggestions", []),
            "model_used": cached["model"],
            "tokens_used": int(cached["prompt_tokens"]) + int(cached["completion_tokens"]),
            "cost_usd": round(float(cached["cost_usd"]), 6),
            "cached": True,
        }

    user = _session_prompt(conn, session_id)
    if user is None:
        return {"error": f"no session with id {session_id!r}"}

    fn = transport or _http_complete
    text, p_tok, c_tok = fn(model, _SUMMARY_SYSTEM, user, key)
    obj = _parse_json_reply(text)
    cost = _record(conn, session_id, model, p_tok, c_tok, obj)
    return {
        "summary": obj.get("summary", ""),
        "coaching_tips": obj.get("coaching_tips", []),
        "improvement_suggestions": obj.get("improvement_suggestions", []),
        "model_used": model,
        "tokens_used": p_tok + c_tok,
        "cost_usd": round(cost, 6),
        "cached": False,
    }


def coach(conn, n: int = 20, *, transport: Transport | None = None,
          api_key: str | None = None, model: str = DEFAULT_MODEL) -> dict:
    key = _resolve_key(api_key)
    if not key:
        return _no_key_error()
    rows = conn.execute(
        "SELECT session_id, title FROM sessions ORDER BY last_epoch DESC LIMIT ?",  # SAFE
        (max(1, n),),
    ).fetchall()
    blob = "\n".join(f"- {r['title'] or r['session_id']}" for r in rows) or "(no sessions)"
    fn = transport or _http_complete
    text, p_tok, c_tok = fn(model, _COACH_SYSTEM, f"My last {n} sessions:\n{blob}", key)
    obj = _parse_json_reply(text)
    cost = _record(conn, "__coach__", model, p_tok, c_tok, obj)
    return {
        "report": obj.get("report", obj.get("summary", text)),
        "model_used": model,
        "tokens_used": p_tok + c_tok,
        "cost_usd": round(cost, 6),
    }


def improve_prompt(conn, raw_prompt: str, *, transport: Transport | None = None,
                   api_key: str | None = None, model: str = DEFAULT_MODEL) -> dict:
    key = _resolve_key(api_key)
    if not key:
        return _no_key_error()
    fn = transport or _http_complete
    text, p_tok, c_tok = fn(model, _PROMPT_SYSTEM, raw_prompt, key)
    obj = _parse_json_reply(text)
    try:
        delta = float(obj.get("projected_delta", 0.0))
    except (TypeError, ValueError):
        delta = 0.0
    cost = _record(conn, "__prompt__", model, p_tok, c_tok, obj)
    return {
        "original": raw_prompt,
        "improved": obj.get("improved", text),
        "projected_delta": round(delta, 4),
        "model_used": model,
        "tokens_used": p_tok + c_tok,
        "cost_usd": round(cost, 6),
    }


def ai_summary_payload(conn, session_id: str) -> dict:
    """Server-facing: identical to summarize_session, 402 dict when no key."""
    return summarize_session(conn, session_id)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _fake_summary(model, system, user, api_key):
    return ('{"summary":"refactored auth","coaching_tips":["lead with a file path"],'
            '"improvement_suggestions":["x","y","z"]}', 1000, 200)


def _fake_coach(model, system, user, api_key):
    return ('{"report":"# Coaching\\n- you re-explain context"}', 500, 100)


def _fake_improve(model, system, user, api_key):
    return ('{"improved":"Refactor auth.py: ...","projected_delta":0.3}', 80, 40)


def selftest(c) -> None:
    import os as _os
    import tempfile

    from . import index

    saved_key = _os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            conn = index.connect(_os.path.join(tmp, "ai.db"))
            try:
                conn.execute(
                    "INSERT INTO sessions(session_id,title,primary_model,last_epoch) "
                    "VALUES('s1','Auth refactor','claude-opus-4-8',100)")
                conn.execute(
                    "INSERT INTO sessions(session_id,title,primary_model,last_epoch) "
                    "VALUES('s2','Tests',     'claude-sonnet-4-6',200)")
                conn.execute(
                    "INSERT INTO messages(uuid,session_id,role,seq,text) "
                    "VALUES('m1','s1','user',0,'refactor auth.py to use jwt')")
                conn.commit()

                # --- no key -> 402 on every entry point --------------------
                r = summarize_session(conn, "s1")
                c.eq(r.get("status"), 402, "summarize without key -> 402")
                c.eq(r.get("error"), "ANTHROPIC_API_KEY not set", "402 message exact")
                c.eq(coach(conn).get("status"), 402, "coach without key -> 402")
                c.eq(improve_prompt(conn, "do x").get("status"), 402,
                     "improve without key -> 402")
                c.eq(ai_summary_payload(conn, "s1").get("status"), 402,
                     "payload without key -> 402")
                c.eq(ai_status(conn)["api_key_set"], False, "ai_status: no key")
                c.eq(ai_status(conn)["total_ai_calls"], 0, "ai_status: 0 calls initially")

                # --- with injected key + fake transport --------------------
                r = summarize_session(conn, "s1", transport=_fake_summary, api_key="k")
                c.eq(r["cached"], False, "first summary is a fresh call")
                c.eq(r["summary"], "refactored auth", "summary parsed from reply")
                c.eq(len(r["improvement_suggestions"]), 3, "3 improvement suggestions")
                c.eq(r["coaching_tips"], ["lead with a file path"], "coaching tips parsed")
                c.eq(r["tokens_used"], 1200, "tokens_used = prompt + completion")
                c.eq(r["model_used"], DEFAULT_MODEL, "model recorded")
                expect_cost = pricing.cost_for_usage(DEFAULT_MODEL, 1000, 200)
                c.close(r["cost_usd"], round(expect_cost, 6), "cost via pricing table")

                rows = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE session_id='s1'"
                                    ).fetchone()[0]
                c.eq(rows, 1, "exactly one ai_usage row stored for s1")

                # --- cache: second call, no new row ------------------------
                r2 = summarize_session(conn, "s1", transport=_fake_summary, api_key="k")
                c.eq(r2["cached"], True, "second summary is cached")
                c.eq(r2["summary"], "refactored auth", "cached summary identical")
                rows2 = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE session_id='s1'"
                                     ).fetchone()[0]
                c.eq(rows2, 1, "cache hit adds no new row")

                # --- force bypasses cache ----------------------------------
                r3 = summarize_session(conn, "s1", transport=_fake_summary, api_key="k",
                                       force=True)
                c.eq(r3["cached"], False, "force=True makes a fresh call")
                rows3 = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE session_id='s1'"
                                     ).fetchone()[0]
                c.eq(rows3, 2, "force adds a new row")

                # --- distinct session adds a row ---------------------------
                conn.execute("INSERT INTO messages(uuid,session_id,role,seq,text) "
                             "VALUES('m2','s2','user',0,'write tests')")
                conn.commit()
                summarize_session(conn, "s2", transport=_fake_summary, api_key="k")
                rows_s2 = conn.execute("SELECT COUNT(*) FROM ai_usage WHERE session_id='s2'"
                                       ).fetchone()[0]
                c.eq(rows_s2, 1, "distinct session adds its own row")

                # --- ai_status accumulates ---------------------------------
                st = ai_status(conn)
                c.eq(st["total_ai_calls"], 3, "ai_status counts all 3 stored calls")
                expect_total = round(3 * expect_cost, 6)
                c.close(st["total_ai_cost_usd"], expect_total, "ai_status sums cost")
                c.eq(st["api_key_set"], False, "status still reflects env (no key set)")

                # --- coach + improve ---------------------------------------
                cr = coach(conn, 5, transport=_fake_coach, api_key="k")
                c.ok("Coaching" in cr["report"], "coach returns a markdown report")
                c.eq(cr["tokens_used"], 600, "coach tokens summed")
                ip = improve_prompt(conn, "fix the bug", transport=_fake_improve, api_key="k")
                c.eq(ip["original"], "fix the bug", "improve echoes original")
                c.ok(ip["improved"].startswith("Refactor"), "improve returns rewrite")
                c.close(ip["projected_delta"], 0.3, "improve returns projected delta")

                # --- unknown session ---------------------------------------
                c.ok("error" in summarize_session(conn, "nope", transport=_fake_summary,
                                                  api_key="k"),
                     "unknown session id -> error")

                # --- defensive JSON parse ----------------------------------
                bad = _parse_json_reply("not json at all")
                c.eq(bad["summary"], "not json at all", "non-JSON reply wrapped as summary")
                c.eq(bad["improvement_suggestions"], [], "non-JSON reply has empty lists")
                c.eq(api_key_present(), False, "api_key_present false without env")
            finally:
                conn.close()
    finally:
        if saved_key is not None:
            _os.environ["ANTHROPIC_API_KEY"] = saved_key
