"""Live session viewer — tail a Claude Code ``.jsonl`` session as it's written.

Poll-based tail (``tail_events``) plus a terminal formatter and a liveness check.
The HTTP server layers Server-Sent Events on top of ``tail_events``; the MCP tool
and CLI poll it directly. Pure standard library.
"""

from __future__ import annotations

import json
import os
import time


def _summarize_record(rec: dict) -> tuple:
    """Return (event_type, content, ts) for one Claude Code jsonl record."""
    rtype = rec.get("type") or rec.get("role") or "event"
    ts = rec.get("timestamp") or rec.get("ts")
    msg = rec.get("message")
    content = ""
    tool_used = None

    if isinstance(msg, dict):
        body = msg.get("content")
        if isinstance(body, str):
            content = body
        elif isinstance(body, list):
            chunks = []
            for block in body:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    chunks.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_used = block.get("name", "tool")
                    chunks.append(f"{tool_used}(...)")
                elif btype == "tool_result":
                    tool_used = tool_used or "tool_result"
                    chunks.append("tool_result")
            content = " ".join(ch for ch in chunks if ch)
        role = msg.get("role")
        if role in ("user", "assistant"):
            rtype = role
    elif isinstance(rec.get("content"), str):
        content = rec["content"]

    if tool_used is not None:
        rtype = "tool_use"

    content = " ".join(str(content).split())  # collapse whitespace
    if len(content) > 200:
        content = content[:197] + "..."
    return rtype, content, ts


def tail_events(path: str, since_line: int = 0, *, max_events: int = 1000) -> dict:
    """Parse new jsonl lines after ``since_line`` into event dicts.

    Malformed or blank lines are skipped but still count toward the line index,
    so ``next_line`` stays aligned with the physical file. Returns
    ``{"events":[...], "next_line": int, "eof": True}``.
    """
    events: list[dict] = []
    line_no = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line_no, raw in enumerate(fh, start=1):
                if line_no <= since_line:
                    continue
                if len(events) >= max_events:
                    # stop parsing but report where we stopped
                    return {"events": events, "next_line": line_no - 1, "eof": False}
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(rec, dict):
                    continue
                etype, content, ts = _summarize_record(rec)
                events.append({"line": line_no, "event_type": etype,
                               "content": content, "ts": ts})
    except FileNotFoundError:
        return {"events": [], "next_line": since_line, "eof": True}
    return {"events": events, "next_line": line_no, "eof": True}


def format_event(ev: dict) -> str:
    """A single, stable terminal line for one event."""
    etype = ev.get("event_type", "event")
    content = ev.get("content", "")
    ts = ev.get("ts")
    stamp = ""
    if ts:
        # keep just HH:MM:SS if it looks like an ISO timestamp
        text = str(ts)
        stamp = "[" + text[11:19] + "] " if "T" in text and len(text) >= 19 else f"[{text}] "
    icon = {"user": "💬", "assistant": "🤖", "tool_use": "🛠"}.get(etype, "•")
    body = content if content else "(no content)"
    return f"{stamp}{icon} {etype}: {body}"


def is_live(path: str, *, now: float | None = None, window: float = 60.0) -> bool:
    """True if the file was modified within ``window`` seconds of ``now``."""
    if now is None:
        now = time.time()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return False
    return (now - mtime) <= window


def resolve_session_path(conn, session_id: str) -> str | None:
    row = conn.execute(
        "SELECT file_path FROM sessions WHERE session_id=?", (session_id,)  # SAFE
    ).fetchone()
    return (row["file_path"] if row and row["file_path"] else None)


def live_events_payload(conn, session_id: str, params: dict | None = None) -> dict:
    params = params or {}
    path = resolve_session_path(conn, session_id)
    if not path:
        return {"error": "no session file", "session_id": session_id}
    try:
        since = int(params.get("since", 0))
    except (TypeError, ValueError):
        since = 0
    result = tail_events(path, since_line=since)
    result["session_id"] = session_id
    result["is_live"] = is_live(path)
    return result


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln + "\n")


def selftest(c) -> None:
    import tempfile

    from . import index

    user_rec = json.dumps({"type": "user",
                           "message": {"role": "user", "content": "hello there"}})
    asst_rec = json.dumps({"type": "assistant", "timestamp": "2026-06-27T14:32:17.000Z",
                           "message": {"role": "assistant",
                                       "content": [{"type": "text", "text": "hi back"}]}})
    tool_rec = json.dumps({"type": "assistant",
                           "message": {"role": "assistant", "content": [
                               {"type": "tool_use", "name": "Read",
                                "input": {"file": "auth.py"}}]}})

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sess.jsonl")
        _write_lines(path, [user_rec, asst_rec])

        res = tail_events(path, since_line=0)
        c.eq(len(res["events"]), 2, "tail reads 2 events")
        c.eq(res["next_line"], 2, "next_line after 2 lines is 2")
        c.eq(res["eof"], True, "tail reports eof")
        c.eq(res["events"][0]["event_type"], "user", "first event is user")
        c.eq(res["events"][1]["event_type"], "assistant", "second event is assistant")
        c.eq(res["events"][0]["content"], "hello there", "user content extracted")
        c.eq(res["events"][1]["content"], "hi back", "assistant text extracted")
        c.eq(res["events"][0]["line"], 1, "first event line is 1")

        # append a new line, tail from since_line=2
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(tool_rec + "\n")
        res2 = tail_events(path, since_line=2)
        c.eq(len(res2["events"]), 1, "incremental tail yields only the new event")
        c.eq(res2["events"][0]["event_type"], "tool_use", "tool_use event detected")
        c.eq(res2["events"][0]["line"], 3, "new event is line 3")
        c.eq(res2["next_line"], 3, "next_line advances to 3")

        # malformed + blank lines skipped without crashing
        _write_lines(path, [user_rec, "", "{ not json", asst_rec])
        res3 = tail_events(path, since_line=0)
        c.eq(len(res3["events"]), 2, "malformed and blank lines skipped")
        c.eq(res3["next_line"], 4, "next_line still counts physical lines")

        # formatter
        line = format_event(res["events"][1])
        c.ok(isinstance(line, str) and len(line) > 0, "format_event returns a string")
        c.ok("assistant" in line, "format_event names the event type")
        c.ok("14:32:17" in line, "format_event renders HH:MM:SS from ts")
        c.ok("(no content)" in format_event({"event_type": "x", "content": ""}),
             "format_event handles empty content")

        # liveness
        c.ok(is_live(path), "just-written file is live")
        c.ok(not is_live(path, now=time.time() + 10000), "old file is not live")
        c.ok(not is_live(os.path.join(tmp, "missing.jsonl")), "missing file is not live")

        # empty / missing tail
        c.eq(tail_events(os.path.join(tmp, "missing.jsonl"))["events"], [],
             "tail of missing file -> []")

        # resolve + payload through the index
        conn = index.connect(os.path.join(tmp, "i.db"))
        try:
            conn.execute("INSERT INTO sessions(session_id,title,file_path) "
                         "VALUES('s1','S1',?)", (path,))
            conn.commit()
            c.eq(resolve_session_path(conn, "s1"), path, "resolve_session_path finds path")
            c.eq(resolve_session_path(conn, "nope"), None, "resolve unknown -> None")
            pay = live_events_payload(conn, "s1", {"since": 0})
            c.eq(pay["session_id"], "s1", "payload echoes session id")
            c.ok("is_live" in pay and "events" in pay, "payload has is_live + events")
            c.eq(live_events_payload(conn, "nope").get("error"), "no session file",
                 "payload for unknown session -> error")
        finally:
            conn.close()
