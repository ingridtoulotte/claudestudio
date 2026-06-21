"""Ask engine: routing and grounded reports over crafted edge-case data.

These build tiny indexes by hand (rather than from `fixtures`) so a single odd
row — an empty Bash command, a tool call with no path — can be exercised in
isolation. The Ask engine backs the `/api/ask` endpoint, so a crash here is a
500 the user sees; the assertions below pin the "degrade gracefully" contract.
"""

from __future__ import annotations

import json

import pytest

from claudestudio import ask, index


@pytest.fixture()
def one_tool(db_path):
    """A DB with one session and a single, caller-described tool call.

    Yields a factory `make(name, input_json)` returning the open connection so a
    test can pick exactly the tool shape it wants to probe.
    """
    conn = index.connect(db_path)
    conn.execute(
        "INSERT INTO sessions(session_id,title,user_msgs,tool_calls,cost_usd) "
        "VALUES('s1','T',1,1,0.0)"
    )

    def make(name: str, input_json: str):
        conn.execute(
            "INSERT INTO tool_calls(session_id,message_uuid,seq,name,is_error,input_json) "
            "VALUES('s1','m1',1,?,0,?)",
            (name, input_json),
        )
        conn.commit()
        return conn

    yield make
    conn.close()


@pytest.mark.parametrize("command", ["", "   ", "\n", "  \n  "])
def test_important_tools_survives_blank_command(one_tool, command):
    # A Bash/PowerShell call whose `command` is empty or whitespace has no first
    # line; the label builder must not index [0] of an empty splitlines() list.
    conn = one_tool("Bash", json.dumps({"command": command}))
    out = ask.important_tools(conn, "s1")
    assert out["intent"] == "important"
    # the call is still reported, just without a trailing command preview
    assert out["blocks"], "expected the tool call to be listed, not dropped"


def test_important_tools_keeps_real_command_preview(one_tool):
    conn = one_tool("Bash", '{"command": "pytest -q\\nsecond line"}')
    out = ask.important_tools(conn, "s1")
    labels = [
        it["text"]
        for blk in out["blocks"]
        if blk.get("type") == "decisions"
        for it in blk["items"]
    ]
    assert any("pytest -q" in t for t in labels)
    assert all("second line" not in t for t in labels)  # only the first line


def test_answer_routes_important_without_crash(one_tool):
    # Same path as the UI: a scoped "important tool calls" question must not 500.
    conn = one_tool("Bash", '{"command": ""}')
    out = ask.answer(conn, "what are the most important tool calls?", session="s1")
    assert out["intent"] == "important"
