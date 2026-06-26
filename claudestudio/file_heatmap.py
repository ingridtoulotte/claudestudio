"""Per-file impact heatmap — which files does Claude touch most?

Aggregates every file-editing tool call (Edit / Write / MultiEdit / …) across the
indexed corpus into a file × metric matrix: edit count, distinct sessions,
recency, tokens, and a normalised 0–1 ``heat_score``. Also renders a pure-stdlib
``xml.etree`` SVG (top 20 files × last 12 ISO weeks) the SPA embeds directly.

Pure read over the local index — no model calls, no network.

Usage::

    from claudestudio.file_heatmap import compute_file_heatmap, heatmap_svg
    data = compute_file_heatmap(conn, project_id=None)
    svg = heatmap_svg(data)        # valid standalone <svg> string, role="img"
"""

from __future__ import annotations

import datetime as _dt
import json
import time
import xml.etree.ElementTree as ET

from .parser import _parse_ts, local_datetime

# Tools that mutate a file on disk (mirrors narrative / api.tool_diff).
_EDIT_TOOLS = {
    "Edit", "MultiEdit", "Update", "str_replace_based_edit", "str_replace_editor",
    "Write", "create_file", "write_to_file", "NotebookEdit",
}
_PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file")

_RECENCY_HALF_LIFE_DAYS = 14.0
BRAND_PURPLE = "#9a8cff"
DARK_BG = "#0d0d14"


def _path_of(input_json: str) -> str | None:
    """Pull the edited file path out of a tool call's stored input JSON."""
    try:
        inp = json.loads(input_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(inp, dict):
        return None
    for k in _PATH_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().replace("\\", "/")
    return None


def _to_epoch(v, *, end_of_day=False):
    """YYYY-MM-DD or epoch → epoch seconds, or None. Range-safe (never raises)."""
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        d = _dt.datetime.strptime(str(v), "%Y-%m-%d")
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59)
        return d.timestamp()
    except (ValueError, OSError, OverflowError):
        return None


def _iso_week(epoch: float) -> str | None:
    dt = local_datetime(epoch)
    if dt is None:
        return None
    iso = dt.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def compute_file_heatmap(conn, project_id=None, since=None, until=None) -> dict:
    """File × metric heatmap data over the (optionally filtered) corpus.

    `project_id` restricts to one project (matched on project or project_name).
    `since`/`until` (YYYY-MM-DD or epoch) bound the touching session's activity.
    Returns the structured dict documented in the module/feature spec; an empty
    corpus yields ``{"files": [], "total_files": 0, …}`` safely.
    """
    since_e = _to_epoch(since)
    until_e = _to_epoch(until, end_of_day=True)

    where = [f"t.name IN ({','.join('?' * len(_EDIT_TOOLS))})"]
    args: list = list(_EDIT_TOOLS)
    if project_id:
        where.append("(s.project = ? OR s.project_name = ?)")
        args += [project_id, project_id]
    if since_e is not None:
        where.append("s.last_epoch >= ?")
        args.append(since_e)
    if until_e is not None:
        where.append("s.first_epoch <= ?")
        args.append(until_e)

    rows = conn.execute(
        "SELECT t.input_json, t.ts, s.session_id, s.project_name, s.last_epoch, "
        "       s.input_tokens, s.output_tokens "
        "FROM tool_calls t JOIN sessions s USING(session_id) "
        "WHERE " + " AND ".join(where),
        args,
    ).fetchall()

    files: dict[str, dict] = {}
    seen_session_tokens: dict[str, set] = {}  # path -> set(session_id) for token sum
    for r in rows:
        path = _path_of(r["input_json"])
        if not path:
            continue
        touch_epoch = _parse_ts(r["ts"]) or (r["last_epoch"] or 0.0)
        f = files.get(path)
        if f is None:
            f = files[path] = {
                "path": path, "edit_count": 0, "session_count": 0,
                "last_touched": 0.0, "total_tokens": 0,
                "projects": [], "_sessions": set(), "_projects": set(),
                "weeks": {},
            }
            seen_session_tokens[path] = set()
        f["edit_count"] += 1
        f["last_touched"] = max(f["last_touched"], touch_epoch)
        f["_sessions"].add(r["session_id"])
        if r["project_name"]:
            f["_projects"].add(r["project_name"])
        if r["session_id"] not in seen_session_tokens[path]:
            seen_session_tokens[path].add(r["session_id"])
            f["total_tokens"] += (r["input_tokens"] or 0) + (r["output_tokens"] or 0)
        wk = _iso_week(touch_epoch)
        if wk:
            f["weeks"][wk] = f["weeks"].get(wk, 0) + 1

    out_files = []
    for f in files.values():
        f["session_count"] = len(f["_sessions"])
        f["projects"] = sorted(f["_projects"])
        del f["_sessions"], f["_projects"]
        out_files.append(f)

    _assign_heat_scores(out_files)
    out_files.sort(key=lambda x: (-x["heat_score"], x["path"]))

    touched = [f["last_touched"] for f in out_files if f["last_touched"]]
    return {
        "files": out_files,
        "total_files": len(out_files),
        "date_range": {
            "from": min(touched) if touched else None,
            "to": max(touched) if touched else None,
        },
    }


