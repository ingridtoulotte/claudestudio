"""Plugin system for ClaudeStudio — drop a ``.py`` file in ``~/.claudestudio/plugins/``.

A plugin is a plain Python module that optionally defines any of these hooks::

    register_routes(handler_class) -> None    # add HTTP routes to the server
    register_mcp_tools(tools_list) -> None    # append MCP tool dicts
    register_cli_commands(subparsers) -> None # add CLI subcommands
    on_session_indexed(db, session_id) -> None# called after each session is indexed

Discovery scans ``~/.claudestudio/plugins/*.py`` at startup via ``importlib.util``.
Plugins are **isolated**: an exception while importing one logs a warning and
loading continues — a broken plugin can never take the app down. Plugins may only
import the stdlib and ``claudestudio.*``; an unexpected third-party import is
flagged (best-effort, via ``sys.stdlib_module_names`` where available).

Foundations only (v0.6.1): additive, no breaking surface.

Usage::

    from claudestudio import plugin_loader
    plugin_loader.load_plugins()              # once, at startup
    plugin_loader.apply_route_hooks(Handler)  # wire any HTTP routes
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from dataclasses import dataclass, field

HOOK_NAMES = (
    "register_routes", "register_mcp_tools",
    "register_cli_commands", "on_session_indexed",
)


@dataclass
class PluginMeta:
    """A discovered (not yet imported) plugin file."""
    name: str
    path: str


@dataclass
class LoadedPlugin:
    """The result of attempting to import one plugin file."""
    name: str
    path: str
    module: object | None = None
    hooks: list = field(default_factory=list)
    error: str | None = None
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.module is not None


# Singleton set at startup by load_plugins().
_LOADED: list = []


def default_plugins_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".claudestudio", "plugins")


def discover_plugins(plugins_dir=None) -> list:
    """List plugin files in `plugins_dir` (default ``~/.claudestudio/plugins``).

    Returns ``[]`` for a missing directory. Files starting with ``_`` (e.g.
    ``__init__``) are skipped; results are sorted for deterministic load order.
    """
    d = plugins_dir or default_plugins_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        out.append(PluginMeta(name=name[:-3], path=os.path.join(d, name)))
    return out


def load_plugins(plugins_dir=None) -> list:
    """Import every discovered plugin, isolating failures. Sets the singleton.

    Returns one :class:`LoadedPlugin` per discovered file — successes carry the
    imported module and the list of hooks it defines; failures carry an ``error``
    string and are skipped (with a warning) rather than raising.
    """
    global _LOADED
    loaded = []
    for meta in discover_plugins(plugins_dir):
        loaded.append(_load_one(meta))
    _LOADED = loaded
    return loaded


def _load_one(meta) -> LoadedPlugin:
    lp = LoadedPlugin(name=meta.name, path=meta.path)
    mod_name = f"claudestudio_plugin_{meta.name}"
    before = set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, meta.path)
        if spec is None or spec.loader is None:
            lp.error = "could not create import spec"
            _warn(lp.error, meta)
            return lp
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 — a bad plugin must not crash startup
        lp.error = traceback.format_exc(limit=3).strip().splitlines()[-1]
        _warn(f"failed to import: {lp.error}", meta)
        return lp
    lp.module = module
    lp.hooks = [h for h in HOOK_NAMES if callable(getattr(module, h, None))]
    for w in _foreign_imports(before):
        msg = f"imports non-stdlib package {w!r} (plugins should use stdlib + claudestudio.*)"
        lp.warnings.append(msg)
        _warn(msg, meta)
    return lp


def _foreign_imports(before: set) -> list:
    """Top-level modules imported during load that aren't stdlib or claudestudio.

    Best-effort: ``sys.stdlib_module_names`` exists on 3.10+; on 3.9 we skip the
    check rather than risk false positives, so the guard never blocks a load.
    """
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:
        return []
    new = set(sys.modules) - before
    foreign = set()
    for m in new:
        top = m.split(".")[0]
        if not top or top.startswith("_"):
            continue
        if top in stdlib or top == "claudestudio" or top.startswith("claudestudio_plugin_"):
            continue
        foreign.add(top)
    return sorted(foreign)


def get_loaded_plugins() -> list:
    """The plugins loaded at startup (the singleton). Empty before load_plugins()."""
    return _LOADED


# ---------------------------------------------------------------------------
# hook application — each call is isolated so one bad hook can't break the rest
# ---------------------------------------------------------------------------

def apply_route_hooks(handler_class) -> None:
    _run_hook("register_routes", handler_class)


def apply_cli_hooks(subparsers) -> None:
    _run_hook("register_cli_commands", subparsers)


def collect_mcp_tools(tools_list) -> None:
    _run_hook("register_mcp_tools", tools_list)


def run_session_indexed_hooks(db, session_id) -> None:
    _run_hook("on_session_indexed", db, session_id)


def _run_hook(hook_name: str, *args) -> None:
    for lp in _LOADED:
        if not lp.ok or hook_name not in lp.hooks:
            continue
        fn = getattr(lp.module, hook_name, None)
        if not callable(fn):
            continue
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 — isolate hook failures
            err = traceback.format_exc(limit=2).strip().splitlines()[-1]
            lp.warnings.append(f"{hook_name} raised: {err}")
            _warn(f"hook {hook_name} raised: {err}", lp)


def _warn(msg: str, meta) -> None:
    name = getattr(meta, "name", "?")
    sys.stderr.write(f"  ⚠  plugin {name!r}: {msg}\n")
