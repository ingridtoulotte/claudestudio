"""Claude Code hook integration — make the index update itself.

Claude Code can run a shell command when a session ends (a *hook* declared in
``~/.claude/settings.json``). Wiring ``claudestudio index`` to that event makes
the whole workspace hands-free: every time you finish a session, the index
refreshes in the background and the app (or ``claudestudio watch``) shows it.

Everything here is pure stdlib ``json`` over a settings file. We read the file
if it exists, merge our entry without clobbering any hook you already have, and
write it back. Uninstall removes only our entry and prunes empty containers, so
it is always safe and fully reversible. No network, no model calls.

Hook wire shape we add (Claude Code ``settings.json``)::

    {
      "hooks": {
        "SessionEnd": [
          {"hooks": [{"type": "command", "command": "claudestudio index"}]}
        ]
      }
    }
"""

from __future__ import annotations

import json
import os

from . import index

# The Claude Code event that fires when a session ends. Reindexing here keeps the
# index current the moment you stop working, with zero manual steps.
HOOK_EVENT = "SessionEnd"
# The command Claude Code runs. `claudestudio index` is incremental, so it only
# touches files whose mtime/size changed — cheap to run on every session end.
HOOK_COMMAND = "claudestudio index"


def default_settings_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def _load_settings(path: str) -> dict:
    """Read settings.json, tolerant of an absent or malformed file.

    A missing file is an empty config. A file that exists but isn't valid JSON
    (or isn't a JSON object) is treated as empty too — install never throws on a
    corrupt settings file, it simply lays our hook on top of a clean object.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_settings(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


def _our_entry() -> dict:
    return {"type": "command", "command": HOOK_COMMAND}


def _groups(settings: dict) -> list:
    """Return the list of hook-groups under our event, or [] if none."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    groups = hooks.get(HOOK_EVENT)
    return groups if isinstance(groups, list) else []


def is_installed(settings: dict) -> bool:
    """True iff our exact command is already wired under the hook event."""
    for group in _groups(settings):
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks", []) or []:
            if isinstance(h, dict) and h.get("command") == HOOK_COMMAND:
                return True
    return False


def install_hook(settings_path: str | None = None) -> dict:
    """Add the post-session reindex hook to settings.json (idempotent).

    Merges into any existing config: other hooks, other events, and other
    ``SessionEnd`` commands are preserved untouched. Installing twice is a no-op
    (no duplicate entry). Returns ``{"path", "installed", "changed", "settings"}``
    where ``settings`` is the exact object written to disk.
    """
    path = settings_path or default_settings_path()
    settings = _load_settings(path)
    if is_installed(settings):
        return {"path": path, "installed": True, "changed": False, "settings": settings}

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):  # a non-dict `hooks` value: replace it cleanly
        hooks = settings["hooks"] = {}
    groups = hooks.get(HOOK_EVENT)
    if not isinstance(groups, list):
        groups = hooks[HOOK_EVENT] = []
    groups.append({"hooks": [_our_entry()]})
    _save_settings(path, settings)
    return {"path": path, "installed": True, "changed": True, "settings": settings}


def uninstall_hook(settings_path: str | None = None) -> dict:
    """Remove our reindex hook, leaving every other hook intact.

    Strips only entries whose command is exactly ``HOOK_COMMAND``, then prunes
    any hook-group and the event key itself if they become empty — so the file
    returns to the shape it had before install. A no-op when not installed.
    """
    path = settings_path or default_settings_path()
    settings = _load_settings(path)
    if not is_installed(settings):
        return {"path": path, "installed": False, "changed": False, "settings": settings}

    hooks = settings.get("hooks", {})
    groups = hooks.get(HOOK_EVENT, [])
    pruned = []
    for group in groups:
        if not isinstance(group, dict):
            pruned.append(group)
            continue
        kept = [h for h in group.get("hooks", []) or []
                if not (isinstance(h, dict) and h.get("command") == HOOK_COMMAND)]
        if kept:
            group["hooks"] = kept
            pruned.append(group)
    if pruned:
        hooks[HOOK_EVENT] = pruned
    else:
        hooks.pop(HOOK_EVENT, None)
    if not hooks:
        settings.pop("hooks", None)
    _save_settings(path, settings)
    return {"path": path, "installed": False, "changed": True, "settings": settings}


def hook_status(settings_path: str | None = None, db_path: str | None = None) -> dict:
    """Report whether the hook is installed and when it (the index) last ran.

    ``last_run_epoch`` is the index database's mtime — a faithful proxy for the
    last time ``claudestudio index`` ran, whether the hook or you triggered it.
    """
    path = settings_path or default_settings_path()
    db = db_path or index.default_db_path()
    last_run = None
    if os.path.exists(db):
        try:
            last_run = os.path.getmtime(db)
        except OSError:
            last_run = None
    return {
        "installed": is_installed(_load_settings(path)),
        "settings_path": path,
        "event": HOOK_EVENT,
        "command": HOOK_COMMAND,
        "last_run_epoch": last_run,
    }
