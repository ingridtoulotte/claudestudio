"""Automated CHANGELOG draft generator (Feature 2.11, v0.6.0).

``claudestudio changelog-draft`` reads the git log since the last version tag and
sorts the commits into Keep-a-Changelog sections (Added / Changed / Fixed /
Security) using simple, auditable keyword heuristics. It writes a *draft* — a
human still edits it — but it removes the blank-page problem at release time.

Read-only: only ``git log`` / ``git describe`` via ``subprocess``. If git isn't
available it says so and exits cleanly. The classifier is a pure function so the
self-test pins exact output for a fixed commit list — no real repo needed.
"""

from __future__ import annotations

import subprocess

_GIT_TIMEOUT_S = 10.0

# Section -> ordered keyword triggers. First matching section wins, so the order
# of SECTIONS matters: Security is checked before Fixed (a "security fix" is
# Security), and Removed/Changed before the generic Added fallback.
SECTIONS = ["Security", "Removed", "Fixed", "Changed", "Added"]
_KEYWORDS = {
    "Security": ("security", "vuln", "cve", "exploit", "sanitiz", "harden",
                 "traversal", "injection", "csrf", "xss", "auth bypass"),
    "Removed": ("remove", "delete", "drop ", "deprecate"),
    "Fixed": ("fix", "bug", "crash", "regression", "hotfix", "patch", "resolve",
              "correct", "repair"),
    "Changed": ("change", "refactor", "rename", "update", "bump", "improve",
                "tweak", "rework", "migrate", "breaking"),
    "Added": ("add", "feat", "feature", "introduce", "implement", "new ", "support"),
}


def classify(subject: str) -> str:
    """Bucket one commit subject into a CHANGELOG section. Pure + deterministic.

    A conventional-commit prefix (``feat:``/``fix:``/``security:``) is honoured
    first; otherwise the keyword tables decide. Anything unmatched falls to Added,
    the kindest default for a forgotten line.
    """
    s = (subject or "").strip().lower()
    prefix = s.split(":", 1)[0].strip() if ":" in s[:20] else ""
    prefix_map = {
        "feat": "Added", "feature": "Added",
        "fix": "Fixed", "bugfix": "Fixed", "hotfix": "Fixed",
        "security": "Security", "sec": "Security",
        "refactor": "Changed", "perf": "Changed", "chore": "Changed",
        "remove": "Removed", "revert": "Changed", "docs": "Changed",
    }
    if prefix in prefix_map:
        return prefix_map[prefix]
    for section in SECTIONS:
        if any(k in s for k in _KEYWORDS[section]):
            return section
    return "Added"


def _clean_subject(subject: str) -> str:
    """Strip a conventional-commit prefix and capitalise for the changelog line."""
    s = (subject or "").strip()
    if ":" in s[:24]:
        head, _, rest = s.partition(":")
        if head.strip().lower().split("(")[0] in (
            "feat", "feature", "fix", "bugfix", "hotfix", "security", "sec",
            "refactor", "perf", "chore", "remove", "revert", "docs", "test", "ci",
        ):
            s = rest.strip()
    return s[:1].upper() + s[1:] if s else s


def group_commits(subjects: list[str]) -> dict:
    """Group commit subjects into ``{section: [lines]}`` for the sections used."""
    groups: dict[str, list[str]] = {s: [] for s in SECTIONS}
    for subj in subjects:
        subj = (subj or "").strip()
        if not subj or subj.lower().startswith("merge "):
            continue
        groups[classify(subj)].append(_clean_subject(subj))
    return {k: v for k, v in groups.items() if v}


def render_draft(subjects: list[str], *, version: str = "Unreleased",
                 date: str | None = None) -> str:
    """Render the Markdown draft. Deterministic for a fixed input."""
    header = f"## [{version}]" + (f" - {date}" if date else "")
    lines = [header, ""]
    groups = group_commits(subjects)
    if not groups:
        lines.append("_No commits since the last tag._")
        return "\n".join(lines) + "\n"
    # Keep-a-Changelog canonical section order
    for section in ["Added", "Changed", "Removed", "Fixed", "Security"]:
        items = groups.get(section)
        if not items:
            continue
        lines.append(f"### {section}")
        for it in items:
            lines.append(f"- {it}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# git integration (best-effort; never required for the pure renderer above)
# ---------------------------------------------------------------------------

def _run_git(args: list[str], run=None) -> tuple[int, str]:
    if run is not None:
        res = run(args)
        return res
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True,
                           timeout=_GIT_TIMEOUT_S, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 127, ""
    return p.returncode, p.stdout or ""


def last_tag(run=None) -> str | None:
    code, out = _run_git(["describe", "--tags", "--abbrev=0"], run)
    tag = out.strip()
    return tag if code == 0 and tag else None


def commit_subjects_since(tag: str | None, run=None) -> list[str]:
    rng = f"{tag}..HEAD" if tag else "HEAD"
    code, out = _run_git(["log", rng, "--no-merges", "--format=%s"], run)
    if code != 0:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def generate(version: str = "Unreleased", date: str | None = None, run=None) -> dict:
    """Read git, classify, render. Returns ``{available, tag, draft, count}``.

    ``available`` is False when git can't be reached — the CLI prints a friendly
    note and exits 0 rather than failing a release script.
    """
    tag = last_tag(run)
    if tag is None:
        # distinguish "no tags yet" (git works) from "no git" by trying a plain log
        code, _ = _run_git(["rev-parse", "--git-dir"], run)
        if code != 0:
            return {"available": False, "tag": None, "draft": "", "count": 0}
    subjects = commit_subjects_since(tag, run)
    return {
        "available": True, "tag": tag, "count": len(subjects),
        "draft": render_draft(subjects, version=version, date=date),
    }
