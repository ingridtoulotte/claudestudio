"""MCP server — make ClaudeStudio queryable by Claude Code itself.

Run ``python -m claudestudio mcp`` (or the ``claudestudio-mcp`` entry point) to
expose your indexed session history over the Model Context Protocol via a
JSON-RPC 2.0 stdio transport. Any MCP client — Claude Code included — can then
search your history, pull a session, ask grounded questions, and read analytics.

Zero new dependencies: JSON-RPC is just structured JSON on stdin/stdout, and
every tool reuses the existing read-only query layer (`api`, `analytics`,
`index`, `ask`). Nothing here calls a model or the network — the server only
reads the local index, honouring the same local-first promise as the rest of the
app.

Wire shape (MCP / JSON-RPC 2.0):
    -> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
    <- {"jsonrpc":"2.0","id":1,"result":{...}}
    -> {"jsonrpc":"2.0","id":2,"method":"tools/call",
        "params":{"name":"search_sessions","arguments":{"query":"parser"}}}
    <- {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"…"}]}}
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, cast

from . import __version__, analytics, api, index

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "claudestudio", "version": __version__}

# JSON-RPC standard error codes we use.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# tool registry — every tool is a pure read over the local index
# ---------------------------------------------------------------------------

def _trim_session(d: dict) -> dict:
    """Keep an MCP session summary small and stable across endpoints."""
    return {
        "session_id": d.get("session_id"),
        "title": d.get("title"),
        "project": d.get("project"),
        "project_name": d.get("project_name"),
        "models": d.get("models"),
        "primary_model": d.get("primary_model"),
        "msg_count": d.get("msg_count"),
        "tool_calls": d.get("tool_calls"),
        "cost_usd": d.get("cost_usd"),
        "last_ts": d.get("last_ts"),
        "first_ts": d.get("first_ts"),
    }


def _t_search_sessions(conn, args: dict) -> dict:
    query = str(args.get("query") or "")
    limit = args.get("limit", 10)
    res = api.list_sessions(conn, {"q": query, "limit": limit, "archived": "all"})
    return {"query": query, "sessions": [_trim_session(s) for s in res["sessions"]],
            "total": res["total"]}


def _t_get_session(conn, args: dict) -> dict:
    sid = str(args.get("session_id") or "")
    detail = api.get_session_summary(conn, sid)
    if detail is None:
        return {"error": f"no session with id {sid!r}"}
    out = _trim_session(detail)
    out["by_tool"] = detail.get("by_tool", [])
    out["preview"] = detail.get("preview", "")
    out["git_branch"] = detail.get("git_branch", "")
    return out


def _t_get_session_annotations(conn, args: dict) -> dict:
    """Session-level notes attached via the UI/CLI (kept in `user_state.notes`)."""
    sid = str(args.get("session_id") or "")
    row = conn.execute(
        "SELECT notes FROM user_state WHERE session_id=?", (sid,)  # SAFE: parameterized
    ).fetchone()
    notes = (row["notes"] if row else "") or ""
    annotations = [{"session_id": sid, "body": notes}] if notes.strip() else []
    return {"session_id": sid, "annotations": annotations}


def _t_get_project_stats(conn, args: dict) -> dict:
    name = str(args.get("project_name") or "").strip()
    for p in analytics.projects(conn):
        if name and (p.get("project_name") == name or p.get("project") == name):
            return p
    return {"error": f"no project matching {name!r}"}


def _t_get_analytics_summary(conn, args: dict) -> dict:
    days = api._int_param(args.get("days"), 30, lo=1, hi=3650)
    ov = analytics.overview(conn)
    daily = ov.get("daily", [])
    window = {"days": days, "sessions": 0, "messages": 0, "cost_usd": 0.0, "tool_calls": 0}
    if daily:
        latest = daily[-1]["date"]
        import datetime as _dt
        try:
            cutoff = (_dt.datetime.strptime(latest, "%Y-%m-%d")
                      - _dt.timedelta(days=days - 1)).strftime("%Y-%m-%d")
        except ValueError:
            cutoff = ""
        for d in daily:
            if d["date"] >= cutoff:
                window["sessions"] += d["sessions"]
                window["messages"] += d["messages"]
                window["cost_usd"] += d["cost_usd"]
                window["tool_calls"] += d["tool_calls"]
    window["cost_usd"] = round(window["cost_usd"], 4)
    return {
        "all_time": {
            "sessions": ov["sessions"], "messages": ov["messages"],
            "tool_calls": ov["tool_calls"], "tokens": ov["tokens"],
            "cost_usd": round(ov["cost_usd"], 4), "projects": ov["projects"],
        },
        "window": window,
        "by_model": [
            {"model": m["model"], "family": m["family"],
             "cost_usd": round(m["cost_usd"], 4), "messages": m["messages"]}
            for m in ov.get("by_model", [])
        ],
    }


def _t_find_sessions_by_file(conn, args: dict) -> dict:
    return api.sessions_by_file(conn, str(args.get("file_path") or ""),
                                args.get("limit", 20))


def _t_get_recent_sessions(conn, args: dict) -> dict:
    limit = args.get("limit", 5)
    res = api.list_sessions(conn, {"limit": limit, "sort": "recent", "archived": "all"})
    return {"sessions": [_trim_session(s) for s in res["sessions"]]}


def _t_ask_history(conn, args: dict) -> dict:
    question = str(args.get("question") or "")
    session = args.get("session_id") or args.get("session") or None
    return api.ask(conn, question, session)


def _t_list_bookmarks(conn, args: dict) -> dict:
    session = args.get("session_id") or args.get("session") or None
    return api.list_bookmarks(conn, str(session) if session else None)


def _t_get_prompt_patterns(conn, args: dict) -> dict:
    min_count = api._int_param(args.get("min_count"), 3, lo=2, hi=1000)
    from . import patterns
    return {"patterns": patterns.extract_patterns(conn, min_count=min_count)}


# --- v0.5.2 tools (F9) -----------------------------------------------------

def _t_get_cost_by_period(conn, args: dict) -> dict:
    """Spend / tokens / session counts for the last N calendar periods."""
    period = str(args.get("period") or "monthly")
    n = api._int_param(args.get("n"), 6, lo=1, hi=120)
    return api.cost_by_period(conn, period, n)


def _t_get_diff_for_session(conn, args: dict) -> dict:
    """Every inline file diff in a session (optionally filtered to one file)."""
    sid = str(args.get("session_id") or "")
    file_path = args.get("file_path") or None
    return api.diffs_for_session(conn, sid, file_path)


def _t_get_annotations(conn, args: dict) -> dict:
    """The user's inline session/message annotations (the v0.5.2 notes table)."""
    sid = str(args.get("session_id") or "")
    return api.get_annotations(conn, sid)


