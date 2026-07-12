"""Unit tests for the derived decision scope level — one test per rule branch."""

from __future__ import annotations

from repowise.core.analysis.decisions.scope import derive_decision_scope


def test_single_file_with_symbol_is_function() -> None:
    assert derive_decision_scope(["a/b.py"], [], symbol="a/b.py::handler") == "function"


def test_single_file_without_symbol_is_file() -> None:
    assert derive_decision_scope(["a/b.py"], []) == "file"


def test_evidence_file_counts_when_affected_files_empty() -> None:
    assert derive_decision_scope([], [], evidence_file="a/b.py") == "file"


def test_multiple_files_one_module_is_module() -> None:
    assert derive_decision_scope(["a/b.py", "a/c.py"], ["a"]) == "module"


def test_multiple_files_without_modules_is_module() -> None:
    assert derive_decision_scope(["a/b.py", "a/c.py"], []) == "module"


def test_files_spanning_modules_is_cross_module() -> None:
    assert derive_decision_scope(["a/b.py", "z/c.py"], ["a", "z"]) == "cross-module"


def test_only_one_module_is_module() -> None:
    assert derive_decision_scope([], ["a"]) == "module"


def test_only_multiple_modules_is_cross_module() -> None:
    assert derive_decision_scope([], ["a", "z"]) == "cross-module"


def test_nothing_linked_is_cross_module() -> None:
    assert derive_decision_scope([], []) == "cross-module"
    assert derive_decision_scope(None, None) == "cross-module"


def test_duplicate_file_entries_collapse_to_one_file() -> None:
    assert derive_decision_scope(["a/b.py", "a/b.py"], []) == "file"
