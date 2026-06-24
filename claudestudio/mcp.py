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