def _t_generate_project_brief(conn, args: dict) -> dict:
    """A full onboarding brief for a project: stats + the CLAUDE.md profile."""
    pid = str(args.get("project_id") or args.get("project") or "")
    return api.project_brief(conn, pid)


# --- v0.6.0 tools (#15, #16) -----------------------------------------------

def _t_get_cross_refs(conn, args: dict) -> dict:
    """Prompts that reference an earlier session, with candidate target sessions."""
    return api.cross_refs(conn, {"limit": args.get("limit", 50)})


def _t_find_sessions_by_github_ref(conn, args: dict) -> dict:
    """Sessions that discussed a given GitHub issue/PR (#123, owner/repo#456)."""
    params = {}
    if args.get("ref"):
        params["q"] = str(args["ref"])
    if args.get("number") is not None:
        params["q"] = "#" + str(args["number"])
    if args.get("repo"):
        params["repo"] = str(args["repo"])
    return api.github_refs_search(conn, params)


# --- v0.6.1 tools (#17–#20) ------------------------------------------------

def _t_list_tags(conn, args: dict) -> dict:
    """All user-defined session tags with their live session counts."""
    from .tags import TagManager
    return {"tags": TagManager.list_tags(conn)}


def _t_get_session_tags(conn, args: dict) -> dict:
    """The tags applied to one session."""
    from .tags import TagManager
    sid = str(args.get("session_id") or "")
    return {"session_id": sid, "tags": TagManager.get_session_tags(conn, sid)}


def _t_get_session_narrative(conn, args: dict) -> dict:
    """A deterministic one-paragraph narrative of a session (no model calls)."""
    from . import narrative
    sid = str(args.get("session_id") or "")
    return narrative.narrative_for_session(conn, sid)


