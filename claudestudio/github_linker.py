"""GitHub issue / PR deep linker (Feature 2.10, v0.6.0).

Sessions are full of GitHub references — ``#123``, ``owner/repo#456``, or a full
``https://github.com/owner/repo/issues/789`` URL. Surfacing them turns a session
into a navigable map: "which sessions discussed issue #412?", "open the PR this
session was about". Detection is a fixed, auditable regex; the references are
stored at index time (schema v4 ``session_github_refs`` table) so search is a
cheap indexed lookup. Read-only and offline — we never call the GitHub API; the
links are plain external URLs the user clicks.
"""

from __future__ import annotations

import re

# Full GitHub issue/PR URL (captures owner, repo, kind, number).
_URL_RE = re.compile(
    r"https?://github\.com/([\w.\-]+)/([\w.\-]+)/(issues|pull)/(\d+)",
    re.IGNORECASE,
)
# owner/repo#123  (cross-repo shorthand)
_REPO_RE = re.compile(r"\b([\w.\-]+)/([\w.\-]+)#(\d+)\b")
# bare #123  (same-repo shorthand) — must not be preceded by a word char so we
# don't catch things like `color#fff` or `id#3`. Numbers 1..6+ digits.
_BARE_RE = re.compile(r"(?<![\w/#])#(\d{1,7})\b")


def _norm_kind(raw: str) -> str:
    return "pr" if raw.lower() in ("pull", "pr") else "issue"


def extract_refs(text: str) -> list[dict]:
    """Every GitHub reference in `text`, de-duplicated, in first-seen order.

    Each ref: ``{ref, owner, repo, number, kind, url}``. ``owner``/``repo`` are
    ``""`` for a bare ``#123`` (same-repo); ``url`` is ``""`` when we can't build
    a real one without the repo. ``kind`` is ``"issue"`` or ``"pr"`` (bare refs
    default to ``"issue"`` since the shorthand is ambiguous until resolved).
    """
    s = text or ""
    seen: set = set()
    out: list[dict] = []

    def add(ref, owner, repo, number, kind, url):
        key = (owner.lower(), repo.lower(), number, kind) if owner else ("", "", number, "bare")
        if key in seen:
            return
        seen.add(key)
        out.append({"ref": ref, "owner": owner, "repo": repo,
                    "number": int(number), "kind": kind, "url": url})

    for m in _URL_RE.finditer(s):
        owner, repo, kind, num = m.group(1), m.group(2), _norm_kind(m.group(3)), m.group(4)
        seg = "pull" if kind == "pr" else "issues"
        add(m.group(0), owner, repo, num, kind,
            f"https://github.com/{owner}/{repo}/{seg}/{num}")
    for m in _REPO_RE.finditer(s):
        owner, repo, num = m.group(1), m.group(2), m.group(3)
        # a URL already covered this exact ref? add() de-dupes on (owner,repo,num).
        add(f"{owner}/{repo}#{num}", owner, repo, num, "issue",
            f"https://github.com/{owner}/{repo}/issues/{num}")
    for m in _BARE_RE.finditer(s):
        num = m.group(1)
        add(f"#{num}", "", "", num, "issue", "")
    return out


def extract_from_session(ps) -> list[dict]:
    """All GitHub references in a parsed session, tagged with the message seq.

    Scans user/assistant text and tool-call inputs (a `gh issue view 123` Bash
    command counts too). De-duplicated across the whole session on (owner, repo,
    number, kind); the *earliest* seq that mentioned it wins.
    """
    seen: set = set()
    out: list[dict] = []
    for m in ps.messages:
        chunks = [m.text or "", m.thinking or ""]
        for tc in m.tool_calls:
            for v in (tc.input or {}).values():
                if isinstance(v, str):
                    chunks.append(v)
        for ref in extract_refs("\n".join(chunks)):
            key = (ref["owner"].lower(), ref["repo"].lower(), ref["number"], ref["kind"]) \
                if ref["owner"] else ("", "", ref["number"], "bare")
            if key in seen:
                continue
            seen.add(key)
            ref = dict(ref)
            ref["seq"] = m.seq
            out.append(ref)
    return out
