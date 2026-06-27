"""ascii-report — a compact ASCII table of today's sessions at /api/ascii-report.

A ClaudeStudio community plugin. It registers one extra HTTP route on the local
server, ``GET /api/ascii-report``, returning a plain-text table of the sessions
active today (title, cost, tool calls). Handy for piping into a terminal or a
status bar.

stdlib only · no telemetry · serves only over the existing loopback server.
"""

from __future__ import annotations

import datetime as _dt


def _today_bounds() -> tuple[float, float]:
    now = _dt.datetime.now()
    start = _dt.datetime(now.year, now.month, now.day)
    return start.timestamp(), (start + _dt.timedelta(days=1)).timestamp()


def _render(db) -> str:
    lo, hi = _today_bounds()
    rows = db.execute(
        "SELECT title, cost_usd, tool_calls FROM sessions "
        "WHERE last_epoch >= ? AND last_epoch < ? "
        "ORDER BY last_epoch DESC LIMIT 50",
        (lo, hi),
    ).fetchall()
    if not rows:
        return "No sessions today.\n"
    lines = [f"{'TITLE':<40} {'COST':>9} {'TOOLS':>6}", "-" * 57]
    for r in rows:
        title = (r["title"] or "Untitled")[:40]
        lines.append(f"{title:<40} {float(r['cost_usd'] or 0):>8.4f}$ "
                     f"{int(r['tool_calls'] or 0):>6}")
    return "\n".join(lines) + "\n"


def register_routes(handler_class) -> None:
    """Wrap the server's GET dispatch to add /api/ascii-report (text/plain)."""
    from urllib.parse import urlparse

    from claudestudio import index

    original_do_get = handler_class.do_GET

    def do_GET(self):  # noqa: N802 — matches BaseHTTPRequestHandler
        if urlparse(self.path).path == "/api/ascii-report":
            if hasattr(self, "_host_ok") and not self._host_ok():
                return
            conn = index.connect_ro(self.db_path)
            try:
                body = _render(conn).encode("utf-8")
            finally:
                conn.close()
            self.send_response(200)
            if hasattr(self, "_emit_security_headers"):
                self._emit_security_headers()
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return original_do_get(self)

    handler_class.do_GET = do_GET
