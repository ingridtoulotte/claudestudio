"""Local HTTP server: JSON API + static SPA.

Local-first and private by design — binds to 127.0.0.1 only, no telemetry, no
outbound calls. Each request opens its own short-lived SQLite connection (SQLite
opens are cheap and this keeps the threaded server free of cross-thread handles).
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import api, index

def _resolve_web_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("CLAUDESTUDIO_WEB", ""),
        os.path.join(here, "web"),                   # packaged inside the package (ships in the wheel)
        os.path.join(os.path.dirname(here), "web"),  # legacy repo-root/web layout
        os.path.join(os.getcwd(), "web"),
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return os.path.join(here, "web")


WEB_DIR = _resolve_web_dir()

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeStudio"
    protocol_version = "HTTP/1.1"

    # injected by make_server
    db_path: str = ""
    projects_root: str | None = None

    def log_message(self, *_):  # silence default stderr access log
        pass

    # -- helpers ------------------------------------------------------------
    def _conn(self):
        return index.connect(self.db_path)

    def _send_json(self, obj, status=200):
        payload = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_bytes(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_download(self, text, content_type, filename, status=200):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if path.startswith("/api/"):
            return self._api_get(path, params)
        return self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        try:
            conn = self._conn()
            try:
                if path == "/api/reindex":
                    stats = index.reindex(conn, self.projects_root, force=body.get("force", False))
                    return self._send_json(stats)
                if path == "/api/saved":
                    return self._send_json(api.add_saved(conn, body))
                if path.startswith("/api/state/"):
                    sid = path[len("/api/state/"):]
                    return self._send_json(api.set_state(conn, sid, body))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - surface as JSON, never 500-crash
            return self._send_json({"error": str(exc)}, status=500)
        return self._send_json({"error": "not found"}, status=404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            conn = self._conn()
            try:
                if path.startswith("/api/saved/"):
                    sid = path[len("/api/saved/"):]
                    return self._send_json(api.delete_saved(conn, sid))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": str(exc)}, status=500)
        return self._send_json({"error": "not found"}, status=404)

    def _api_get(self, path, params):
        try:
            conn = self._conn()
            try:
                if path == "/api/summary":
                    return self._send_json(api.summary(conn))
                if path == "/api/sessions":
                    return self._send_json(api.list_sessions(conn, params))
                if path == "/api/search":
                    return self._send_json(api.search(conn, params))
                if path == "/api/analytics":
                    return self._send_json(api.analytics_payload(conn))
                if path == "/api/projects":
                    return self._send_json(api.projects_payload(conn))
                if path == "/api/wrapped":
                    year = params.get("year")
                    return self._send_json(api.wrapped_payload(conn, int(year) if year else None))
                if path == "/api/compare":
                    return self._send_json(api.compare(conn, params.get("a", ""), params.get("b", "")))
                if path == "/api/saved":
                    return self._send_json({"saved": api.list_saved(conn)})
                if path.startswith("/api/session/") and "/export" in path:
                    rest = path[len("/api/session/"):]
                    sid, _, suffix = rest.partition("/export")
                    fmt = suffix.lstrip(".") or params.get("format", "md")
                    out = api.export_session(conn, sid, fmt)
                    if out is None:
                        return self._send_json({"error": "not found"}, status=404)
                    return self._send_download(out["text"], out["content_type"], out["filename"])
                if path.startswith("/api/session/"):
                    sid = path[len("/api/session/"):]
                    data = api.get_session(conn, sid)
                    if data is None:
                        return self._send_json({"error": "not found"}, status=404)
                    return self._send_json(data)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": str(exc)}, status=500)
        return self._send_json({"error": "not found"}, status=404)

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("/\\").replace("\\", "/")
        full = os.path.join(WEB_DIR, safe)
        # contain within WEB_DIR
        if not os.path.abspath(full).startswith(os.path.abspath(WEB_DIR)):
            return self._send_bytes(b"forbidden", "text/plain", 403)
        if not os.path.isfile(full):
            # SPA fallback
            full = os.path.join(WEB_DIR, "index.html")
            if not os.path.isfile(full):
                return self._send_bytes(b"ClaudeStudio web assets missing", "text/plain", 404)
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            data = fh.read()
        self._send_bytes(data, _CONTENT_TYPES.get(ext, "application/octet-stream"))


def make_server(db_path, projects_root=None, host="127.0.0.1", port=8787):
    Handler.db_path = db_path
    Handler.projects_root = projects_root
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd


def serve(db_path, projects_root=None, host="127.0.0.1", port=8787, open_browser=True):
    # find a free port if the requested one is taken
    import socket
    for candidate in [port, *range(port + 1, port + 25)]:
        try:
            httpd = make_server(db_path, projects_root, host, candidate)
            port = candidate
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"No free port in {port}..{port+25}")

    url = f"http://{host}:{port}/"
    print(f"  ClaudeStudio running at {url}")
    print("  Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: _open_app(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        httpd.server_close()


def _open_app(url):
    """Open in an app-style window when Chrome/Edge is available, else a tab."""
    import shutil
    import subprocess

    chrome_like = []
    if os.name == "nt":
        chrome_like = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(name)
            if p:
                chrome_like.append(p)
    for exe in chrome_like:
        if os.path.isfile(exe) or shutil.which(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--window-size=1440,900"])
                return
            except OSError:
                continue
    webbrowser.open(url)
