"""Static-file containment: the web server must never serve a file outside WEB_DIR.

Regression for two traversal tricks the old `startswith` containment let through
on POSIX:
  * a backslash segment survives `normpath` (where '\\' is not a separator) and
    reconstitutes a '../' after the later replace;
  * a sibling directory whose name merely shares a prefix ("web" vs "web_secrets")
    passes a string `startswith` check.
"""

from __future__ import annotations

import os

from claudestudio import server


def _mkweb(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>ok</html>")
    (web / "app.js").write_text("// ok")
    sib = tmp_path / "web_secrets"  # name shares the "web" prefix on purpose
    sib.mkdir()
    (sib / "creds.txt").write_text("SECRET")
    return str(web)


def _escapes(web: str, url_path: str) -> bool:
    """True iff resolving `url_path` lands on a file outside `web`."""
    full = server._resolve_static(web, url_path)
    if full is None:
        return False
    base = os.path.realpath(web)
    try:
        return os.path.commonpath([base, os.path.realpath(full)]) != base
    except ValueError:
        return True


# -- _is_within: platform-independent proof the prefix-sibling flaw is gone ----

def test_is_within_allows_self_and_children(tmp_path):
    base = str(tmp_path / "web")
    os.makedirs(base)
    assert server._is_within(base, base)
    assert server._is_within(base, os.path.join(base, "app.js"))
    assert server._is_within(base, os.path.join(base, "sub", "deep.css"))


def test_is_within_rejects_prefix_sibling(tmp_path):
    base = str(tmp_path / "web")
    os.makedirs(base)
    sibling = str(tmp_path / "web_secrets" / "creds.txt")
    os.makedirs(os.path.dirname(sibling))
    open(sibling, "w").close()
    # the old `abspath(full).startswith(abspath(base))` returned True here
    assert not server._is_within(base, sibling)


# -- _resolve_static: no request path may escape WEB_DIR ----------------------

def test_resolves_normal_file(tmp_path):
    web = _mkweb(tmp_path)
    full = server._resolve_static(web, "/index.html")
    assert full is not None and os.path.basename(full) == "index.html"


def test_no_traversal_escapes(tmp_path):
    web = _mkweb(tmp_path)
    for p in (
        "/../web_secrets/creds.txt",
        "/..\\web_secrets\\creds.txt",
        "/....//web_secrets/creds.txt",
        "/..%5cweb_secrets%5ccreds.txt",
    ):
        assert not _escapes(web, p), p
