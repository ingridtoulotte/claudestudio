"""Multi-machine sync, zero cloud (Feature 2.4, v0.6.0).

Keep your ClaudeStudio index in sync across a work laptop and a personal machine
without any cloud service. Two backends, both stdlib ``subprocess`` over tools
you already have:

  * **git** — version the ``~/.claudestudio/`` directory in a repo and push/pull
    to any remote git can reach (a NAS, another box over SSH, a private GitHub
    repo). History + conflict detection come for free.
  * **rsync** — a plain mirror when git isn't wanted/available.

Hard rules: only ``~/.claudestudio/`` is ever touched — never the original
``.jsonl`` session files. ``--dry-run`` prints the exact commands without running
the mutating ones. Every command runner is injectable, so the self-test drives
the whole state machine without forking a real ``git``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

STATE_FILE = ".claudestudio-sync.json"
_GIT_TIMEOUT_S = 30.0


class RunResult:
    """A tiny, subprocess-shaped result so a mock runner is a one-liner."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _default_runner(args: list[str], cwd: str) -> RunResult:
    try:
        p = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_S, check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return RunResult(127, "", str(exc))
    return RunResult(p.returncode, p.stdout or "", p.stderr or "")


def default_state_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".claudestudio")


# ---------------------------------------------------------------------------
# state file  (last push / pull / bytes / conflict)
# ---------------------------------------------------------------------------

def _state_path(state_dir: str) -> str:
    return os.path.join(state_dir, STATE_FILE)


def read_state(state_dir: str) -> dict:
    try:
        with open(_state_path(state_dir), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(state_dir: str, **patch) -> dict:
    state = read_state(state_dir)
    state.update(patch)
    os.makedirs(state_dir, exist_ok=True)
    with open(_state_path(state_dir), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    return state


# ---------------------------------------------------------------------------
# method detection
# ---------------------------------------------------------------------------

def detect_method(state_dir: str, *, run=None, prefer="auto") -> str:
    """Choose 'git' or 'rsync'. `prefer` forces a backend; 'auto' picks git when a
    repo already exists or git is installed, else rsync when available."""
    if prefer in ("git", "rsync"):
        return prefer
    if os.path.isdir(os.path.join(state_dir, ".git")):
        return "git"
    if shutil.which("git"):
        return "git"
    if shutil.which("rsync"):
        return "rsync"
    return "git"  # report git; the command will surface the real "not installed"


# ---------------------------------------------------------------------------
# command planning  (the heart of dry-run + testability)
# ---------------------------------------------------------------------------

def plan(action: str, state_dir: str, remote: str, method: str) -> list[list[str]]:
    """The ordered command list for an action. Pure — no execution, no I/O.

    `action` is 'push' or 'pull'. This is exactly what ``--dry-run`` prints and
    what the runner executes, so the two can never drift.
    """
    is_git_repo = os.path.isdir(os.path.join(state_dir, ".git"))
    if method == "rsync":
        local = state_dir.rstrip("/\\") + os.sep
        if action == "push":
            return [["rsync", "-az", "--delete", local, remote]]
        return [["rsync", "-az", "--delete", remote.rstrip("/\\") + "/", local]]
    # git
    cmds: list[list[str]] = []
    if not is_git_repo:
        cmds.append(["git", "init"])
        if remote:
            cmds.append(["git", "remote", "add", "origin", remote])
    if action == "push":
        cmds.append(["git", "add", "-A"])
        cmds.append(["git", "commit", "-m", "claudestudio sync"])
        cmds.append(["git", "push", "origin", "HEAD"])
    else:  # pull
        cmds.append(["git", "fetch", "origin"])
        cmds.append(["git", "pull", "--ff-only", "origin", "HEAD"])
    return cmds


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def _run_plan(cmds, state_dir, run) -> dict:
    """Run a command plan, stopping at the first hard failure. Returns a report.

    A ``git commit`` that fails because there's nothing to commit is *not* a hard
    failure (the index didn't change) — it's reported as ``no_changes`` and the
    plan continues so a push still runs.
    """
    ran, ok, conflict, no_changes = [], True, False, False
    output = []
    for cmd in cmds:
        res = run(cmd, state_dir)
        ran.append(" ".join(cmd))
        out = (res.stdout or "") + (res.stderr or "")
        output.append(out.strip())
        if res.returncode == 0:
            continue
        low = out.lower()
        if cmd[:2] == ["git", "commit"] and ("nothing to commit" in low or "no changes" in low):
            no_changes = True
            continue
        if "conflict" in low or "non-fast-forward" in low or "diverged" in low:
            conflict = True
        ok = False
        break
    return {
        "ok": ok, "conflict": conflict, "no_changes": no_changes,
        "commands": ran, "output": [o for o in output if o],
    }


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        if os.sep + ".git" in root:
            continue
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


def sync(action: str, *, state_dir=None, remote="", method="auto",
         dry_run=False, run=None, now=None) -> dict:
    """Push or pull the index directory. Returns a structured report.

    The report always carries ``commands`` (the planned/run commands), so a
    ``--dry-run`` and a real run are introspectable the same way. On a successful
    real run the sync state file is updated with the timestamp + byte count.
    """
    if action not in ("push", "pull"):
        raise ValueError(f"sync action must be push/pull, got {action!r}")
    state_dir = state_dir or default_state_dir()
    run = run or _default_runner
    resolved = detect_method(state_dir, run=run, prefer=method)
    cmds = plan(action, state_dir, remote, resolved)

    if dry_run:
        return {
            "action": action, "method": resolved, "remote": remote,
            "dry_run": True, "ok": True,
            "commands": [" ".join(c) for c in cmds],
        }

    if resolved == "rsync" and not remote:
        return {"action": action, "method": resolved, "ok": False,
                "error": "rsync requires a --remote (host:path) target",
                "commands": [" ".join(c) for c in cmds]}

    report = _run_plan(cmds, state_dir, run)
    report.update({"action": action, "method": resolved, "remote": remote, "dry_run": False})
    if report["ok"]:
        stamp = float(now) if now is not None else time.time()
        report["bytes"] = _dir_bytes(state_dir)
        key = "last_push" if action == "push" else "last_pull"
        _write_state(state_dir, **{
            key: stamp, "bytes": report["bytes"],
            "method": resolved, "remote": remote,
            "conflict": report["conflict"],
        })
    return report


def status(state_dir=None) -> dict:
    """Last push / pull, bytes transferred, and whether a conflict is outstanding."""
    state_dir = state_dir or default_state_dir()
    st = read_state(state_dir)
    return {
        "state_dir": state_dir,
        "is_git_repo": os.path.isdir(os.path.join(state_dir, ".git")),
        "last_push": st.get("last_push"),
        "last_pull": st.get("last_pull"),
        "bytes": st.get("bytes"),
        "method": st.get("method"),
        "remote": st.get("remote"),
        "conflict": bool(st.get("conflict")),
    }
