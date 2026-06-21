"""Ask — a grounded, local companion for your Claude Code history.

This is **not** a language model and makes **no** network or model calls. Every
answer is *computed* from the SQLite index with deterministic rules, then cites
the exact sessions and messages it drew from. That keeps the feature honest and
keeps the local-first promise: nothing leaves your machine, and the same
question always yields the same answer.

The public surface is :func:`answer`, which routes a natural-language question to
one of a handful of grounded reports:

    digest        — "what happened in this session?"
    handoff       — "give me a handoff brief", "catch me up"
    reopen        — "what should I reopen next?", "where did I leave off?"
    files         — "which files changed?", "why was X edited?"
    important     — "what are the most important tool calls here?"
    compare       — "compare these two sessions"
    spend         — "what did this cost?", "where did the tokens go?"
    search        — anything else falls back to grounded full-text search

Each report returns a typed list of *blocks* the web UI renders richly, plus
`citations` that deep-link back into the real data. The same functions back the
HTTP API and the self-test, so behaviour is covered by exact assertions.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3

from . import analytics

# ---------------------------------------------------------------------------
# tool taxonomy — how a tool call touches the workspace
# ---------------------------------------------------------------------------

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update"}
READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Grep", "Glob", "Find"}
RUN_TOOLS = {"Bash", "PowerShell", "Shell"}
# Tools that mutate state are the high-signal ones for "what actually happened".
_TOOL_WEIGHT = {
    "Write": 5, "Edit": 5, "MultiEdit": 5, "NotebookEdit": 5, "Update": 5,
    "Bash": 3, "PowerShell": 3, "Task": 3, "WebFetch": 2, "WebSearch": 2,
    "Grep": 1, "Glob": 1, "Read": 1, "NotebookRead": 1,
}

_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")
# something that looks like a real file: has a slash or a dotted extension.
_FILEISH = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,8}$")
_BASH_TOKEN = re.compile(r"[^\s'\"`]+\.[A-Za-z0-9]{1,8}")


def _basename(p: str) -> str:
    return os.path.basename(p.rstrip("/\\")) or p


def paths_in_tool(name: str, inp: dict) -> list[str]:
    """Extract the file path(s) a single tool call refers to, best-effort.

    Conservative on purpose: only returns strings that look like real files so
    "files touched" stays high-signal rather than echoing every CLI flag.
    """
    out: list[str] = []
    for key in _PATH_KEYS:
        v = inp.get(key)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    if name in RUN_TOOLS:
        cmd = inp.get("command")
        if isinstance(cmd, str):
            for tok in _BASH_TOKEN.findall(cmd):
                if "/" in tok or "\\" in tok or "." in tok:
                    out.append(tok)
    # de-dup, keep order
    seen, uniq = set(), []
    for p in out:
        if p not in seen and _FILEISH.search(p.replace("\\", "/")):
            seen.add(p)
            uniq.append(p)
    return uniq


# ---------------------------------------------------------------------------
# small DB helpers
# ---------------------------------------------------------------------------

def _session_meta(conn, sid: str) -> dict | None:
    r = conn.execute(
        "SELECT session_id,title,project,project_name,git_branch,primary_model,"
        "msg_count,user_msgs,tool_calls,cost_usd,duration_s,first_epoch,last_epoch "
        "FROM sessions WHERE session_id=?",
        (sid,),
    ).fetchone()
    return dict(r) if r else None


def _latest_session_id(conn, project: str | None = None) -> str | None:
    if project:
        r = conn.execute(
            "SELECT session_id FROM sessions WHERE project=? OR project_name=? "
            "ORDER BY last_epoch DESC LIMIT 1",
            (project, project),
        ).fetchone()
    else:
        r = conn.execute(
            "SELECT session_id FROM sessions ORDER BY last_epoch DESC LIMIT 1"
        ).fetchone()
    return r["session_id"] if r else None


def _cite(conn, sid: str, seq: int | None = None, meta: dict | None = None) -> dict:
    # callers that already hold the session row can pass `meta` to skip the
    # per-citation lookup (was an N+1 in reopen / file_history).
    if meta is None:
        meta = _session_meta(conn, sid) or {"session_id": sid, "title": sid[:8]}
    c = {"session_id": sid, "title": meta.get("title") or "Untitled",
         "project_name": meta.get("project_name")}
    if seq is not None:
        c["seq"] = seq
    return c


# ---------------------------------------------------------------------------
# grounded reports
# ---------------------------------------------------------------------------

def files_touched(conn, sid: str) -> list[dict]:
    """Files this session read, edited, or ran against — ranked, edits first."""
    rows = conn.execute(
        "SELECT name, input_json, seq, is_error FROM tool_calls "
        "WHERE session_id=? ORDER BY id",
        (sid,),
    ).fetchall()
    files: dict[str, dict] = {}
    for r in rows:
        try:
            inp = json.loads(r["input_json"] or "{}")
        except json.JSONDecodeError:
            inp = {}
        op = ("edit" if r["name"] in EDIT_TOOLS else
              "read" if r["name"] in READ_TOOLS else
              "search" if r["name"] in SEARCH_TOOLS else
              "run" if r["name"] in RUN_TOOLS else "use")
        for p in paths_in_tool(r["name"], inp):
            key = p.replace("\\", "/")
            f = files.setdefault(key, {
                "path": key, "name": _basename(key), "ops": set(),
                "count": 0, "errors": 0, "first_seq": r["seq"],
            })
            f["ops"].add(op)
            f["count"] += 1
            if r["is_error"]:
                f["errors"] += 1
    ranked = sorted(
        files.values(),
        key=lambda f: ("edit" not in f["ops"], -f["count"], f["name"]),
    )
    for f in ranked:
        f["ops"] = sorted(f["ops"])
        f["edited"] = "edit" in f["ops"]
    return ranked


def _key_statements(conn, sid: str, limit: int = 4) -> list[dict]:
    """Substantive assistant statements — a cheap proxy for 'key decisions'.

    Heuristic: prefer assistant text that reads like a conclusion (mentions a
    fix / cause / result), longest first, deduped.
    """
    rows = conn.execute(
        "SELECT seq, text FROM messages "
        "WHERE session_id=? AND role='assistant' AND text<>'' ORDER BY seq",
        (sid,),
    ).fetchall()
    cues = ("fix", "fixed", "found", "cause", "because", "root", "switch",
            "added", "removed", "instead", "now ", "result", "done",
            "decided", "chose", "drop", "replace", "p95", "faster")
    scored = []
    for r in rows:
        first = (r["text"].strip().splitlines() or [""])[0].strip()
        if len(first) < 16:
            continue
        score = sum(2 for c in cues if c in first.lower()) + min(len(first), 160) / 160
        scored.append((score, r["seq"], first[:200]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    seen, out = set(), []
    for _, seq, txt in scored:
        k = txt.lower()[:60]
        if k in seen:
            continue
        seen.add(k)
        out.append({"seq": seq, "text": txt, "session_id": sid})
        if len(out) >= limit:
            break
    out.sort(key=lambda d: d["seq"])
    return out


def _tool_breakdown(conn, sid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT name, COUNT(*) calls, COALESCE(SUM(is_error),0) errors "
        "FROM tool_calls WHERE session_id=? GROUP BY name ORDER BY calls DESC",
        (sid,),
    ).fetchall()
    return [dict(r) for r in rows]


def _first_prompt(conn, sid: str) -> str:
    r = conn.execute(
        "SELECT text FROM messages WHERE session_id=? AND role='user' AND text<>'' "
        "ORDER BY seq LIMIT 1",
        (sid,),
    ).fetchone()
    return (r["text"].strip().splitlines() or [""])[0][:240] if r else ""


def _ended_on_error(conn, sid: str) -> dict | None:
    r = conn.execute(
        "SELECT name, seq FROM tool_calls WHERE session_id=? AND is_error=1 "
        "ORDER BY id DESC LIMIT 1",
        (sid,),
    ).fetchone()
    return dict(r) if r else None


def session_digest(conn, sid: str) -> dict:
    """'What happened in this session?' — a grounded recap."""
    meta = _session_meta(conn, sid)
    if not meta:
        return _not_found(sid)
    files = files_touched(conn, sid)
    edited = [f for f in files if f["edited"]]
    tools = _tool_breakdown(conn, sid)
    decisions = _key_statements(conn, sid)
    errors = sum(t["errors"] for t in tools)

    blocks: list[dict] = [
        {"type": "stats", "items": [
            {"label": "prompts", "value": meta["user_msgs"]},
            {"label": "tool calls", "value": meta["tool_calls"]},
            {"label": "files edited", "value": len(edited)},
            {"label": "errors", "value": errors, "tone": "bad" if errors else None},
            {"label": "est. cost", "value": _money(meta["cost_usd"]), "accent": True},
        ]},
    ]
    prompt = _first_prompt(conn, sid)
    if prompt:
        blocks.append({"type": "text", "label": "Asked for",
                       "text": prompt})
    if decisions:
        blocks.append({"type": "decisions", "label": "Key statements", "items": decisions})
    if files:
        blocks.append({"type": "files", "label": "Files touched",
                       "items": _files_for_block(files, sid)})
    if tools:
        blocks.append({"type": "list", "label": "Tools used",
                       "items": [f"{t['name']} ×{t['calls']}"
                                 + (f" · {t['errors']} err" if t["errors"] else "")
                                 for t in tools[:8]]})
    return _wrap("digest",
                 f"Session digest · {meta['title']}",
                 blocks, [_cite(conn, sid)], scope={"session_id": sid},
                 grounding=_ground(1))


def handoff_brief(conn, sid: str) -> dict:
    """A brief you could paste to a teammate (or future-you) to continue."""
    meta = _session_meta(conn, sid)
    if not meta:
        return _not_found(sid)
    files = files_touched(conn, sid)
    edited = [f for f in files if f["edited"]]
    err = _ended_on_error(conn, sid)
    prompt = _first_prompt(conn, sid)
    decisions = _key_statements(conn, sid, limit=3)

    steps: list[str] = []
    if err:
        steps.append(f"Re-check the last {err['name']} — it returned an error.")
    if edited:
        steps.append("Review / test edits to "
                     + ", ".join(f["name"] for f in edited[:3])
                     + (" …" if len(edited) > 3 else "") + ".")
    if not steps:
        steps.append("Continue from the last response; no errors were left open.")

    blocks: list[dict] = []
    if prompt:
        blocks.append({"type": "text", "label": "Goal", "text": prompt})
    blocks.append({"type": "stats", "items": [
        {"label": "branch", "value": meta.get("git_branch") or "—"},
        {"label": "model", "value": (meta.get("primary_model") or "—").replace("claude-", "")},
        {"label": "files edited", "value": len(edited)},
        {"label": "left on error", "value": "yes" if err else "no",
         "tone": "bad" if err else "good"},
    ]})
    if edited:
        blocks.append({"type": "files", "label": "In flight (edited)",
                       "items": _files_for_block(edited, sid)})
    if decisions:
        blocks.append({"type": "decisions", "label": "What was decided", "items": decisions})
    blocks.append({"type": "steps", "label": "Suggested next steps", "items": steps})
    return _wrap("handoff",
                 f"Handoff brief · {meta['title']}",
                 blocks, [_cite(conn, sid)], scope={"session_id": sid},
                 grounding=_ground(1))


def reopen_suggestions(conn, limit: int = 6) -> dict:
    """'What should I reopen next?' — recent work, ranked by how live it looks."""
    rows = conn.execute(
        "SELECT session_id,title,project_name,last_epoch,cost_usd,msg_count,tool_calls "
        "FROM sessions JOIN user_state USING(session_id) "
        "WHERE archived=0 ORDER BY last_epoch DESC LIMIT 40"
    ).fetchall()
    if not rows:
        return _wrap("reopen", "Nothing to reopen yet",
                     [{"type": "text", "text": "No sessions are indexed. Hit Sync, "
                       "or run `claudestudio demo` to explore with sample data."}],
                     [], grounding=_ground(0))
    # batch the "ended on an error?" lookup for every candidate in one query,
    # instead of one _ended_on_error() round-trip per session (the N+1 hot spot).
    # MAX(id) + bare columns returns the name/seq of each session's latest error.
    ids = [r["session_id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    last_err: dict[str, sqlite3.Row] = {}
    for er in conn.execute(
        f"SELECT session_id, name, seq, MAX(id) AS _mid FROM tool_calls "
        f"WHERE is_error=1 AND session_id IN ({placeholders}) GROUP BY session_id",
        ids,
    ):
        last_err[er["session_id"]] = er

    scored = []
    for i, r in enumerate(rows):
        err = last_err.get(r["session_id"])
        recency = max(0, 40 - i)  # newer = higher
        reasons = []
        score = recency
        if err:
            score += 60
            reasons.append(f"left on a {err['name']} error")
        if (r["tool_calls"] or 0) >= 8:
            score += 12
            reasons.append("heavy tool use")
        if i < 5:
            reasons.append("recently active")
        scored.append((score, dict(r), reasons))
    scored.sort(key=lambda x: -x[0])
    items = []
    for _, r, reasons in scored[:limit]:
        items.append({
            "session_id": r["session_id"], "title": r["title"] or "Untitled",
            "project_name": r["project_name"], "last_epoch": r["last_epoch"],
            "cost_usd": r["cost_usd"], "msg_count": r["msg_count"],
            "reason": "; ".join(reasons) or "recent",
        })
    cites = [_cite(conn, it["session_id"], meta=it) for it in items]
    return _wrap("reopen", "What to reopen next",
                 [{"type": "text",
                   "text": "Ranked by recency and whether the session looks unfinished "
                           "(ended on a tool error, heavy tool use)."},
                  {"type": "sessions", "items": items}],
                 cites, grounding=_ground(len(rows)))


def file_history(conn, query: str, limit: int = 30) -> dict:
    """'Where did this change happen / why was X edited?' across all history."""
    needle = query.strip().replace("\\", "/")
    rows = conn.execute(
        "SELECT t.session_id, t.name, t.input_json, t.seq, t.is_error, "
        "       s.title, s.project_name, s.last_epoch "
        "FROM tool_calls t JOIN sessions s USING(session_id) "
        "WHERE t.input_json LIKE ? ORDER BY s.last_epoch DESC LIMIT 400",
        (f"%{needle}%",),
    ).fetchall()
    by_session: dict[str, dict] = {}
    for r in rows:
        try:
            inp = json.loads(r["input_json"] or "{}")
        except json.JSONDecodeError:
            inp = {}
        hit = any(needle.lower() in p.lower() for p in paths_in_tool(r["name"], inp))
        if not hit:
            continue
        b = by_session.setdefault(r["session_id"], {
            "session_id": r["session_id"], "title": r["title"] or "Untitled",
            "project_name": r["project_name"], "last_epoch": r["last_epoch"],
            "edits": 0, "reads": 0, "seq": r["seq"],
        })
        if r["name"] in EDIT_TOOLS:
            b["edits"] += 1
        elif r["name"] in READ_TOOLS:
            b["reads"] += 1
    sessions = sorted(by_session.values(),
                      key=lambda b: (-(b["edits"]), -(b["last_epoch"] or 0)))[:limit]
    if not sessions:
        return _wrap("files", f"No file matches for “{query}”",
                     [{"type": "text",
                       "text": "No tool call referenced that path. Try just the "
                               "filename, or search full-text from the sidebar."}],
                     [], grounding=_ground(0))
    items = [{
        "session_id": b["session_id"], "title": b["title"],
        "project_name": b["project_name"], "last_epoch": b["last_epoch"],
        "seq": b["seq"],
        "reason": f"{b['edits']} edit{'s'*(b['edits']!=1)}, {b['reads']} read{'s'*(b['reads']!=1)}",
    } for b in sessions]
    total_edits = sum(b["edits"] for b in by_session.values())
    return _wrap("files", f"History for “{query}”",
                 [{"type": "stats", "items": [
                     {"label": "sessions", "value": len(by_session)},
                     {"label": "edits", "value": total_edits},
                   ]},
                  {"type": "text",
                   "text": f"Sessions that touched a path matching “{query}”, "
                           "edits first. Open one to see exactly where and why."},
                  {"type": "sessions", "items": items}],
                 [_cite(conn, b["session_id"], b["seq"], meta=b) for b in sessions],
                 grounding=_ground(len(by_session)))


def important_tools(conn, sid: str, limit: int = 8) -> dict:
    """'What are the most important tool calls here?' — mutations first."""
    meta = _session_meta(conn, sid)
    if not meta:
        return _not_found(sid)
    rows = conn.execute(
        "SELECT name, input_json, seq, is_error FROM tool_calls "
        "WHERE session_id=? ORDER BY id", (sid,),
    ).fetchall()
    scored = []
    for r in rows:
        try:
            inp = json.loads(r["input_json"] or "{}")
        except json.JSONDecodeError:
            inp = {}
        paths = paths_in_tool(r["name"], inp)
        weight = _TOOL_WEIGHT.get(r["name"], 1) + (2 if r["is_error"] else 0)
        label = r["name"]
        if paths:
            label += " · " + _basename(paths[0])
        elif isinstance(inp.get("command"), str):
            # a blank / whitespace-only command has no first line — guard the
            # [0] (an empty `command` would otherwise crash this endpoint).
            first = (inp["command"].strip().splitlines() or [""])[0]
            if first:
                label += " · " + first[:60]
        scored.append((weight, r["seq"], label, bool(r["is_error"])))
    scored.sort(key=lambda x: (-x[0], x[1]))
    items = [{"text": lbl + ("  ⚠ error" if err else ""), "seq": seq, "session_id": sid}
             for _, seq, lbl, err in scored[:limit]]
    if not items:
        return _wrap("important", f"No tool calls in · {meta['title']}",
                     [{"type": "text", "text": "This session made no tool calls."}],
                     [_cite(conn, sid)], scope={"session_id": sid}, grounding=_ground(1))
    return _wrap("important", f"Most important tool calls · {meta['title']}",
                 [{"type": "text",
                   "text": "Ranked by impact: writes & edits first, then commands, "
                           "with errors boosted."},
                  {"type": "decisions", "items": items}],
                 [_cite(conn, sid)], scope={"session_id": sid}, grounding=_ground(1))


def compare_sessions(conn, a: str, b: str) -> dict:
    """Qualitative + quantitative diff of two sessions."""
    ma, mb = _session_meta(conn, a), _session_meta(conn, b)
    if not ma or not mb:
        return _not_found(a if not ma else b)
    fa = {f["name"] for f in files_touched(conn, a)}
    fb = {f["name"] for f in files_touched(conn, b)}
    shared = sorted(fa & fb)
    rows = [
        ("prompts", ma["user_msgs"], mb["user_msgs"]),
        ("tool calls", ma["tool_calls"], mb["tool_calls"]),
        ("files touched", len(fa), len(fb)),
        ("est. cost", ma["cost_usd"], mb["cost_usd"]),
    ]
    blocks = [
        {"type": "compare", "a": ma["title"], "b": mb["title"],
         "rows": [{"label": k, "a": av, "b": bv,
                   "money": (k == "est. cost")} for k, av, bv in rows]},
    ]
    if shared:
        blocks.append({"type": "list", "label": "Files both touched", "items": shared[:12]})
    else:
        blocks.append({"type": "text", "label": "Overlap",
                       "text": "These two sessions touched no files in common."})
    return _wrap("compare", "Session comparison", blocks,
                 [_cite(conn, a), _cite(conn, b)], grounding=_ground(2))


def spend_summary(conn) -> dict:
    """'What did this cost / where did the tokens go?'"""
    a = analytics.overview(conn)
    by_model = a["by_model"][:6]
    items = [{"text": f"{m['model'].replace('claude-','')} — {_money(m['cost_usd'])} "
                      f"· {m['tokens']:,} tok", "session_id": None}
             for m in by_model]
    blocks = [
        {"type": "stats", "items": [
            {"label": "est. spend", "value": _money(a["cost_usd"]), "accent": True},
            {"label": "tokens", "value": f"{a['tokens']:,}"},
            {"label": "from cache", "value": f"{a['cache_read']:,}"},
            {"label": "sessions", "value": a["sessions"]},
        ]},
        {"type": "text",
         "text": "Estimated at public Anthropic prices, cache-aware. Models with no "
                 "public price are counted as $0 and flagged in Analytics."},
    ]
    if items:
        blocks.append({"type": "list", "label": "By model",
                       "items": [it["text"] for it in items]})
    return _wrap("spend", "Cost & tokens", blocks, [], grounding=_ground(a["sessions"]))


def _grounded_search(conn, q: str, limit: int = 12) -> dict:
    from . import api  # local import to avoid a cycle at module load
    res = api.search(conn, {"q": q, "limit": limit})
    results = res.get("results", [])
    if not results:
        return _wrap("search", f"No matches for “{q}”",
                     [{"type": "text",
                       "text": "Nothing in your indexed prompts, responses, thinking, "
                               "or tool calls matched. Try fewer / different words."}],
                     [], grounding=_ground(0))
    # collapse to sessions, keep the best snippet per session
    seen: dict[str, dict] = {}
    for r in results:
        sid = r["session_id"]
        if sid not in seen:
            seen[sid] = {"session_id": sid, "title": r.get("title") or "Untitled",
                         "project_name": r.get("project_name"),
                         "last_epoch": r.get("last_epoch"), "seq": r.get("seq"),
                         "reason": (r.get("snip") or "").replace("⟦", "").replace("⟧", "")[:120]}
    items = list(seen.values())[:limit]
    return _wrap("search", f"Found in {len(items)} session(s)",
                 [{"type": "text",
                   "text": "I answered by searching your history full-text. "
                           "Open a result to see it in context."},
                  {"type": "sessions", "items": items}],
                 [_cite(conn, it["session_id"], it.get("seq")) for it in items],
                 grounding=_ground(len(items)))


# ---------------------------------------------------------------------------
# intent router
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                      r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def _detect_path(q: str) -> str | None:
    for tok in re.findall(r"[\w./\\-]+", q):
        norm = tok.replace("\\", "/")
        # require a dot and at least one letter — avoids version numbers like 4.8
        if _FILEISH.search(norm) and "." in norm and re.search(r"[A-Za-z]", norm):
            return tok
    return None


def _has(q: str, *words: str) -> bool:
    return any(w in q for w in words)


def answer(conn, question: str, session: str | None = None) -> dict:
    """Route a question to a grounded report. The heart of Ask mode.

    `session` scopes the question to one session when set (e.g. asked from the
    session view). Returns a structured, citeable answer dict.
    """
    q = (question or "").strip()
    ql = q.lower()
    if not q:
        return _wrap("help", "Ask your history anything",
                     [{"type": "text",
                       "text": "Try: “what should I reopen next?”, “summarize this "
                               "session”, “which files changed?”, “give me a handoff "
                               "brief”."}],
                     [], grounding=_ground(0))

    # explicit session id in the text wins as scope
    m = _UUID_RE.search(q)
    if m:
        session = session or m.group(0)

    # --- compare (needs two ids) ----------------------------------------
    ids = _UUID_RE.findall(q)
    if _has(ql, "compare", "versus", " vs ", "difference between") and len(ids) >= 2:
        return compare_sessions(conn, ids[0], ids[1])

    # --- file history ---------------------------------------------------
    path = _detect_path(q)
    if path and _has(ql, "file", "edit", "chang", "touch", "where", "why", "modif"):
        return file_history(conn, path)

    # --- scoped (a session is in context) -------------------------------
    if session:
        if _has(ql, "handoff", "hand off", "catch", "continue", "resume",
                "pick up", "next step", "left off", "where was"):
            return handoff_brief(conn, session)
        if _has(ql, "important", "key tool", "tool call", "which tool"):
            return important_tools(conn, session)
        if _has(ql, "file", "touch", "chang", "edit"):
            meta = _session_meta(conn, session)
            if meta:
                fs = files_touched(conn, session)
                return _wrap("files", f"Files touched · {meta['title']}",
                             [{"type": "files", "items": _files_for_block(fs, session)}]
                             if fs else
                             [{"type": "text", "text": "No files were edited or read by "
                               "tool calls in this session."}],
                             [_cite(conn, session)], scope={"session_id": session},
                             grounding=_ground(1))
        # default scoped intent: digest
        if _has(ql, "happen", "summar", "recap", "overview", "digest", "what did",
                "tl;dr", "tldr", "this session", "about this"):
            return session_digest(conn, session)
        # fall through to digest for any other scoped question
        return session_digest(conn, session)

    # --- global intents -------------------------------------------------
    if _has(ql, "reopen", "left off", "pick up", "what next", "what should i",
            "continue", "resume", "come back", "unfinished"):
        return reopen_suggestions(conn)
    if _has(ql, "handoff", "hand off", "catch me up", "brief"):
        sid = _latest_session_id(conn)
        return handoff_brief(conn, sid) if sid else _grounded_search(conn, q)
    if _has(ql, "cost", "spend", "expensive", "token", "budget", "price"):
        return spend_summary(conn)
    if _has(ql, "happen", "summar", "recap", "overview", "digest", "what did i",
            "last session", "recent"):
        sid = _latest_session_id(conn)
        return session_digest(conn, sid) if sid else _grounded_search(conn, q)

    # --- fallback: grounded full-text search ----------------------------
    return _grounded_search(conn, q)


# ---------------------------------------------------------------------------
# shaping helpers
# ---------------------------------------------------------------------------

def _files_for_block(files: list[dict], sid: str) -> list[dict]:
    return [{"path": f["path"], "name": f["name"], "ops": f["ops"],
             "edited": f["edited"], "count": f["count"], "errors": f["errors"],
             "session_id": sid, "seq": f.get("first_seq")} for f in files[:14]]


def _money(c) -> str:
    c = float(c or 0)
    if c == 0:
        return "$0"
    if c < 0.01:
        return f"${c:.4f}"
    if c < 100:
        return f"${c:,.2f}"
    return f"${c:,.0f}"


def _ground(n: int) -> str:
    if n <= 0:
        return "Computed locally · no model calls."
    return f"Computed locally from {n} session{'s' if n != 1 else ''} · no model calls."


def _wrap(intent, title, blocks, citations, scope=None, grounding=""):
    return {
        "intent": intent, "title": title, "blocks": blocks,
        "citations": citations, "scope": scope or {},
        "grounding": grounding or _ground(0),
    }


def _not_found(sid: str) -> dict:
    return _wrap("error", "Session not found",
                 [{"type": "text",
                   "text": f"No session with id {sid!r} is in the index. "
                           "Hit Sync, or open it from the Sessions list."}],
                 [], grounding=_ground(0))


def suggestions(scope_session: bool) -> list[str]:
    """Starter questions surfaced in the UI for discoverability."""
    if scope_session:
        return [
            "What happened in this session?",
            "Give me a handoff brief",
            "Which files changed?",
            "What are the most important tool calls?",
        ]
    return [
        "What should I reopen next?",
        "Summarize my most recent session",
        "Where did the tokens go?",
        "Give me a handoff brief",
    ]
