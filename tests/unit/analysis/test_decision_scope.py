"""Unit tests for the derived decision scope level — one test per rule branch."""

from __future__ import annotations

from repowise.core.analysis.decisions.scope import derive_decision_scope


def test_single_file_is_file() -> None:
    assert derive_decision_scope(["a/b.py"], []) == "file"


def test_evidence_file_counts_when_nothing_else_is_linked() -> None:
    assert derive_decision_scope([], [], evidence_file="a/b.py") == "file"


def test_modules_outrank_the_evidence_file_fallback() -> None:
    assert (
        derive_decision_scope([], ["server", "ui", "core"], evidence_file="README.md")
        == "cross-module"
    )
    assert derive_decision_scope([], ["server"], evidence_file="README.md") == "module"


def test_multiple_files_one_module_is_module() -> None:
    assert derive_decision_scope(["a/b.py", "a/c.py"], ["a"]) == "module"


def test_multiple_files_without_modules_infers_from_top_level_dirs() -> None:
    assert derive_decision_scope(["a/b.py", "a/c.py"], []) == "module"
    assert derive_decision_scope(["a/b.py", "z/c.py"], []) == "cross-module"


def test_multiple_root_level_files_without_modules_is_file() -> None:
    assert derive_decision_scope(["README.md", "LICENSE"], []) == "file"


def test_files_spanning_modules_is_cross_module() -> None:
    assert derive_decision_scope(["a/b.py", "z/c.py"], ["a", "z"]) == "cross-module"


def test_only_one_module_is_module() -> None:
    assert derive_decision_scope([], ["a"]) == "module"


def test_only_multiple_modules_is_cross_module() -> None:
    assert derive_decision_scope([], ["a", "z"]) == "cross-module"


def test_nothing_linked_is_none() -> None:
    assert derive_decision_scope([], []) is None
    assert derive_decision_scope(None, None) is None


def test_duplicate_file_entries_collapse_to_one_file() -> None:
    assert derive_decision_scope(["a/b.py", "a/b.py"], []) == "file"