def _t_get_file_heatmap(conn, args: dict) -> dict:
    """The top-10 hottest files with heat_score, edit_count and session_count."""
    from . import file_heatmap
    return file_heatmap.top_files(
        conn, limit=10, project_id=args.get("project_id"),
        since=args.get("since"), until=args.get("until"))


# --- v0.6.2 tools (#21–#26, the "Insight Engine") --------------------------

def _t_generate_resume_brief(conn, args: dict) -> dict:
    """A copy-paste-ready brief to resume a session in a new Claude Code window."""
    return api.resume_brief(conn, str(args.get("session_id") or ""))


def _t_compare_sessions(conn, args: dict) -> dict:
    """Compare two sessions by cost, tokens, health, prompts and files touched."""
    return api.compare(conn, str(args.get("session_id_a") or ""),
                        str(args.get("session_id_b") or ""))


def _t_get_error_taxonomy(conn, args: dict) -> dict:
    """Error-type distribution across sessions, optionally scoped by project/date."""
    return api.error_taxonomy_payload(
        conn, {"project": args.get("project"), "since": args.get("since")})


def _t_verify_claude_md(conn, args: dict) -> dict:
    """Whether a project's CLAUDE.md claims match its actual session history."""
    return api.verify_claude_md(conn, str(args.get("project_id") or args.get("project") or ""))


def _t_search_by_error_type(conn, args: dict) -> dict:
    """Sessions containing errors of a given taxonomy type, most recent first."""
    return api.sessions_by_error_type(
        conn, str(args.get("error_type") or ""), args.get("limit", 20))


def _t_get_budget_forecast(conn, args: dict) -> dict:
    """Project end-of-month spend, biggest cost driver and efficiency opportunity."""
    return api.budget_forecast(conn)


