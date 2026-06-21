"""Integration: a real server, real HTTP requests, real security gates."""

from __future__ import annotations

import json
import os
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
