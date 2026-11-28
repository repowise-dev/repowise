"""A range names its head commit's subject and says its fix flag covers the range."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from rich.console import Console

from repowise.cli.commands.risk_cmd import change_render


def _facts(monkeypatch, ref: str, *, is_fix: bool = True) -> str:
    console = Console(record=True, width=200)
    monkeypatch.setattr(change_render, "console", console)
    features = SimpleNamespace(
        ref=ref, subject="fix: the parser", is_fix=is_fix,
        la=10, ld=2, nf=3, nd=1, ns=1, entropy=0.5, exp=40,
    )  # fmt: skip
    result = SimpleNamespace(features=features, request_excludes=(), working_tree=False)
    change_render._print_change_facts(result)
    return console.export_text()


@pytest.mark.parametrize("ref", ["a..b", "a...b"])
def test_range_labels_head_commit_and_range_fix(monkeypatch, ref):
    out = _facts(monkeypatch, ref)

    assert "Head commit: fix: the parser" in out
    assert "(range contains a fix)" in out


def test_single_commit_keeps_plain_subject_and_fix(monkeypatch):
    out = _facts(monkeypatch, "abc123")

    assert "Head commit:" not in out
    assert "fix: the parser" in out
    assert "(fix)" in out
    assert "range contains" not in out


def test_range_without_fix_has_no_tag(monkeypatch):
    out = _facts(monkeypatch, "a..b", is_fix=False)

    assert "Head commit: fix: the parser" in out
    assert "(range contains a fix)" not in out
