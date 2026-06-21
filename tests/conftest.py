"""Shared pytest fixtures.

These run *alongside* the zero-dependency `--selftest` (which stays the canonical
gate). pytest adds coverage, parametrization, and GitHub test-summary reporting.
Everything is built from `claudestudio.fixtures`, so no real session data is read.
"""

from __future__ import annotations

import os

import pytest

from claudestudio import fixtures, index


@pytest.fixture()
def known(tmp_path):
    """A tiny, fully-deterministic fixture with hand-computable expectations.

    Returns the dict from `fixtures.build_known` plus the projects `root`.
    """
    root = os.path.join(str(tmp_path), "projects")
    os.makedirs(root, exist_ok=True)
    info = fixtures.build_known(root)
    info["root"] = root
    return info


@pytest.fixture()
def db_path(tmp_path):
    return os.path.join(str(tmp_path), "index.db")


@pytest.fixture()
def empty_db(db_path):
    conn = index.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def populated_db(known, db_path):
    """An index built from the known fixture. Yields (conn, info)."""
    conn = index.connect(db_path)
    index.reindex(conn, known["root"])
    yield conn, known
    conn.close()


@pytest.fixture()
def corpus_db(tmp_path):
    """A larger synthetic corpus. Yields (conn, root, count)."""
    root = os.path.join(str(tmp_path), "corpus")
    fixtures.build_corpus(root, count=10, seed=3)
    db = os.path.join(str(tmp_path), "corpus.db")
    conn = index.connect(db)
    index.reindex(conn, root)
    yield conn, root, 10
    conn.close()
