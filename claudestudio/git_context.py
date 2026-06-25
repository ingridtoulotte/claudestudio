"""Git context for a session (Feature 7, v0.5.2) — read-only, best-effort.

"Which branch was I on when this happened?" is a core debugging question. For a
session whose project directory is a git repo, we resolve the commit that was
``HEAD`` at (or just before) the session's time by cross-referencing the session
timestamp with ``git log``, plus the repo's current branch.

Hard rules:
  * **Never raises.** Not a repo, git not installed, a detached/empty repo, a
    timeout — every failure path returns ``None``. Git context is a nice-to-have
    badge; it must never break a session view.
  * **Read-only.** Only ``git log`` / ``git branch`` — no writes, no network,
    no checkout. Honours the local-first promise.
  * **Cached.** Results are memoised in a bounded in-memory LRU so opening many
    sessions in one project doesn't fork ``git`` over and over.
"""

from __future__ import annotations

import collections
import datetime as _dt
import os
import subprocess
import threading

# Bounded memo: (resolved_project_path, day_bucket_epoch) -> context dict | None.
# 512 entries is plenty for a browsing session and keeps memory trivial.
_CACHE_MAX = 512
_cache: collections.OrderedDict[tuple, dict | None] = collections.OrderedDict()
_cache_lock = threading.Lock()

# Don't let a wedged git hang a request thread.
_GIT_TIMEOUT_S = 3.0


def _cache_get(key):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return True, _cache[key]
    return False, None


def _cache_put(key, value):
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def _run_git(project_path: str, args: list[str]) -> str | None:
    """Run a read-only git command in `project_path`. Returns stdout, or None on
    any failure (git missing, not a repo, non-zero exit, timeout)."""
    try:
        proc = subprocess.run(
            ["git", "-C", project_path, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def get_current_branch(project_path: str) -> str | None:
    """The repo's current branch name, or None (detached HEAD, not a repo, …)."""
    if not project_path or not os.path.isdir(project_path):
        return None
    out = _run_git(project_path, ["branch", "--show-current"])
    if out is None:
        return None
    branch = out.strip()
    return branch or None


def get_git_context(project_path: str, timestamp: float) -> dict | None:
    """Resolve the commit that was HEAD at/just before `timestamp`.

    `timestamp` is epoch seconds (a session's last activity). Returns
    ``{sha, short_sha, branch, message}`` or ``None`` when no git context is
    available. Memoised per (project, day). Never raises.
    """
    if not project_path or not os.path.isdir(project_path):
        return None
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return None

    # Bucket by day so all of a day's sessions in one repo share a cache entry.
    key = (os.path.realpath(project_path), int(ts // 86400))
    hit, cached = _cache_get(key)
    if hit:
        return cached

    result = _resolve(project_path, ts)
    _cache_put(key, result)
    return result


def _resolve(project_path: str, ts: float) -> dict | None:
    # ISO for --until. An out-of-range epoch (corrupt log) can't be formatted —
    # treat as "no context" rather than letting it raise.
    try:
        until = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return None

    # NUL-separated fields so a commit subject with spaces/pipes survives intact.
    fmt = "%H%x00%h%x00%s"
    out = _run_git(
        project_path,
        ["log", "--until=" + until, "-1", "--format=" + fmt],
    )
    if not out:
        # No commit at/before the session time (or not a repo): fall back to the
        # tip so a brand-new clone still shows *some* context, else give up.
        out = _run_git(project_path, ["log", "-1", "--format=" + fmt])
        if not out:
            return None
    parts = out.strip().split("\x00")
    if len(parts) < 2 or not parts[0]:
        return None
    sha, short_sha = parts[0], parts[1]
    message = parts[2] if len(parts) > 2 else ""
    return {
        "sha": sha,
        "short_sha": short_sha,
        "branch": get_current_branch(project_path),
        "message": message,
    }


def clear_cache() -> None:
    """Drop the memo (used by tests; harmless in production)."""
    with _cache_lock:
        _cache.clear()