TOOLS = [
    {
        "name": "search_sessions",
        "description": "Full-text search across all indexed Claude Code sessions (BM25 + title match). Returns session summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "words to search for"},
                "limit": {"type": "integer", "description": "max results (default 10)"},
            },
            "required": ["query"],
        },
        "handler": _t_search_sessions,
    },
    {
        "name": "get_session",
        "description": "Get a single session's summary: metadata, token/cost totals, and per-tool call counts.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
        "handler": _t_get_session,
    },
    {
        "name": "get_session_annotations",
        "description": "Get the user's notes attached to a session.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
        "handler": _t_get_session_annotations,
    },
    {
        "name": "get_project_stats",
        "description": "Aggregate stats for one project: session count, messages, tool calls, tokens, cost.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_name": {"type": "string"}},
            "required": ["project_name"],
        },
        "handler": _t_get_project_stats,
    },
    {
        "name": "get_analytics_summary",
        "description": "All-time and trailing-window analytics: sessions, messages, tokens, cost, and per-model spend.",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "window size in days (default 30)"}},
        },
        "handler": _t_get_analytics_summary,
    },
    {
        "name": "find_sessions_by_file",
        "description": "Find sessions whose tool calls referenced a given file (matched by basename).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["file_path"],
        },
        "handler": _t_find_sessions_by_file,
    },
    {
        "name": "get_recent_sessions",
        "description": "The most recently active sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "default 5"}},
        },
        "handler": _t_get_recent_sessions,
    },
    {
        "name": "ask_history",
        "description": "Ask a grounded, natural-language question about your history. Computed locally with citations — no model calls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "session_id": {"type": "string", "description": "optional: scope to one session"},
            },
            "required": ["question"],
        },
        "handler": _t_ask_history,
    },
    {
        "name": "list_bookmarks",
        "description": "List the user's per-message bookmarks (starred moments inside sessions). Optionally scope to one session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "optional: only this session's bookmarks"},
            },
        },
        "handler": _t_list_bookmarks,
    },
    {
        "name": "get_prompt_patterns",
        "description": "Recurring prompt patterns — clusters of near-identical prompts the user asks again and again, with counts. Useful for surfacing a reusable prompt.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_count": {"type": "integer", "description": "minimum cluster size (default 3)"},
            },
        },
        "handler": _t_get_prompt_patterns,
    },
    {
        "name": "get_cost_by_period",
        "description": "Spend, token totals and session counts for the last N calendar periods. Use this when asked 'how much did I spend this week/month?' — grounded in the local index, no estimate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["daily", "weekly", "monthly"],
                           "description": "bucket size (default monthly)"},
                "n": {"type": "integer", "description": "how many recent periods (default 6)"},
            },
        },
        "handler": _t_get_cost_by_period,
    },
    {
        "name": "get_diff_for_session",
        "description": "Every inline file diff in a session (old→new for each edit/write), optionally filtered to one file. Use this to show exactly what a session changed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "file_path": {"type": "string", "description": "optional: only diffs for this file (matched by basename)"},
            },
            "required": ["session_id"],
        },
        "handler": _t_get_diff_for_session,
    },
    {
        "name": "get_annotations",
        "description": "The user's inline annotations on a session — personal notes attached to the session or individual messages. Use this to recall human context the user wrote.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
        "handler": _t_get_annotations,
    },
    {
        "name": "generate_project_brief",
        "description": "A full onboarding brief for one project: session count, spend, top files, top tools, last activity and an inferred CLAUDE.md profile. One call to get up to speed on a project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "project path or short name"}},
            "required": ["project_id"],
        },
        "handler": _t_generate_project_brief,
    },
    {
        "name": "get_cross_refs",
        "description": "Find prompts where the user referenced an earlier session ('as we did last time', 'like in the refactor session') and the candidate sessions they likely meant. Use this to recover the thread a user is mentally continuing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "max references (default 50)"},
            },
        },
        "handler": _t_get_cross_refs,
    },
    {
        "name": "find_sessions_by_github_ref",
        "description": "Find sessions that discussed a specific GitHub issue or PR. Accepts a ref like '#123' or 'owner/repo#456', or a number + repo. Read-only; no GitHub API calls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "e.g. '#123' or 'owner/repo#456'"},
                "number": {"type": "integer", "description": "issue/PR number"},
                "repo": {"type": "string", "description": "optional 'owner/repo' or 'repo' filter"},
            },
        },
        "handler": _t_find_sessions_by_github_ref,
    },
    {
        "name": "list_tags",
        "description": "List all user-defined session tags with their session counts. Tags are the user's freeform organizational labels (bug-fix, architecture, ship-it, …).",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_tags,
    },
    {
        "name": "get_session_tags",
        "description": "Return the tags applied to a specific session (id, name, colour). Use to recall how the user categorised a session.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
        "handler": _t_get_session_tags,
    },
    {
        "name": "get_session_narrative",
        "description": "Generate a deterministic, human-readable narrative of a session: goal, approach, outcome, files changed, errors, recovery, next steps and a quality label. No model calls. Great for auto-writing a PR description or stand-up note from a session.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
        "handler": _t_get_session_narrative,
    },
    {
        "name": "get_file_heatmap",
        "description": "The top-10 hottest files across your sessions with heat_score, edit_count and session_count. Answers 'which files am I touching most in this project this week?'. Optional project_id / since / until (YYYY-MM-DD) filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "optional project filter"},
                "since": {"type": "string", "description": "optional YYYY-MM-DD lower bound"},
                "until": {"type": "string", "description": "optional YYYY-MM-DD upper bound"},
            },
        },
        "handler": _t_get_file_heatmap,
    },
    {
        "name": "generate_resume_brief",
        "description": "Generate a copy-paste-ready context brief to resume a session in a new Claude Code window: last tool calls, recent errors, uncommitted files, branch/SHA, and open questions.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
        "handler": _t_generate_resume_brief,
    },
    {
        "name": "compare_sessions",
        "description": "Compare two sessions by cost, tokens, health, prompt overlap and files touched, with a plain-English verdict on which approach was better.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id_a": {"type": "string"},
                "session_id_b": {"type": "string"},
            },
            "required": ["session_id_a", "session_id_b"],
        },
        "handler": _t_compare_sessions,
    },
    {
        "name": "get_error_taxonomy",
        "description": "Return the error-type distribution across all sessions (permission_error, file_not_found, syntax_error, timeout, api_error, assertion_failure, unknown), with worst sessions and a weekly trend. Optionally filter by project or since date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "optional project filter"},
                "since": {"type": "string", "description": "optional YYYY-MM-DD lower bound"},
            },
        },
        "handler": _t_get_error_taxonomy,
    },
    {
        "name": "verify_claude_md",
        "description": "Check whether a project's CLAUDE.md claims match actual session history. Each claim is scored verified / stale / unverifiable against the project's real tool calls.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "project path or short name"}},
            "required": ["project_id"],
        },
        "handler": _t_verify_claude_md,
    },
    {
        "name": "search_by_error_type",
        "description": "Find sessions containing errors of a specific taxonomy type, sorted by recency. Pairs with get_error_taxonomy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "error_type": {"type": "string",
                               "description": "one of: permission_error, file_not_found, syntax_error, timeout, api_error, assertion_failure, unknown"},
                "limit": {"type": "integer", "description": "max results (default 20)"},
            },
            "required": ["error_type"],
        },
        "handler": _t_search_by_error_type,
    },
    {
        "name": "get_budget_forecast",
        "description": "Project end-of-month spend at the current pace, identify the biggest cost-driver project, how many sessions until the budget ceiling, and the most wasteful (expensive + low-health) spending pattern.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_get_budget_forecast,
    },
]

