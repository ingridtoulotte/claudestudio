"""github-status — set a GitHub commit status when a session touches a repo.

A ClaudeStudio community plugin. After a session is indexed, if it ran in a git
repository and the required environment variables are present, this posts a
``success`` commit status to the GitHub API so teammates can see a Claude Code
session ran against the commit.

Required environment:
  * ``GITHUB_TOKEN``  — a token with ``repo:status`` scope
  * ``GITHUB_REPOSITORY`` — ``owner/repo``
  * ``GITHUB_SHA``    — the commit SHA to annotate

If any are missing the plugin is a no-op. stdlib only · no telemetry.
"""

from __future__ import annotations

import json
import os
import urllib.request


def _env_ready() -> tuple[str, str, str] | None:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if tok and repo and "/" in repo and sha:
        return tok, repo, sha
    return None


def on_session_indexed(db, session_id: str) -> None:
    ready = _env_ready()
    if ready is None:
        return
    token, repo, sha = ready
    row = db.execute(
        "SELECT git_branch, tool_calls FROM sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if not row or not (row["git_branch"] or "").strip():
        return  # not a git session — nothing to annotate
    url = f"https://api.github.com/repos/{repo}/statuses/{sha}"
    body = json.dumps({
        "state": "success",
        "context": "claudestudio",
        "description": f"Claude Code session · {int(row['tool_calls'] or 0)} tool calls",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    })
    try:
        urllib.request.urlopen(req, timeout=5).close()  # noqa: S310 — github api over https
    except OSError:
        pass  # status posting is best-effort
