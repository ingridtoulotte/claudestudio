"""Session templates with auto-context (Feature 7, v0.6.3).

A bridge from ClaudeStudio (the history viewer) back into the Claude Code
workflow: pick a template, fill the blanks, and get a ready-to-paste context
block for a brand-new Claude Code session. The ``{auto-context}`` placeholder is
filled deterministically from your *own* session history (top tools, a recurring
prompt pattern, recent error types) — the same grounded, no-model-calls approach
as the narrative engine.

Built-in templates ship inside the package (``data/templates/*.md``); user
templates live in ``~/.claudestudio/templates/`` and override built-ins of the
same name. Everything is plain text + stdlib.
"""

from __future__ import annotations

import os
import re
import sqlite3

# {placeholder} names — letters, digits, hyphen, underscore. `{auto-context}` is a
# reserved placeholder filled from history, never asked of the user.
_VAR_RE = re.compile(r"\{([a-zA-Z0-9_-]+)\}")
AUTO_CONTEXT_VAR = "auto-context"


def builtin_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "templates")


def user_templates_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".claudestudio", "templates")


def _scan(dir_path: str, source: str) -> dict:
    out: dict = {}
    if not os.path.isdir(dir_path):
        return out
    for name in sorted(os.listdir(dir_path)):
        if not name.endswith(".md") or name.startswith("_"):
            continue
        stem = name[:-3]
        try:
            with open(os.path.join(dir_path, name), encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            continue
        out[stem] = {"name": stem, "source": source, "body": body,
                     "vars": template_vars(body)}
    return out


def _all(user_dir: str | None = None) -> dict:
    """Built-ins overlaid by user templates of the same name (user wins)."""
    merged = _scan(builtin_dir(), "builtin")
    merged.update(_scan(user_dir or user_templates_dir(), "user"))
    return merged


def template_vars(body: str) -> list[str]:
    """The fill-in variables in a template body, in first-seen order, minus the
    reserved ``auto-context`` placeholder."""
    seen: list[str] = []
    for m in _VAR_RE.finditer(body):
        v = m.group(1)
        if v == AUTO_CONTEXT_VAR or v in seen:
            continue
        seen.append(v)
    return seen


def list_templates(user_dir: str | None = None) -> list[dict]:
    """All templates (built-in + user) as ``{name, source, vars, title}``."""
    items = []
    for t in _all(user_dir).values():
        items.append({"name": t["name"], "source": t["source"], "vars": t["vars"],
                      "title": _title(t["body"], t["name"])})
    return sorted(items, key=lambda x: x["name"])


def get_template(name: str, user_dir: str | None = None) -> dict | None:
    return _all(user_dir).get(str(name))


def _title(body: str, fallback: str) -> str:
    first = (body.strip().splitlines() or [""])[0].strip()
    return first[:80] if first else fallback


def auto_context(conn: sqlite3.Connection, project: str | None = None) -> str:
    """A deterministic one-line context block mined from the user's history.

    Top tools used, a recurring prompt pattern, and recent error types — all
    grounded in the local index, no model calls. Empty index → a friendly
    "no history yet" note so a rendered template is always usable.
    """
    parts: list[str] = []
    args = [project, project] if project else []

    # top tools
    try:
        if project:
            rows = conn.execute(
                "SELECT t.name, COUNT(*) n FROM tool_calls t "
                "JOIN sessions s USING(session_id) "
                "WHERE (s.project = ? OR s.project_name = ?) AND t.name != '' "
                "GROUP BY t.name ORDER BY n DESC LIMIT 4", args).fetchall()
        else:
            rows = conn.execute(
                "SELECT name, COUNT(*) n FROM tool_calls WHERE name != '' "
                "GROUP BY name ORDER BY n DESC LIMIT 4").fetchall()
        tools = [r["name"] for r in rows]
        if tools:
            parts.append("commonly use " + ", ".join(tools))
    except sqlite3.OperationalError:
        pass

    # a recurring prompt pattern (reuse the deterministic pattern miner)
    try:
        from . import patterns
        pats = patterns.extract_patterns(conn, min_count=2)
        if pats:
            sample = (pats[0].get("canonical_text") or "").strip()
            if sample:
                parts.append(f"often ask: \"{sample[:70]}\"")
    except Exception:  # noqa: BLE001 — context is best-effort
        pass

    # recent error types
    try:
        rows = conn.execute(
            "SELECT error_type, COUNT(*) n FROM session_errors "
            "GROUP BY error_type ORDER BY n DESC LIMIT 3").fetchall()
        errs = [r["error_type"] for r in rows if r["error_type"] != "unknown"]
        if errs:
            parts.append("recent friction: " + ", ".join(errs))
    except sqlite3.OperationalError:
        pass

    if project:
        parts.insert(0, f"in project {project}")
    return "; ".join(parts) if parts else "no indexed history yet"


def render(conn: sqlite3.Connection, name: str, variables: dict | None = None,
           *, include_context: bool = True, user_dir: str | None = None) -> dict:
    """Fill a template's blanks + auto-context. Returns ``{name, rendered, missing}``.

    Unknown ``{vars}`` left unfilled are reported in ``missing`` (the placeholder
    is kept verbatim so nothing is silently dropped). ``include_context=False``
    skips the history mining (used by the clipboard "raw" toggle).
    """
    tpl = get_template(name, user_dir)
    if tpl is None:
        return {"error": f"no template named {name!r}"}
    variables = {str(k): str(v) for k, v in (variables or {}).items()}
    project = variables.get("project")
    body = tpl["body"]

    if include_context and ("{" + AUTO_CONTEXT_VAR + "}") in body:
        body = body.replace("{" + AUTO_CONTEXT_VAR + "}", auto_context(conn, project))

    missing: list[str] = []

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key == AUTO_CONTEXT_VAR:
            return m.group(0)  # already handled (or context disabled)
        if key in variables:
            return variables[key]
        missing.append(key)
        return m.group(0)

    rendered = _VAR_RE.sub(_sub, body)
    return {"name": name, "rendered": rendered, "missing": sorted(set(missing)),
            "source": tpl["source"]}


def create_template(name: str, body: str = "", user_dir: str | None = None) -> dict:
    """Write a new user template file. Returns its path. Refuses path separators."""
    safe = str(name).strip()
    if not safe or "/" in safe or "\\" in safe or safe.startswith("."):
        raise ValueError(f"invalid template name: {name!r}")
    d = user_dir or user_templates_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, safe + ".md")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body or f"{safe} `{{file}}`: {{goal}}. Context: {{auto-context}}.\n")
    return {"name": safe, "path": path}