_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _tools_list_payload() -> dict:
    return {"tools": [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOLS
    ]}


# ---------------------------------------------------------------------------
# JSON-RPC dispatch  (transport-free, so the self-test can exercise it directly)
# ---------------------------------------------------------------------------

def call_tool(db_path: str, name: str, arguments: dict) -> dict:
    """Run one tool by name against a read-only index connection.

    Returns the MCP ``tools/call`` result shape: a single text-content block
    holding the JSON-encoded tool output, plus an ``isError`` flag.
    """
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        return _tool_result({"error": f"unknown tool {name!r}"}, is_error=True)
    handler = cast("Callable[..., Any]", tool["handler"])
    conn = index.connect_ro(db_path)
    try:
        result = handler(conn, arguments or {})
    except Exception as exc:  # noqa: BLE001 — surface as a tool error, never crash the server
        return _tool_result({"error": str(exc)}, is_error=True)
    finally:
        conn.close()
    return _tool_result(result, is_error=bool(isinstance(result, dict) and result.get("error")))


def _tool_result(obj, *, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(obj, default=str, ensure_ascii=False)}],
        "isError": is_error,
    }


def handle_request(db_path: str, req: dict) -> dict | None:
    """Handle one JSON-RPC request object. Returns a response dict, or None for
    a notification (no ``id``), which the transport must not reply to."""
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
        return _error(req.get("id") if isinstance(req, dict) else None,
                      INVALID_REQUEST, "invalid JSON-RPC request")
    method = req.get("method")
    rid = req.get("id")
    is_notification = "id" not in req

    if method == "initialize":
        result = {"protocolVersion": PROTOCOL_VERSION,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": SERVER_INFO}
    elif method in ("notifications/initialized", "initialized"):
        return None  # client ack — nothing to answer
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = _tools_list_payload()
    elif method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        if not name:
            return _error(rid, INVALID_PARAMS, "tools/call requires a tool name")
        result = call_tool(db_path, name, params.get("arguments") or {})
    else:
        if is_notification:
            return None
        return _error(rid, METHOD_NOT_FOUND, f"unknown method {method!r}")

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------

def serve_stdio(db_path: str, *, stdin=None, stdout=None) -> int:
    """Read newline-delimited JSON-RPC requests from stdin, write responses to
    stdout. Blocks until EOF (the client closed the pipe)."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _error(None, PARSE_ERROR, "could not parse JSON"))
            continue
        resp = handle_request(db_path, req)
        if resp is not None:
            _write(stdout, resp)
    return 0


def _write(stream, obj) -> None:
    stream.write(json.dumps(obj, default=str, ensure_ascii=False) + "\n")
    stream.flush()


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="claudestudio-mcp",
        description="ClaudeStudio MCP server (JSON-RPC 2.0 over stdio).",
    )
    ap.add_argument("--db", default=index.default_db_path(), help="index database path")
    args = ap.parse_args(argv)
    if not os.path.exists(args.db):
        sys.stderr.write(
            f"  No index at {args.db}. Run `claudestudio index` first "
            f"(or `claudestudio demo`).\n"
        )
        return 1
    return serve_stdio(args.db)


if __name__ == "__main__":
    sys.exit(main())
