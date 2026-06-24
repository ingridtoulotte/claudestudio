"""Integration: a real server, real HTTP requests, real security gates."""

from __future__ import annotations

import json
import os
import socket
import threading
import urllib.error
import urllib.request

import pytest

from claudestudio import fixtures, index, server


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("srv")
    root = os.path.join(str(tmp), "projects")
    fixtures.build_corpus(root, count=6, seed=1)
    db = os.path.join(str(tmp), "index.db")
    conn = index.connect(db)
    index.reindex(conn, root)
    conn.close()

    httpd = server.make_server(db, root, "127.0.0.1", 0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", port
    finally:
        httpd.shutdown()
        httpd.server_close()


def _request(url, *, method="GET", headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _first_session_id(base):
    status, _, raw = _request(base + "/api/sessions?limit=1")
    assert status == 200
    return json.loads(raw)["sessions"][0]["session_id"]


def test_summary_endpoint_ok(live):
    base, _ = live
    status, _, raw = _request(base + "/api/summary")
    assert status == 200
    assert json.loads(raw)["sessions"] == 6


def test_security_headers_on_api(live):
    base, _ = live
    _, headers, _ = _request(base + "/api/summary")
    lower = {k.lower(): v for k, v in headers.items()}
    assert "content-security-policy" in lower
    assert lower.get("x-content-type-options") == "nosniff"
    assert lower.get("x-frame-options") == "DENY"


def test_security_headers_on_static(live):
    base, _ = live
    status, headers, body = _request(base + "/")
    assert status == 200 and b"ClaudeStudio" in body
    assert "content-security-policy" in {k.lower() for k in headers}


def test_spoofed_host_rejected(live):
    base, _ = live
    status, _, _ = _request(base + "/api/summary", headers={"Host": "evil.example"})
    assert status == 421


def test_localhost_host_allowed(live):
    base, port = live
    status, _, _ = _request(base + "/api/summary", headers={"Host": f"localhost:{port}"})
    assert status == 200


def test_same_origin_post_allowed(live):
    base, port = live
    sid = _first_session_id(base)
    status, _, raw = _request(
        base + "/api/state/" + sid,
        method="POST",
        headers={"Content-Type": "application/json",
                 "Sec-Fetch-Site": "same-origin"},
        body={"favorite": True},
    )
    assert status == 200
    assert json.loads(raw)["favorite"] is True


def test_cross_site_post_blocked(live):
    base, _ = live
    sid = _first_session_id(base)
    status, _, _ = _request(
        base + "/api/state/" + sid,
        method="POST",
        headers={"Content-Type": "application/json",
                 "Sec-Fetch-Site": "cross-site"},
        body={"favorite": True},
    )
    assert status == 403


def test_cross_origin_post_blocked(live):
    base, _ = live
    sid = _first_session_id(base)
    status, _, _ = _request(
        base + "/api/state/" + sid,
        method="POST",
        headers={"Content-Type": "application/json",
                 "Origin": "http://evil.example"},
        body={"favorite": True},
    )
    assert status == 403


def test_non_browser_post_still_works(live):
    # No Sec-Fetch-Site, no Origin (curl/script): not the CSRF threat model.
    base, _ = live
    sid = _first_session_id(base)
    status, _, _ = _request(
        base + "/api/state/" + sid,
        method="POST",
        headers={"Content-Type": "application/json"},
        body={"archived": False},
    )
    assert status == 200


def _raw_first_line(port, request_bytes, timeout=4):
    """Hand-build a request over a raw socket (urllib won't forge Content-Length).

    Returns the response's first line as text, or None if the connection was
    reset / timed out before any response arrived — the pre-fix failure modes
    (a handler crash resets the socket; a negative length hangs on read()).
    """
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(request_bytes)
        chunk = s.recv(4096)
    except (socket.timeout, ConnectionError, OSError):
        return None
    finally:
        s.close()
    if not chunk:
        return None
    return chunk.split(b"\r\n", 1)[0].decode("latin-1")


def test_non_numeric_content_length_gets_clean_response(live):
    # A malformed Content-Length is parsed before the security gates; it must not
    # crash the handler and reset the socket — the client still gets a real HTTP
    # status line instead of a connection abort.
    _, port = live
    line = _raw_first_line(
        port,
        b"POST /api/state/x HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\nSec-Fetch-Site: same-origin\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: abc\r\n\r\n{}",
    )
    assert line is not None, "server reset the connection instead of responding"
    assert line.startswith("HTTP/1.1"), line


def test_negative_content_length_does_not_hang(live):
    # A negative Content-Length must not send rfile.read() to EOF (a hang on a
    # keep-alive connection); the server responds promptly with a real status.
    _, port = live
    line = _raw_first_line(
        port,
        b"POST /api/state/x HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\nSec-Fetch-Site: same-origin\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: -1\r\n\r\n",
    )
    assert line is not None, "server hung or reset instead of responding"
    assert line.startswith("HTTP/1.1"), line
