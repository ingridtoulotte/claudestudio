"""slack-notify — post a one-line session summary to a Slack webhook.

A ClaudeStudio community plugin. When a session finishes indexing, this posts a
compact summary (title, cost, tool count) to the Slack Incoming Webhook URL in
the ``CLAUDESTUDIO_SLACK_WEBHOOK`` environment variable. If that variable is
unset, the plugin does nothing — no network call, no error.

stdlib only · no telemetry · the only outbound call is to the webhook you
configure yourself.
"""

from __future__ import annotations

import json
import os
import urllib.request

WEBHOOK_ENV = "CLAUDESTUDIO_SLACK_WEBHOOK"


def on_session_indexed(db, session_id: str) -> None:
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if not url or not url.startswith("https://"):
        return  # opt-in: no webhook configured, so nothing to do
    row = db.execute(
        "SELECT title, cost_usd, tool_calls FROM sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if not row:
        return
    title = (row["title"] or "Untitled session")[:80]
    text = (f":robot_face: *ClaudeStudio* — `{title}` finished · "
            f"${float(row['cost_usd'] or 0):.4f} · "
            f"{int(row['tool_calls'] or 0)} tool calls")
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).close()  # noqa: S310 — user-configured https webhook
    except OSError:
        pass  # a notification failure must never break indexing
