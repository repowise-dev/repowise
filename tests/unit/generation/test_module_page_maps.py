"""Which module groups get a page."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.generation.page_generator.levels import _is_empty_module


def test_an_empty_module_gets_no_page():
    empty = SimpleNamespace(symbols=[], dependencies=[])
    code = SimpleNamespace(symbols=[{"name": "f"}], dependencies=[])
    barrel = SimpleNamespace(symbols=[], dependencies=["pkg/a.py"])
    assert _is_empty_module([empty], has_parts=False)
    assert not _is_empty_module([empty, code], has_parts=False)
    assert not _is_empty_module([barrel], has_parts=False)
    assert not _is_empty_module([empty], has_parts=True)
