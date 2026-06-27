"""Tool Chain Visualizer (Feature 6, v0.6.3).

The most common thing a Claude Code session *does* is run tools in sequences —
``Read → Edit → Bash``, ``Grep → Read → Edit``. This module mines those recurring
``tool_A → tool_B → tool_C`` chains across the whole index and renders them as a
Sankey-style flow diagram, plus a ranked table with each chain's frequency,
average cost and average session health.

Pure stdlib: the SVG is built with :mod:`xml.etree.ElementTree`, the analysis is
plain SQL + counting. Deterministic — the same index always yields the same
chains and the same diagram, so the self-test can pin exact output.

The ``days`` time filter is relative to the *newest* indexed session, not the
wall clock, so it behaves identically on a fixture in 2024 and a live index
today (and the self-test stays deterministic). Pass ``now`` to override.
"""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

# Brand palette (matches the rest of the SVG views — file heatmap etc.).
_BRAND = "#9a8cff"
_BG = "#0d0d14"
_NODE = "#1b1b2b"
_TEXT = "#e8e8f0"
_MUTED = "#8a8aa0"
_LINK = "#6c5cc4"

# Chain lengths we mine (2- and 3-grams of consecutive tool calls). A 1-gram is
# just "how often a tool runs" (already covered by tool stats), and 4+ chains are
# too sparse to be useful, so 2–3 is the sweet spot.
_MIN_LEN = 2
_MAX_LEN = 3


