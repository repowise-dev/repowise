"""Tests for merging exclude-pattern sources in repo_config."""
from __future__ import annotations

from repowise.core.repo_config import merge_exclude_patterns


def test_merge_preserves_order_and_dedupes():
    a = ["node_modules", "dist"]
    b = ["dist", ".venv"]
    assert merge_exclude_patterns(a, b) == ["node_modules", "dist", ".venv"]


def test_merge_handles_none_sources():
    assert merge_exclude_patterns(None, ["a"], None) == ["a"]
    assert merge_exclude_patterns(None) == []
    assert merge_exclude_patterns() == []


def test_merge_dedupes_within_one_source():
    assert merge_exclude_patterns(["x", "x", "y"]) == ["x", "y"]


def test_merge_empty_list_is_idempotent():
    assert merge_exclude_patterns([], []) == []


def test_merge_first_source_wins_position():
    # A pattern seen first keeps its earlier position across sources.
    assert merge_exclude_patterns(["z", "a"], ["a", "b"]) == ["z", "a", "b"]