def _assign_heat_scores(files: list[dict]) -> None:
    """Fill each file's normalised 0–1 `heat_score` in place.

    ``0.5·(edits/max_edits) + 0.3·(sessions/max_sessions) + 0.2·recency`` where
    recency decays exponentially (14-day half-life) from now to the last touch.
    """
    if not files:
        return
    max_edit = max((f["edit_count"] for f in files), default=0) or 1
    max_sess = max((f["session_count"] for f in files), default=0) or 1
    now = time.time()
    for f in files:
        days = max(0.0, (now - (f["last_touched"] or 0.0)) / 86400.0)
        recency = 0.5 ** (days / _RECENCY_HALF_LIFE_DAYS) if f["last_touched"] else 0.0
        score = (0.5 * (f["edit_count"] / max_edit)
                 + 0.3 * (f["session_count"] / max_sess)
                 + 0.2 * recency)
        f["heat_score"] = round(min(1.0, max(0.0, score)), 4)


def _recent_weeks(n: int = 12) -> list[str]:
    """The last `n` ISO week labels up to today, oldest → newest."""
    today = local_datetime(time.time()) or _dt.datetime(2026, 1, 1)
    weeks = []
    for i in range(n - 1, -1, -1):
        d = today - _dt.timedelta(weeks=i)
        iso = d.isocalendar()
        weeks.append(f"{iso[0]:04d}-W{iso[1]:02d}")
    # de-dup while preserving order (week boundaries can repeat across a year edge)
    seen, out = set(), []
    for w in weeks:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def heatmap_svg(data: dict, *, rows: int = 20, weeks: int = 12) -> str:
    """Render the heatmap as a standalone, accessible SVG string.

    Rows are the top `rows` files by heat score; columns the last `weeks` ISO
    weeks. Cell opacity tracks that file-week's edit volume (empty = 0.07). The
    ``<svg>`` carries ``role="img"`` + ``aria-label`` (WCAG 2.1 AA) and each cell
    a ``data-tip`` attribute the SPA reads for tooltips.
    """
    files = data.get("files", [])[:rows]
    week_labels = _recent_weeks(weeks)
    cell_w, cell_h = 26, 20
    label_w, top_h = 260, 48
    width = label_w + cell_w * len(week_labels) + 16
    height = top_h + cell_h * max(1, len(files)) + 16

    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width), "height": str(height),
        "viewBox": f"0 0 {width} {height}",
        "role": "img",
        "aria-label": f"File edit heatmap: top {len(files)} files over "
                      f"{len(week_labels)} weeks",
    })
    ET.SubElement(svg, "rect", {"x": "0", "y": "0", "width": str(width),
                                "height": str(height), "fill": DARK_BG})

    # column headers (ISO week)
    for ci, wk in enumerate(week_labels):
        x = label_w + ci * cell_w + cell_w / 2
        t = ET.SubElement(svg, "text", {
            "x": f"{x:.1f}", "y": str(top_h - 28), "fill": "#9aa0b4",
            "font-size": "8", "text-anchor": "middle",
            "font-family": "monospace", "transform": f"rotate(-45 {x:.1f} {top_h-28})",
        })
        t.text = wk.split("-")[-1]  # 'W23'

    # find max cell value for opacity scaling
    max_cell = 1
    for f in files:
        for wk in week_labels:
            max_cell = max(max_cell, f.get("weeks", {}).get(wk, 0))

    for ri, f in enumerate(files):
        y = top_h + ri * cell_h
        # filename (bold) + truncated dir
        path = f.get("path", "")
        name = path.rsplit("/", 1)[-1]
        prefix = path[:-len(name)] if name and path.endswith(name) else ""
        prefix = _truncate_left(prefix, 22)
        row_label = ET.SubElement(svg, "text", {
            "x": "8", "y": str(y + cell_h - 6), "font-size": "10",
            "font-family": "monospace",
        })
        if prefix:
            sp = ET.SubElement(row_label, "tspan", {"fill": "#6b7088"})
            sp.text = prefix
        sn = ET.SubElement(row_label, "tspan", {"fill": "#e7e9f3",
                                                "font-weight": "bold"})
        sn.text = name
        for ci, wk in enumerate(week_labels):
            v = f.get("weeks", {}).get(wk, 0)
            opacity = 0.07 if v == 0 else round(0.2 + 0.8 * (v / max_cell), 3)
            ET.SubElement(svg, "rect", {
                "x": str(label_w + ci * cell_w + 2), "y": str(y + 2),
                "width": str(cell_w - 4), "height": str(cell_h - 4),
                "rx": "3", "fill": BRAND_PURPLE, "fill-opacity": str(opacity),
                "data-tip": f"{name} · {wk} · {v} edit{'s' if v != 1 else ''}",
            })
    return ET.tostring(svg, encoding="unicode")


def _truncate_left(s: str, n: int) -> str:
    return ("…" + s[-(n - 1):]) if len(s) > n else s


def top_files(conn, limit: int = 10, project_id=None, since=None, until=None) -> dict:
    """The N hottest files (for the MCP tool / doctor) — trimmed file records."""
    data = compute_file_heatmap(conn, project_id, since, until)
    top = [{"path": f["path"], "edit_count": f["edit_count"],
            "session_count": f["session_count"], "heat_score": f["heat_score"]}
           for f in data["files"][:max(1, int(limit))]]
    return {"files": top, "total_files": data["total_files"]}