def _newest_epoch(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT MAX(last_epoch) AS m FROM sessions").fetchone()
    return float(row["m"]) if row and row["m"] is not None else 0.0


def _session_meta(conn: sqlite3.Connection, since: float | None, project: str | None):
    """Map session_id -> {cost, health, last_epoch} for sessions in the window."""
    where = ["1=1"]
    args: list = []
    if since is not None:
        where.append("last_epoch >= ?")
        args.append(since)
    if project:
        where.append("(project = ? OR project_name = ?)")
        args.extend([project, project])
    rows = conn.execute(
        f"SELECT session_id, cost_usd, health_score, last_epoch "
        f"FROM sessions WHERE {' AND '.join(where)}",
        args,
    ).fetchall()
    return {
        r["session_id"]: {
            "cost": float(r["cost_usd"] or 0.0),
            "health": (int(r["health_score"]) if r["health_score"] is not None else None),
        }
        for r in rows
    }


def _tool_sequences(conn: sqlite3.Connection, session_ids):
    """Ordered tool-name sequence per session (by seq, then id)."""
    seqs: dict = defaultdict(list)
    if not session_ids:
        return seqs
    # One scan, grouped client-side — cheaper than a query per session.
    rows = conn.execute(
        "SELECT session_id, name FROM tool_calls "
        "WHERE name IS NOT NULL AND name != '' "
        "ORDER BY session_id, seq, id"
    ).fetchall()
    wanted = set(session_ids)
    for r in rows:
        if r["session_id"] in wanted:
            seqs[r["session_id"]].append(r["name"])
    return seqs


def extract_chains(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    project: str | None = None,
    limit: int = 10,
    now: float | None = None,
) -> dict:
    """Top tool chains across the window, ranked by frequency.

    Each chain carries: ``tools`` (the sequence), ``count`` (total occurrences),
    ``sessions`` (distinct sessions it appears in), ``avg_cost`` and ``avg_health``
    (mean over those sessions). Returns ``{"chains": [...], "days", "project"}``.
    """
    try:
        days_i = max(1, int(days))
    except (TypeError, ValueError):
        days_i = 30
    try:
        limit_i = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit_i = 10

    newest = now if now is not None else _newest_epoch(conn)
    since = (newest - days_i * 86400) if newest else None
    meta = _session_meta(conn, since, (project or "").strip() or None)
    seqs = _tool_sequences(conn, set(meta))

    counts: Counter = Counter()
    chain_sessions: dict = defaultdict(set)
    for sid, tools in seqs.items():
        for n in range(_MIN_LEN, _MAX_LEN + 1):
            for i in range(len(tools) - n + 1):
                chain = tuple(tools[i:i + n])
                counts[chain] += 1
                chain_sessions[chain].add(sid)

    ranked = sorted(
        counts.items(),
        key=lambda kv: (-kv[1], -len(chain_sessions[kv[0]]), " → ".join(kv[0])),
    )[:limit_i]

    chains = []
    for chain, count in ranked:
        sids = chain_sessions[chain]
        costs = [meta[s]["cost"] for s in sids if s in meta]
        healths = [meta[s]["health"] for s in sids if s in meta and meta[s]["health"] is not None]
        chains.append({
            "tools": list(chain),
            "label": " → ".join(chain),
            "count": count,
            "sessions": len(sids),
            "avg_cost": round(sum(costs) / len(costs), 4) if costs else 0.0,
            "avg_health": round(sum(healths) / len(healths), 1) if healths else None,
        })
    return {"chains": chains, "days": days_i, "project": (project or None)}


def tool_flow(payload: dict) -> dict:
    """Aggregate the top chains into a node/link graph for the Sankey diagram.

    Nodes are ``(column, tool)`` pairs — a tool can appear in more than one
    column. Links are adjacent tool pairs within a chain, weighted by the chain's
    frequency. Returns ``{"columns": [[tool,...]], "links": [{src,dst,weight}]}``.
    """
    col_tools: dict = defaultdict(list)
    col_seen: dict = defaultdict(set)
    node_weight: Counter = Counter()
    links: Counter = Counter()
    for ch in payload.get("chains", []):
        tools = ch["tools"]
        w = ch["count"]
        for col, tool in enumerate(tools):
            key = (col, tool)
            node_weight[key] += w
            if tool not in col_seen[col]:
                col_seen[col].add(tool)
                col_tools[col].append(tool)
            if col + 1 < len(tools):
                links[((col, tool), (col + 1, tools[col + 1]))] += w
    max_col = max(col_tools) if col_tools else -1
    columns = [sorted(col_tools.get(c, []), key=lambda t: -node_weight[(c, t)])
               for c in range(max_col + 1)]
    link_list = [
        {"src_col": s[0], "src": s[1], "dst_col": d[0], "dst": d[1], "weight": w}
        for (s, d), w in sorted(links.items(), key=lambda kv: -kv[1])
    ]
    return {"columns": columns, "links": link_list,
            "node_weight": {f"{c}|{t}": w for (c, t), w in node_weight.items()}}


def chain_svg(payload: dict, *, width: int = 720, row_h: int = 46) -> str:
    """A Sankey-style left-to-right SVG of the top tool chains. Pure xml.etree."""
    flow = tool_flow(payload)
    columns = flow["columns"]
    node_weight = flow["node_weight"]
    ncols = len(columns)
    max_rows = max((len(c) for c in columns), default=0)
    height = max(120, 40 + max_rows * row_h)
    node_w = 132
    gap_x = (width - node_w) / max(1, ncols - 1) if ncols > 1 else 0

    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "viewBox": f"0 0 {width} {height}",
        "width": str(width), "height": str(height),
        "role": "img",
        "aria-label": "Tool chain flow diagram",
    })
    ET.SubElement(svg, "rect", {"x": "0", "y": "0", "width": str(width),
                                "height": str(height), "fill": _BG, "rx": "10"})

    # node positions, keyed by (col, tool)
    pos: dict = {}
    for col, tools in enumerate(columns):
        x = col * gap_x
        for row, tool in enumerate(tools):
            y = 24 + row * row_h
            pos[(col, tool)] = (x, y)

    # links first (under the nodes)
    max_w = max((lk["weight"] for lk in flow["links"]), default=1) or 1
    for lk in flow["links"]:
        s = pos.get((lk["src_col"], lk["src"]))
        d = pos.get((lk["dst_col"], lk["dst"]))
        if not s or not d:
            continue
        x1, y1 = s[0] + node_w, s[1] + 14
        x2, y2 = d[0], d[1] + 14
        mx = (x1 + x2) / 2
        stroke = 1.5 + 6.0 * (lk["weight"] / max_w)
        ET.SubElement(svg, "path", {
            "d": f"M{x1:.1f},{y1:.1f} C{mx:.1f},{y1:.1f} {mx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}",
            "fill": "none", "stroke": _LINK, "stroke-width": f"{stroke:.1f}",
            "stroke-opacity": "0.55", "stroke-linecap": "round",
        })

    # nodes on top
    max_nw = max(node_weight.values(), default=1) or 1
    for (col, tool), (x, y) in pos.items():
        w = node_weight.get(f"{col}|{tool}", 1)
        op = 0.5 + 0.5 * (w / max_nw)
        g = ET.SubElement(svg, "g")
        ET.SubElement(g, "rect", {
            "x": f"{x:.1f}", "y": f"{y:.1f}", "width": str(node_w), "height": "28",
            "rx": "6", "fill": _NODE, "stroke": _BRAND,
            "stroke-opacity": f"{op:.2f}",
        })
        t = ET.SubElement(g, "text", {
            "x": f"{x + node_w / 2:.1f}", "y": f"{y + 18:.1f}",
            "text-anchor": "middle", "fill": _TEXT,
            "font-family": "ui-monospace, monospace", "font-size": "12",
        })
        t.text = tool[:16]
    if not pos:
        t = ET.SubElement(svg, "text", {
            "x": str(width // 2), "y": str(height // 2), "text-anchor": "middle",
            "fill": _MUTED, "font-family": "sans-serif",
            "font-size": "13",
        })
        t.text = "No tool chains in this window yet"
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(svg, encoding="unicode")
