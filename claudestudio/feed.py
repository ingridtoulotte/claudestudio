"""RSS / Atom activity feed (Feature 2.7, v0.6.0).

Your recent sessions, as a standard XML feed. Point any RSS reader, a Slack
bot, or an email client at ``http://127.0.0.1:8787/api/feed.rss`` and get a live
roll of what you've been building — title, summary, date, cost, project. Honours
``?project=``, ``?since=``, ``?limit=`` like the rest of the API.

Built with stdlib ``xml.etree.ElementTree`` (valid, escaped XML — no string
concatenation), so the output round-trips through any parser. Local-first: the
feed is served from your own machine and contains only your own indexed data.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from xml.etree import ElementTree as ET

FEED_TITLE = "ClaudeStudio — recent sessions"
FEED_DESC = "Your recent Claude Code sessions, served locally by ClaudeStudio."
# A localhost self-link; feeds want *a* link, and the data never leaves the box.
FEED_LINK = "http://127.0.0.1:8787/"
_ATOM_NS = "http://www.w3.org/2005/Atom"


def _rfc822(epoch: float) -> str:
    """RFC-822 date for RSS <pubDate>. Falls back to epoch 0 if unrepresentable."""
    try:
        d = _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)
    except (ValueError, OSError, OverflowError, TypeError):
        d = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    return d.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _rfc3339(epoch: float) -> str:
    """RFC-3339 date for Atom <updated>."""
    try:
        d = _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)
    except (ValueError, OSError, OverflowError, TypeError):
        d = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def feed_items(conn: sqlite3.Connection, params: dict | None = None) -> list[dict]:
    """The rows that back the feed, after applying project/since/limit filters."""
    from . import api  # local import: api owns the shared param coercers
    params = params or {}
    where, args = ["last_epoch > 0"], []
    project = (params.get("project") or "").strip()
    if project:
        where.append("(project = ? OR project_name = ?)")
        args += [project, project]
    since = api._as_epoch(params.get("since"))
    if since is not None:
        where.append("last_epoch >= ?")
        args.append(since)
    limit = api._int_param(params.get("limit"), 25, lo=1, hi=200)
    rows = conn.execute(
        f"SELECT session_id, title, project, project_name, preview, "
        f"       last_ts, last_epoch, cost_usd, msg_count, tool_calls, primary_model "
        f"FROM sessions WHERE {' AND '.join(where)} "
        f"ORDER BY last_epoch DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    items = []
    for r in rows:
        title = r["title"] or "Untitled session"
        summary = (
            f"{r['project_name'] or r['project'] or 'project'} · "
            f"{r['msg_count'] or 0} messages · {r['tool_calls'] or 0} tool calls · "
            f"${(r['cost_usd'] or 0.0):.2f}"
        )
        preview = (r["preview"] or "").strip()
        if preview:
            summary += " — " + preview[:200]
        items.append({
            "session_id": r["session_id"],
            "title": title,
            "summary": summary,
            "project": r["project_name"] or r["project"] or "",
            "last_ts": r["last_ts"] or "",
            "last_epoch": r["last_epoch"] or 0.0,
            "cost_usd": r["cost_usd"] or 0.0,
            "link": FEED_LINK + "#/session/" + r["session_id"],
        })
    return items


def build_rss(conn, params: dict | None = None) -> str:
    """A valid RSS 2.0 document for the recent sessions."""
    items = feed_items(conn, params)
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = FEED_TITLE
    ET.SubElement(ch, "link").text = FEED_LINK
    ET.SubElement(ch, "description").text = FEED_DESC
    ET.SubElement(ch, "generator").text = "ClaudeStudio"
    newest = items[0]["last_epoch"] if items else 0.0
    ET.SubElement(ch, "lastBuildDate").text = _rfc822(newest)
    for it in items:
        e = ET.SubElement(ch, "item")
        ET.SubElement(e, "title").text = it["title"]
        ET.SubElement(e, "link").text = it["link"]
        ET.SubElement(e, "description").text = it["summary"]
        ET.SubElement(e, "category").text = it["project"]
        ET.SubElement(e, "pubDate").text = _rfc822(it["last_epoch"])
        guid = ET.SubElement(e, "guid", {"isPermaLink": "false"})
        guid.text = "claudestudio:" + it["session_id"]
    return _serialize(rss)


def build_atom(conn, params: dict | None = None) -> str:
    """A valid Atom 1.0 document for the recent sessions."""
    items = feed_items(conn, params)
    feed = ET.Element("feed", {"xmlns": _ATOM_NS})
    ET.SubElement(feed, "title").text = FEED_TITLE
    ET.SubElement(feed, "subtitle").text = FEED_DESC
    ET.SubElement(feed, "link", {"href": FEED_LINK})
    ET.SubElement(feed, "id").text = "urn:claudestudio:feed"
    newest = items[0]["last_epoch"] if items else 0.0
    ET.SubElement(feed, "updated").text = _rfc3339(newest)
    for it in items:
        e = ET.SubElement(feed, "entry")
        ET.SubElement(e, "title").text = it["title"]
        ET.SubElement(e, "link", {"href": it["link"]})
        ET.SubElement(e, "id").text = "urn:claudestudio:" + it["session_id"]
        ET.SubElement(e, "updated").text = _rfc3339(it["last_epoch"])
        ET.SubElement(e, "summary").text = it["summary"]
        cat = ET.SubElement(e, "category")
        cat.set("term", it["project"])
    return _serialize(feed)


def _serialize(root: ET.Element) -> str:
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )
