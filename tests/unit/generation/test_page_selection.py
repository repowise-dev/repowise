"""Unit tests for the PageSelection intent resolver."""

from __future__ import annotations

from repowise.core.generation.page_selection import (
    PageRecord,
    PageSelectionIntent,
    resolve_page_selection,
)


def _records() -> list[PageRecord]:
    return [
        PageRecord("file_page:src/a.py", "file_page", "src/a.py", is_template=True),
        PageRecord("file_page:src/b.py", "file_page", "src/b.py", is_template=False),
        PageRecord(
            "file_page:tests/t.py",
            "file_page",
            "tests/t.py",
            is_template=True,
            freshness_status="stale",
        ),
        PageRecord("module_page:src", "module_page", "src", is_template=True),
        PageRecord("repo_overview:demo", "repo_overview", "demo", is_template=False),
    ]


def test_unwritten_selects_template_pages_only() -> None:
    result = resolve_page_selection(_records(), PageSelectionIntent(unwritten=True))
    assert result.page_ids == {
        "file_page:src/a.py",
        "file_page:tests/t.py",
        "module_page:src",
    }
    assert result.unknown_page_ids == ()


def test_all_pages_selects_everything() -> None:
    result = resolve_page_selection(_records(), PageSelectionIntent(all_pages=True))
    assert len(result) == 5


def test_stale_selects_stale_and_expired() -> None:
    result = resolve_page_selection(_records(), PageSelectionIntent(stale=True))
    assert result.page_ids == {"file_page:tests/t.py"}


def test_path_glob_matches_prefix_and_glob() -> None:
    # A bare directory prefix matches everything under it.
    result = resolve_page_selection(_records(), PageSelectionIntent(path_globs=("src",)))
    assert result.page_ids == {
        "file_page:src/a.py",
        "file_page:src/b.py",
        "module_page:src",
    }
    # An explicit glob works too.
    result = resolve_page_selection(_records(), PageSelectionIntent(path_globs=("src/*.py",)))
    assert result.page_ids == {"file_page:src/a.py", "file_page:src/b.py"}


def test_explicit_ids_report_unknown() -> None:
    intent = PageSelectionIntent(page_ids=("file_page:src/a.py", "file_page:does/not/exist.py"))
    result = resolve_page_selection(_records(), intent)
    assert result.page_ids == {"file_page:src/a.py"}
    assert result.unknown_page_ids == ("file_page:does/not/exist.py",)


def test_selectors_union() -> None:
    intent = PageSelectionIntent(stale=True, page_ids=("file_page:src/b.py",))
    result = resolve_page_selection(_records(), intent)
    assert result.page_ids == {"file_page:tests/t.py", "file_page:src/b.py"}


def test_empty_intent_resolves_empty() -> None:
    intent = PageSelectionIntent()
    assert intent.is_empty()
    assert resolve_page_selection(_records(), intent).page_ids == set()


def test_windows_target_paths_normalize() -> None:
    records = [PageRecord("file_page:src/a.py", "file_page", "src\\a.py", is_template=True)]
    result = resolve_page_selection(records, PageSelectionIntent(path_globs=("src",)))
    assert result.page_ids == {"file_page:src/a.py"}


# ---------------------------------------------------------------------------
# Test files do not enter the concept tree
# ---------------------------------------------------------------------------
#
# The majority-vote group filter that used to live here is gone with the
# per-directory grouping. Test files are now excluded before grouping runs,
# one file at a time, so a production directory with a colocated test keeps
# its page and the test file simply is not a member of it. That is asserted
# against the real selector in
# ``test_selection_concept_groups.py::test_test_files_never_enter_the_concept_tree``.
