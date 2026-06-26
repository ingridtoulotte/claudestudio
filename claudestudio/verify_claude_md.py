"""Verify a project's CLAUDE.md against reality (v0.6.2 — the "Insight Engine").

Reads a project's ``CLAUDE.md`` and scores each checkable claim against what
Claude Code *actually did* in that project's sessions. A claim like "always run
tests with pytest" is ``✅ Verified`` if pytest shows up in recent tool calls,
``⚠️ Stale`` if it only shows up in old ones, and ``❓ Unverifiable`` when there
is no evidence either way. Purely heuristic and deterministic — no model calls,
no network.
"""

from __future__ import annotations

import datetime as _dt
import json
import os

# Concrete command/tool tokens we can check for in a project's tool-call history.
# A CLAUDE.md line is "checkable" iff it mentions one of these; evidence is which
# of them actually appear in the project's Bash commands.
SIGNALS = (
    "pytest", "jest", "vitest", "mocha", "npm", "pnpm", "yarn", "ruff", "mypy",
    "black", "flake8", "eslint", "prettier", "tsc", "cargo", "go test", "make",
    "docker", "poetry", "pip", "gradle", "maven", "phpunit", "rspec",
)
_STALE_DAYS = 90

VERIFIED = "verified"
STALE = "stale"
UNVERIFIABLE = "unverifiable"


def claim_signal(line: str) -> str | None:
    """The first checkable signal mentioned in a line, or None."""
    low = line.lower()
    for sig in SIGNALS:
        if sig in low:
            return sig
    return None


def extract_claims(text: str) -> list[dict]:
    """Pull checkable claims out of a CLAUDE.md body.

    Returns ``[{text, signal}]`` for every non-heading line that names a known
    command/tool. Headings, fences and blank lines are skipped.
    """
    claims: list[dict] = []
    in_fence = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#"):
            continue
        body = line.lstrip("-*+ ").strip()
        sig = claim_signal(body)
        if sig:
            claims.append({"text": body[:200], "signal": sig})
    return claims


def verify_claims(claims: list[dict], evidence: dict) -> list[dict]:
    """Score each claim against ``evidence = {recent: set, old: set}``.

    Pure function — the heart of the feature, exercised directly by the
    self-test. Returns ``[{text, status, evidence}]``.
    """
    recent = set(evidence.get("recent") or ())
    old = set(evidence.get("old") or ())
    out: list[dict] = []
    for c in claims:
        sig = c.get("signal")
        if sig in recent:
            out.append({"text": c["text"], "status": VERIFIED,
                        "evidence": f"ran `{sig}` recently"})
        elif sig in old:
            out.append({"text": c["text"], "status": STALE,
                        "evidence": f"last ran `{sig}` over {_STALE_DAYS} days ago"})
        else:
            out.append({"text": c["text"], "status": UNVERIFIABLE,
                        "evidence": f"no `{sig}` activity found in this project"})
    return out


def gather_evidence(conn, project: str, now: _dt.datetime | None = None) -> dict:
    """Which signals appear in the project's Bash commands, split recent vs old."""
    now = now or _dt.datetime.now()
    cutoff = (now - _dt.timedelta(days=_STALE_DAYS)).timestamp()
    rows = conn.execute(
        "SELECT t.input_json j, COALESCE(s.last_epoch,0) e "
        "FROM tool_calls t JOIN sessions s USING(session_id) "
        "WHERE (s.project = ? OR s.project_name = ?) AND t.name='Bash'",
        (str(project), str(project)),
    ).fetchall()
    recent: set[str] = set()
    old: set[str] = set()
    for r in rows:
        try:
            inp = json.loads(r["j"] or "{}")
        except (ValueError, TypeError):
            continue
        cmd = (inp.get("command") if isinstance(inp, dict) else "") or ""
        low = str(cmd).lower()
        for sig in SIGNALS:
            if sig in low:
                (recent if float(r["e"] or 0) >= cutoff else old).add(sig)
    return {"recent": recent, "old": old}


def find_claude_md(project_path: str | None) -> str | None:
    """Locate a CLAUDE.md at a project root (case-insensitive). None if absent.

    Each candidate is resolved with ``realpath`` and confirmed to live *inside* the
    (also resolved) project directory before it is returned — a defence against a
    crafted project value tunnelling out via a symlink or ``..`` segment. Only a
    fixed basename is ever joined, so this is belt-and-braces, but it keeps the
    file we open provably contained.
    """
    if not project_path:
        return None
    base = os.path.realpath(project_path)
    if not os.path.isdir(base):
        return None
    for name in ("CLAUDE.md", "claude.md", "Claude.md"):
        cand = os.path.realpath(os.path.join(base, name))
        try:
            contained = os.path.commonpath([base, cand]) == base
        except ValueError:
            contained = False
        if contained and os.path.isfile(cand):
            return cand
    return None


def verify(conn, project: str, now: _dt.datetime | None = None) -> dict:
    """Verify a project's CLAUDE.md. ``project`` is a path or short name.

    The project path is resolved *from the index* (an already-indexed cwd), never
    taken as a raw filesystem path from the caller — so an arbitrary path can't be
    coaxed into a file read. Reads its CLAUDE.md, extracts claims and scores them;
    gracefully reports when no CLAUDE.md is found or the project is unknown.
    """
    row = conn.execute(
        "SELECT project FROM sessions WHERE project=? OR project_name=? LIMIT 1",
        (str(project), str(project)),
    ).fetchone()
    project_path = row["project"] if row else None
    md_path = find_claude_md(project_path)
    if not md_path:
        return {"project": project, "claude_md_found": False, "claims": [],
                "overall_score": 0.0,
                "note": "no CLAUDE.md found for this project"}
    try:
        with open(md_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return {"project": project, "claude_md_found": False, "claims": [],
                "overall_score": 0.0, "note": "CLAUDE.md unreadable"}

    claims = extract_claims(text)
    evidence = gather_evidence(conn, str(project_path), now=now)
    scored = verify_claims(claims, evidence)
    verified = sum(1 for c in scored if c["status"] == VERIFIED)
    score = round(verified / len(scored), 3) if scored else 0.0
    return {
        "project": project,
        "claude_md_found": True,
        "claims": scored,
        "overall_score": score,
        "verified": verified,
        "total": len(scored),
    }
