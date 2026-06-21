"""CLI export path sanitisation (`cli._safe_out_path`)."""

from __future__ import annotations

import os

import pytest

from claudestudio import cli


def test_none_out_uses_default_name():
    p = cli._safe_out_path(None, "my-session.md")
    assert p.name == "my-session.md"
    assert p.is_absolute()


def test_directory_out_places_default_inside(tmp_path):
    d = tmp_path / "exports"
    d.mkdir()
    p = cli._safe_out_path(str(d), "my-session.md")
    assert p.parent == d.resolve()
    assert p.name == "my-session.md"


def test_explicit_file_out_is_honored(tmp_path):
    target = tmp_path / "report.md"
    p = cli._safe_out_path(str(target), "ignored-default.md")
    assert p == target.resolve()


def test_traversal_segments_are_collapsed(tmp_path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    p = cli._safe_out_path(str(sub / ".." / ".." / "escaped.md"), "x.md")
    assert p == (tmp_path / "escaped.md").resolve()
    assert ".." not in str(p)


def test_separator_in_default_name_is_rejected():
    with pytest.raises(ValueError):
        cli._safe_out_path(None, "evil" + os.sep + "name.md")
