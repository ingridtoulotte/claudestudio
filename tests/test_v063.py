"""Tests for the v0.6.3 "Community & Clarity" features.

These complement the zero-dependency `--selftest`; they add parametrization and
coverage for the new modules. All data comes from `claudestudio.fixtures`.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from claudestudio import (
    github_action,
    index,
    onboarding,
    parser,
    templates,
    tool_chains,
)
from claudestudio import (
    plugin_registry as preg,
)
from claudestudio import (
    search_history as shist,
)

# --- schema v7 -------------------------------------------------------------


def test_schema_is_v7(empty_db):
    assert index.SCHEMA_VERSION == 7
    assert index.stored_schema_version(empty_db) == 7
    cols = {r[1] for r in empty_db.execute("PRAGMA table_info(search_history)")}
    assert {"query", "kind", "project", "result_count", "searched_at"} <= cols


# --- search history --------------------------------------------------------


def test_search_history_record_and_recent(empty_db):
    shist.record_search(empty_db, "alpha", kind="user", result_count=3, now=100)
    shist.record_search(empty_db, "beta", result_count=1, now=200)
    rec = shist.recent(empty_db)
    assert [r["query"] for r in rec] == ["beta", "alpha"]
    assert rec[1]["kind"] == "user"
    assert rec[0]["result_count"] == 1


def test_search_history_blank_ignored(empty_db):
    assert shist.record_search(empty_db, "   ", now=1)["recorded"] is False
    assert shist.recent(empty_db) == []


def test_search_history_prunes_to_cap(empty_db):
    for i in range(shist.MAX_ROWS + 25):
        shist.record_search(empty_db, f"q{i}", now=1000 + i)
    n = empty_db.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
    assert n <= shist.MAX_ROWS


def test_search_history_delete_and_clear(empty_db):
    shist.record_search(empty_db, "x", now=1)
    one = shist.recent(empty_db, 1)[0]
    assert shist.delete_one(empty_db, one["id"])["deleted"] is True
    assert shist.delete_one(empty_db, 999)["deleted"] is False
    shist.record_search(empty_db, "y", now=2)
    assert shist.clear(empty_db)["cleared"] is True
    assert shist.recent(empty_db) == []


def test_search_history_missing_table_is_graceful(empty_db):
    # Simulate an old (un-migrated) index: drop the table, then read.
    empty_db.execute("DROP TABLE search_history")
    empty_db.commit()
    assert shist.recent(empty_db) == []
    assert shist.history_payload(empty_db)["history"] == []
    # clear is also tolerant of the missing table
    assert shist.clear(empty_db)["cleared"] is True


# --- onboarding ------------------------------------------------------------


def test_onboarding_status_shape(populated_db):
    conn, _ = populated_db
    st = onboarding.onboarding_status(conn)
    assert set(st) == {"tour_completed", "hook_installed", "sessions_indexed", "budget_set"}
    assert st["sessions_indexed"] >= 1
    assert st["tour_completed"] is False


def test_tour_steps_and_terminal():
    assert len(onboarding.TOUR_STEPS) == 5
    txt = onboarding.terminal_tour()
    assert "guided tour" in txt.lower()
    assert "?tour=1" in txt


# --- plugin registry -------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://raw.githubusercontent.com/a.py",   # not https
    "https://evil.example.com/a.py",           # off allowlist
    "ftp://raw.githubusercontent.com/a.py",     # bad scheme
])
def test_registry_rejects_bad_urls(url):
    with pytest.raises(preg.RegistryError):
        preg.validate_url(url)


def test_registry_accepts_allowlisted_https():
    u = "https://raw.githubusercontent.com/x/y.py"
    assert preg.validate_url(u) == u


def test_registry_install_verify_remove(tmp_path):
    src = b"def on_session_indexed(db, sid):\n    return None\n"
    reg = {"version": 1, "plugins": [{
        "name": "demo",
        "url": "https://raw.githubusercontent.com/i/c/demo.py",
        "sha256": hashlib.sha256(src).hexdigest(),
        "tags": ["t"], "description": "d",
    }]}
    pdir = os.path.join(str(tmp_path), "plugins")
    # confirm required without --yes / callback
    assert preg.install_plugin("demo", registry=reg, fetcher=lambda u: src,
                               pdir=pdir)["status"] == "confirm_required"
    res = preg.install_plugin("demo", registry=reg, fetcher=lambda u: src,
                              yes=True, pdir=pdir)
    assert res["status"] == "installed" and res["verified"] is True
    assert "demo" in preg.installed_names(pdir)
    # duplicate guard
    assert preg.install_plugin("demo", registry=reg, fetcher=lambda u: src,
                               yes=True, pdir=pdir)["status"] == "already_installed"
    assert preg.remove_plugin("demo", pdir=pdir)["status"] == "removed"


def test_registry_checksum_mismatch_aborts(tmp_path):
    reg = {"version": 1, "plugins": [{
        "name": "bad", "url": "https://raw.githubusercontent.com/i/c/bad.py",
        "sha256": "deadbeef"}]}
    pdir = os.path.join(str(tmp_path), "plugins")
    with pytest.raises(preg.RegistryError):
        preg.install_plugin("bad", registry=reg, fetcher=lambda u: b"x", yes=True, pdir=pdir)
    assert not os.path.isfile(os.path.join(pdir, "bad.py"))


def test_registry_offline_degrades_to_empty():
    def boom(url):
        raise OSError("offline")
    assert preg.list_plugins(fetcher=boom)["plugins"] == []


def test_registry_fetch_rejects_bad_json():
    with pytest.raises(preg.RegistryError):
        preg.fetch_registry(fetcher=lambda u: b'{"no":"plugins"}')


# --- tool chains -----------------------------------------------------------


def test_tool_chains_extract_and_svg(corpus_db):
    conn, _, _ = corpus_db
    out = tool_chains.extract_chains(conn, days=36500, limit=10)
    assert out["chains"]
    top = out["chains"][0]
    assert len(top["tools"]) >= 2
    assert " → " in top["label"]
    # ranked by frequency
    counts = [c["count"] for c in out["chains"]]
    assert counts == sorted(counts, reverse=True)
    svg = tool_chains.chain_svg(out)
    assert svg.startswith("<?xml") and "<svg" in svg and "</svg>" in svg


def test_tool_chains_empty_index(empty_db):
    out = tool_chains.extract_chains(empty_db, days=36500)
    assert out["chains"] == []
    assert "<svg" in tool_chains.chain_svg(out)


# --- templates -------------------------------------------------------------


def test_templates_builtins_present():
    names = {t["name"] for t in templates.list_templates()}
    assert {"refactor", "debug", "new-feature", "review"} <= names


def test_template_render_fills_blanks(populated_db):
    conn, _ = populated_db
    out = templates.render(conn, "refactor", {"file": "a.py", "goal": "speed"})
    assert "a.py" in out["rendered"] and "speed" in out["rendered"]
    assert "{auto-context}" not in out["rendered"]
    assert out["missing"] == []


def test_template_reports_missing(populated_db):
    conn, _ = populated_db
    out = templates.render(conn, "refactor", {"file": "a.py"})
    assert "goal" in out["missing"]
    assert "{goal}" in out["rendered"]


def test_template_create_name_safety(tmp_path):
    udir = str(tmp_path)
    res = templates.create_template("ok", user_dir=udir)
    assert os.path.isfile(res["path"])
    with pytest.raises(ValueError):
        templates.create_template("../evil", user_dir=udir)


# --- github action summary -------------------------------------------------


def test_github_summary_markdown(known):
    md = github_action.summarize_path(known["path"])
    assert md.startswith("### ")
    assert "| Cost |" in md and "| Health |" in md
    assert "Tool success" in md


def test_github_summary_missing_file(tmp_path):
    md = github_action.summarize_path(os.path.join(str(tmp_path), "nope.jsonl"))
    assert "No readable session" in md


def test_github_summary_from_session(known):
    ps = parser.parse_session(known["path"])
    md = github_action.summarize_session(ps)
    assert md.strip().endswith("</sub>")
