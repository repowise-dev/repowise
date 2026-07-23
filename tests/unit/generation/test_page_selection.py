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
# Test directories do not get a module page
# ---------------------------------------------------------------------------


def _pf(path, is_test):
    from types import SimpleNamespace

    return SimpleNamespace(file_info=SimpleNamespace(path=path, is_test=is_test))


def test_a_test_directory_is_not_a_module():
    from repowise.core.generation.selection.selector import _is_test_group

    assert _is_test_group([_pf("tests/test_a.py", True), _pf("tests/test_b.py", True)])


def test_a_production_module_with_a_colocated_test_keeps_its_page():
    """Go and Jest put the test beside the source; that is still a module."""
    from repowise.core.generation.selection.selector import _is_test_group

    files = [_pf("pkg/a.go", False), _pf("pkg/b.go", False), _pf("pkg/a_test.go", True)]
    assert not _is_test_group(files)


def test_an_even_split_is_not_a_test_group():
    from repowise.core.generation.selection.selector import _is_test_group

    assert not _is_test_group([_pf("pkg/a.py", False), _pf("pkg/a_test.py", True)])


def test_empty_group_is_not_a_test_group():
    from repowise.core.generation.selection.selector import _is_test_group

    assert not _is_test_group([])
