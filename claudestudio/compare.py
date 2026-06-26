"""Two-session comparison — "what changed between attempts?" (v0.6.2).

Compares any two indexed sessions and produces a structured, deterministic diff:
prompts unique to each side, files touched by both, and the deltas in cost,
tokens and health score — plus a plain-English verdict. No model calls, no
network; every number comes straight from the local index.
"""

from __future__ import annotations

import json

_EDIT_TOOLS = {
    "Edit", "MultiEdit", "Update", "str_replace_based_edit", "str_replace_editor",
    "Write", "create_file", "write_to_file", "NotebookEdit",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")


def _summary(conn, sid: str) -> dict | None:
    r = conn.execute(
        "SELECT session_id, title, project_name, cost_usd, health_score, "
        "       msg_count, tool_calls, "
        "       COALESCE(input_tokens,0)+COALESCE(output_tokens,0)"
        "       +COALESCE(cache_write,0)+COALESCE(cache_read,0) AS tokens "
        "FROM sessions WHERE session_id=?",
        (str(sid),),
    ).fetchone()
    if not r:
        return None
    return {
        "session_id": r["session_id"], "title": r["title"] or "",
        "project_name": r["project_name"] or "",
        "cost_usd": round(float(r["cost_usd"] or 0.0), 4),
        "health_score": int(r["health_score"] or 0),
        "msg_count": int(r["msg_count"] or 0),
        "tool_calls": int(r["tool_calls"] or 0),
        "tokens": int(r["tokens"] or 0),
    }


def _prompts(conn, sid: str) -> list[str]:
    """Real user prompts (tool-result turns carry no text, so they drop out)."""
    rows = conn.execute(
        "SELECT text FROM messages WHERE session_id=? AND role='user' "
        "AND text IS NOT NULL AND text != '' ORDER BY seq",
        (str(sid),),
    ).fetchall()
    out, seen = [], set()
    for r in rows:
        norm = " ".join((r["text"] or "").split())[:200]
        if norm and norm.lower() not in seen:
            seen.add(norm.lower())
            out.append(norm)
    return out


def _files(conn, sid: str) -> set[str]:
    rows = conn.execute(
        "SELECT name, input_json FROM tool_calls WHERE session_id=?", (str(sid),)
    ).fetchall()
    files: set[str] = set()
    for r in rows:
        if r["name"] not in _EDIT_TOOLS:
            continue
        try:
            inp = json.loads(r["input_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(inp, dict):
            continue
        for k in _PATH_KEYS:
            v = inp.get(k)
            if isinstance(v, str) and v.strip():
                files.add(v.strip().replace("\\", "/").rsplit("/", 1)[-1] or v.strip())
                break
    return files


def _tool_success(conn, sid: str) -> float:
    r = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(is_error),0) e FROM tool_calls WHERE session_id=?",
        (str(sid),),
    ).fetchone()
    n = int(r["n"] or 0)
    return 1.0 if n == 0 else (n - int(r["e"] or 0)) / n


def _verdict(a: dict, b: dict, succ_a: float, succ_b: float) -> str:
    if a["session_id"] == b["session_id"]:
        return "Same session — no differences."
    bits: list[str] = []
    if a["cost_usd"] > 0:
        pct = (a["cost_usd"] - b["cost_usd"]) / a["cost_usd"] * 100.0
        if abs(pct) >= 5:
            bits.append(f"Session B was {abs(pct):.0f}% "
                        f"{'cheaper' if pct > 0 else 'more expensive'}")
    if abs(succ_b - succ_a) >= 0.05:
        bits.append("a higher tool-success rate" if succ_b > succ_a
                    else "a lower tool-success rate")
    if b["health_score"] != a["health_score"]:
        bits.append(f"health {b['health_score']} vs {a['health_score']}")
    if not bits:
        return "The two sessions are close on cost, success rate and health."
    lead = bits[0]
    rest = bits[1:]
    sentence = lead + (" and " + ", ".join(rest) if rest else "")
    better = (b["cost_usd"] <= a["cost_usd"]) and (succ_b >= succ_a)
    tail = " — probably a better approach." if better else " — worth a closer look."
    return sentence + tail


def compare_sessions(conn, a: str, b: str) -> dict:
    """Structured diff of two sessions. Missing ids surface as ``{error: ...}``."""
    sa, sb = _summary(conn, a), _summary(conn, b)
    if sa is None or sb is None:
        missing = a if sa is None else b
        return {"error": f"no session with id {missing!r}", "a": sa, "b": sb}

    pa, pb = _prompts(conn, a), _prompts(conn, b)
    set_a = {p.lower() for p in pa}
    set_b = {p.lower() for p in pb}
    only_a = [p for p in pa if p.lower() not in set_b]
    only_b = [p for p in pb if p.lower() not in set_a]

    fa, fb = _files(conn, a), _files(conn, b)
    shared = sorted(fa & fb)

    succ_a, succ_b = _tool_success(conn, a), _tool_success(conn, b)

    return {
        "a": sa, "b": sb,
        "cost_delta_usd": round(sb["cost_usd"] - sa["cost_usd"], 4),
        "token_delta": sb["tokens"] - sa["tokens"],
        "health_delta": sb["health_score"] - sa["health_score"],
        "tool_success_a": round(succ_a, 4),
        "tool_success_b": round(succ_b, 4),
        "prompts_only_in_a": only_a,
        "prompts_only_in_b": only_b,
        "shared_files": shared,
        "files_only_in_a": sorted(fa - fb),
        "files_only_in_b": sorted(fb - fa),
        "verdict": _verdict(sa, sb, succ_a, succ_b),
    }
