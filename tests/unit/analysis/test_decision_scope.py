"""Unit tests for the derived decision scope level — one test per rule branch."""

from __future__ import annotations

from repowise.core.analysis.decisions.scope import commit_scope_files, derive_decision_scope


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


# --- the cap on commit-inherited scope --------------------------------------


def test_a_small_commit_keeps_every_file() -> None:
    files = [f"pkg/m{i}.py" for i in range(5)]
    assert commit_scope_files(files) == sorted(files)


def test_a_large_commit_is_capped() -> None:
    """A decision read out of one commit does not govern a 59-file refactor."""
    assert len(commit_scope_files([f"pkg/m{i:03d}.py" for i in range(59)])) == 20


def test_the_cap_keeps_the_same_files_whatever_order_git_lists_them() -> None:
    """Truncation has to be reproducible, or re-extraction churns the scope."""
    files = [f"pkg/m{i:03d}.py" for i in range(40)]
    assert commit_scope_files(files) == commit_scope_files(list(reversed(files)))


def test_blank_and_duplicate_entries_do_not_consume_the_cap() -> None:
    assert commit_scope_files(["a.py", "a.py", "  ", ""]) == ["a.py"]
    assert commit_scope_files(None) == []


def test_windows_separators_normalise_before_the_cap() -> None:
    assert commit_scope_files([r"pkg\m.py", "pkg/m.py"]) == ["pkg/m.py"]
