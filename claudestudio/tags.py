"""User-defined session tags & labels (schema v5).

Tags turn ClaudeStudio into a personal knowledge base: label any session
``bug-fix``, ``architecture``, ``blocked``, ``ship-it``, ``revisit`` and filter by
any combination. They are *user state* — stored in the ``available_tags`` (the
palette) and ``session_tags`` (the applications) tables, never wiped by
reindexing, exactly like favorites and notes.

Everything here is a pure SQLite read/write over the local index — no model
calls, no network. Tag names are normalised to lowercase, ≤ 32 chars,
``[a-z0-9_-]`` only, so ``"Bug Fix!"`` and ``"bug-fix"`` collapse to one label.

Usage::

    from claudestudio.tags import TagManager
    t = TagManager.create_tag(conn, "ship-it", "#5ec98a")
    TagManager.tag_session(conn, session_id, t["id"])
    TagManager.list_tags(conn)            # [{id, name, colour, session_count}, …]
"""

from __future__ import annotations

import re
import time
import uuid

DEFAULT_COLOUR = "#9a8cff"  # brand purple
MAX_NAME_LEN = 32

_NAME_STRIP = re.compile(r"[^a-z0-9_-]+")
_HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalise_name(name: str) -> str:
    """Fold a freeform label to the canonical tag form, or '' if nothing remains.

    Lowercased, spaces/punctuation → single hyphen, restricted to ``[a-z0-9_-]``,
    trimmed of leading/trailing separators, capped at 32 chars. ``""`` signals an
    unusable name so the caller can reject it instead of storing a blank tag.
    """
    s = (name or "").strip().lower().replace(" ", "-")
    s = _NAME_STRIP.sub("-", s).strip("-_")
    s = re.sub(r"-{2,}", "-", s)
    return s[:MAX_NAME_LEN].strip("-_")


def normalise_colour(colour: str | None) -> str:
    """Return a valid ``#rrggbb`` colour, falling back to the brand purple."""
    c = (colour or "").strip()
    return c if _HEX_COLOUR.match(c) else DEFAULT_COLOUR


class TagManager:
    """Create, apply, remove, and query user-defined session tags.

    Stateless namespace: every method takes the open index connection as its
    first argument (``TagManager.list_tags(conn)``), so it composes with the
    request-scoped connections the server hands out.
    """

    @staticmethod
    def list_tags(conn) -> list[dict]:
        """All tags with their live session counts, busiest first then by name."""
        rows = conn.execute(
            "SELECT t.id, t.name, t.colour, t.created_at, "
            "       COUNT(st.session_id) AS session_count "
            "FROM available_tags t "
            "LEFT JOIN session_tags st ON st.tag_id = t.id "
            "GROUP BY t.id, t.name, t.colour, t.created_at "
            "ORDER BY session_count DESC, t.name ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def create_tag(conn, name: str, colour: str | None = None) -> dict:
        """Create (or return the existing) tag for `name`. Idempotent by name.

        Names are normalised, so re-creating an existing label returns the same
        row instead of erroring on the UNIQUE constraint. Raises ``ValueError`` if
        the name normalises to nothing.
        """
        norm = normalise_name(name)
        if not norm:
            raise ValueError(f"invalid tag name: {name!r}")
        existing = conn.execute(
            "SELECT id, name, colour, created_at FROM available_tags WHERE name=?",
            (norm,),
        ).fetchone()
        if existing:
            d = dict(existing)
            d["session_count"] = TagManager._count(conn, d["id"])
            return d
        tid = uuid.uuid4().hex
        now = time.time()
        conn.execute(
            "INSERT INTO available_tags(id,name,colour,created_at) VALUES(?,?,?,?)",
            (tid, norm, normalise_colour(colour), now),
        )
        conn.commit()
        return {"id": tid, "name": norm, "colour": normalise_colour(colour),
                "created_at": now, "session_count": 0}

    @staticmethod
    def delete_tag(conn, tag_id: str) -> None:
        """Delete a tag and detach it from every session it labelled."""
        tid = str(tag_id)
        conn.execute("DELETE FROM session_tags WHERE tag_id=?", (tid,))
        conn.execute("DELETE FROM available_tags WHERE id=?", (tid,))
        conn.commit()

    @staticmethod
    def tag_session(conn, session_id: str, tag_id: str) -> None:
        """Apply a tag to a session. Idempotent (re-applying is a no-op)."""
        conn.execute(
            "INSERT OR IGNORE INTO session_tags(session_id,tag_id,created_at) "
            "VALUES(?,?,?)",
            (str(session_id), str(tag_id), time.time()),
        )
        conn.commit()

    @staticmethod
    def untag_session(conn, session_id: str, tag_id: str) -> None:
        """Remove one tag from one session. No error if it was not applied."""
        conn.execute(
            "DELETE FROM session_tags WHERE session_id=? AND tag_id=?",
            (str(session_id), str(tag_id)),
        )
        conn.commit()

    @staticmethod
    def get_session_tags(conn, session_id: str) -> list[dict]:
        """Every tag applied to one session, alphabetically by name."""
        rows = conn.execute(
            "SELECT t.id, t.name, t.colour, t.created_at "
            "FROM session_tags st JOIN available_tags t ON t.id = st.tag_id "
            "WHERE st.session_id=? ORDER BY t.name ASC",
            (str(session_id),),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_sessions_by_tag(conn, tag_id: str, limit: int = 60,
                            offset: int = 0) -> list[dict]:
        """Lightweight session summaries carrying `tag_id`, most recent first.

        `limit`/`offset` paginate; both are clamped to a sane range so a hostile
        ``?limit=-1`` can't turn SQLite's LIMIT unbounded.
        """
        lim = _clamp(limit, 60, lo=1, hi=500)
        off = _clamp(offset, 0, lo=0)
        rows = conn.execute(
            "SELECT s.session_id, s.title, s.project_name, s.last_epoch, "
            "       s.msg_count, s.tool_calls, s.cost_usd, s.health_score "
            "FROM session_tags st JOIN sessions s USING(session_id) "
            "WHERE st.tag_id=? "
            "ORDER BY s.last_epoch DESC, s.session_id ASC LIMIT ? OFFSET ?",
            (str(tag_id), lim, off),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def search_tags(conn, q: str) -> list[dict]:
        """Fuzzy (substring, case-insensitive) match on tag name. Empty q → all."""
        needle = normalise_name(q)
        if not needle:
            return TagManager.list_tags(conn)
        like = "%" + needle.replace("%", r"\%").replace("_", r"\_") + "%"
        rows = conn.execute(
            "SELECT t.id, t.name, t.colour, t.created_at, "
            "       COUNT(st.session_id) AS session_count "
            "FROM available_tags t "
            "LEFT JOIN session_tags st ON st.tag_id = t.id "
            "WHERE t.name LIKE ? ESCAPE '\\' "
            "GROUP BY t.id, t.name, t.colour, t.created_at "
            "ORDER BY session_count DESC, t.name ASC",
            (like,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _count(conn, tag_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) n FROM session_tags WHERE tag_id=?", (str(tag_id),)
        ).fetchone()
        return int(row["n"]) if row else 0


def _clamp(v, default, *, lo=0, hi=None) -> int:
    """Coerce to a bounded int, never raising (mirrors api._int_param)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n
