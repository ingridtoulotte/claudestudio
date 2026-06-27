"""Export a session as a runnable Jupyter notebook (nbformat v4).

nbformat v4 is plain JSON — no nbformat dependency needed. User prompts become
markdown cells, assistant replies markdown cells, and every tool call becomes a
code cell whose output carries the tool's result preview.
"""

from __future__ import annotations

import json


def _md_cell(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def _code_cell(source: str, output_text: str) -> dict:
    outputs = []
    if output_text:
        outputs.append({"output_type": "stream", "name": "stdout", "text": output_text})
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "source": source,
        "outputs": outputs,
    }


def _tool_source(name: str, input_json: str | None) -> str:
    """A readable command/diff for a tool-use code cell."""
    detail = ""
    if input_json:
        try:
            data = json.loads(input_json)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            if "command" in data:
                detail = str(data["command"])
            elif "file_path" in data or "path" in data:
                detail = str(data.get("file_path") or data.get("path"))
            else:
                detail = json.dumps(data, ensure_ascii=False)
    header = f"# {name}"
    return f"{header}\n{detail}" if detail else header


def to_notebook(conn, session_id: str) -> dict | None:
    sess = conn.execute(
        "SELECT session_id, cost_usd, input_tokens, output_tokens, health_score "
        "FROM sessions WHERE session_id=?", (session_id,)  # SAFE
    ).fetchone()
    if sess is None:
        return None

    msgs = conn.execute(
        "SELECT uuid, role, text FROM messages WHERE session_id=? ORDER BY seq",  # SAFE
        (session_id,),
    ).fetchall()
    tools = conn.execute(
        "SELECT id, message_uuid, name, input_json, result_preview FROM tool_calls "
        "WHERE session_id=? ORDER BY seq", (session_id,)  # SAFE
    ).fetchall()

    tools_by_msg: dict = {}
    for t in tools:
        tools_by_msg.setdefault(t["message_uuid"], []).append(t)

    cells = []
    emitted = set()
    for m in msgs:
        role = m["role"]
        text = m["text"] or ""
        if role == "user":
            cells.append(_md_cell("> 💬 Prompt\n\n" + text))
        elif role == "assistant":
            cells.append(_md_cell(text))
        else:
            continue  # other roles don't produce a cell
        for t in tools_by_msg.get(m["uuid"], []):
            cells.append(_code_cell(_tool_source(t["name"], t["input_json"]),
                                    t["result_preview"] or ""))
            emitted.add(t["id"])
    # any tool calls not linked to a rendered message still become code cells
    for t in tools:
        if t["id"] not in emitted:
            cells.append(_code_cell(_tool_source(t["name"], t["input_json"]),
                                    t["result_preview"] or ""))

    tokens = (sess["input_tokens"] or 0) + (sess["output_tokens"] or 0)
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "claudestudio": {
                "session_id": sess["session_id"],
                "cost_usd": sess["cost_usd"] or 0.0,
                "tokens": tokens,
                "health_score": sess["health_score"],
            },
            "kernelspec": {"display_name": "Python 3", "language": "python",
                           "name": "python3"},
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }


def notebook_json(conn, session_id: str) -> str | None:
    nb = to_notebook(conn, session_id)
    if nb is None:
        return None
    return json.dumps(nb, ensure_ascii=False, indent=1)


def notebook_payload(conn, session_id: str) -> dict:
    nb = to_notebook(conn, session_id)
    if nb is None:
        return {"error": f"no session with id {session_id!r}"}
    return nb


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def selftest(c) -> None:
    import os
    import tempfile

    from . import index

    with tempfile.TemporaryDirectory() as tmp:
        conn = index.connect(os.path.join(tmp, "nb.db"))
        try:
            conn.execute(
                "INSERT INTO sessions(session_id,title,cost_usd,input_tokens,"
                "output_tokens,health_score) VALUES('s1','Demo',0.42,1000,500,88)")
            # 2 user, 2 assistant messages
            rows = [("u1", "user", "first prompt", 0),
                    ("a1", "assistant", "first answer", 1),
                    ("u2", "user", "second prompt", 2),
                    ("a2", "assistant", "second answer", 3)]
            for uuid, role, text, seq in rows:
                conn.execute(
                    "INSERT INTO messages(uuid,session_id,role,text,seq) VALUES(?,?,?,?,?)",
                    (uuid, "s1", role, text, seq))
            # 2 tool calls linked to assistant messages (a bash + a Read)
            conn.execute(
                "INSERT INTO tool_calls(session_id,message_uuid,seq,name,input_json,"
                "result_preview) VALUES('s1','a1',0,'Bash',?,?)",
                (json.dumps({"command": "ls -la"}), "total 8"))
            conn.execute(
                "INSERT INTO tool_calls(session_id,message_uuid,seq,name,input_json,"
                "result_preview) VALUES('s1','a2',1,'Read',?,?)",
                (json.dumps({"file_path": "auth.py"}), "def login(): ..."))
            conn.commit()

            nb = to_notebook(conn, "s1")
            c.ok(nb is not None, "to_notebook returns a notebook")
            assert nb is not None  # narrow for the type checker
            c.eq(nb["nbformat"], 4, "nbformat is 4")
            c.eq(nb["nbformat_minor"], 5, "nbformat_minor is 5")

            # round-trip through JSON
            nbjson = notebook_json(conn, "s1")
            assert nbjson is not None
            parsed = json.loads(nbjson)
            c.eq(parsed["nbformat"], 4, "round-tripped notebook valid JSON, nbformat 4")

            cells = nb["cells"]
            c.eq(len(cells), 6, "cell count == 2 user + 2 assistant + 2 tools")
            code_cells = [cell for cell in cells if cell["cell_type"] == "code"]
            md_cells = [cell for cell in cells if cell["cell_type"] == "markdown"]
            c.eq(len(code_cells), 2, "two code cells (one per tool call)")
            c.eq(len(md_cells), 4, "four markdown cells (prompts + answers)")
            for cell in code_cells:
                c.ok("outputs" in cell, "code cell has outputs")
                c.ok(cell["execution_count"] is None, "code cell execution_count is None")
                c.ok("metadata" in cell, "code cell has metadata")
            c.ok(any(o["output_type"] == "stream" for o in code_cells[0]["outputs"]),
                 "code cell carries a stream output")
            c.ok("ls -la" in code_cells[0]["source"], "bash command in code cell source")
            c.ok("Bash" in code_cells[0]["source"], "tool name in code cell source")
            c.ok("auth.py" in code_cells[1]["source"], "Read path in code cell source")

            prompt_cells = [cell for cell in md_cells if "💬 Prompt" in cell["source"]]
            c.eq(len(prompt_cells), 2, "two prompt markdown cells with the prompt marker")
            c.ok("first prompt" in prompt_cells[0]["source"], "prompt text preserved")
            c.ok(any("first answer" in cell["source"] for cell in md_cells),
                 "assistant text preserved")

            meta = nb["metadata"]["claudestudio"]
            c.eq(meta["session_id"], "s1", "metadata has session_id")
            c.close(meta["cost_usd"], 0.42, "metadata has cost_usd")
            c.eq(meta["tokens"], 1500, "metadata tokens = input + output")
            c.eq(meta["health_score"], 88, "metadata has health_score")
            c.eq(nb["metadata"]["kernelspec"]["name"], "python3", "python3 kernelspec")

            # first cell ordering: user prompt comes before its answer
            c.eq(cells[0]["cell_type"], "markdown", "first cell is markdown (prompt)")
            c.ok("💬 Prompt" in cells[0]["source"], "first cell is the first prompt")

            # unknown session
            c.eq(to_notebook(conn, "nope"), None, "unknown session -> None")
            c.eq(notebook_json(conn, "nope"), None, "notebook_json unknown -> None")
            c.ok("error" in notebook_payload(conn, "nope"), "payload unknown -> error")
            c.eq(notebook_payload(conn, "s1")["nbformat"], 4, "payload returns notebook")
        finally:
            conn.close()
