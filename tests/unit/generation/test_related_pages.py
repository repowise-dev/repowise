"""Unit tests for the related-pages post-processor."""

from __future__ import annotations

import json
from dataclasses import dataclass

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.related_pages import attach_related_pages


def _make_page(
    page_type: str,
    target_path: str,
    content: str = "",
    *,
    title: str = "",
) -> GeneratedPage:
    return GeneratedPage(
        page_id=f"{page_type}:{target_path}",
        page_type=page_type,
        title=title or f"{page_type} {target_path}",
        content=content,
        source_hash="x",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=0,
        target_path=target_path,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@dataclass(frozen=True)
class _Group:
    key: str
    file_paths: tuple[str, ...]


def _related(page: GeneratedPage) -> list[dict]:
    return page.metadata["related_pages"]


def test_import_edges_connect_pages_without_prose_mentions():
    """A page whose prose never names its imports still gets related entries."""
    pages = [
        _make_page("file_page", "a.py", content="No mentions here."),
        _make_page("file_page", "b.py"),
        _make_page("file_page", "c.py"),
    ]

    attach_related_pages(pages, import_edges=[("a.py", "b.py"), ("c.py", "a.py")])

    rel = _related(pages[0])
    by_reason = {(r["reason"], r["target_page_id"]) for r in rel}
    assert ("imports", "file_page:b.py") in by_reason
    assert ("imported-by", "file_page:c.py") in by_reason


def test_prose_wiki_links_win_dedup():
    """A target already linked from prose is not repeated as related."""
    pages = [
        _make_page("file_page", "a.py"),
        _make_page("file_page", "b.py"),
    ]
    pages[0].metadata["wiki_links"] = [
        {"anchor": "b.py", "target_page_id": "file_page:b.py", "kind": "file"}
    ]

    attach_related_pages(pages, import_edges=[("a.py", "b.py")])

    assert _related(pages[0]) == []


def test_reason_priority_reports_target_once():
    """A target reachable via imports AND co-change appears once, as imports."""
    pages = [
        _make_page("file_page", "a.py"),
        _make_page("file_page", "b.py"),
    ]
    git_meta = {
        "a.py": {
            "co_change_partners_json": json.dumps([{"file_path": "b.py", "co_change_count": 9}])
        }
    }

    attach_related_pages(pages, import_edges=[("a.py", "b.py")], git_meta_map=git_meta)

    rel = _related(pages[0])
    assert len(rel) == 1
    assert rel[0]["reason"] == "imports"


def test_co_change_partners_ordered_by_count():
    pages = [
        _make_page("file_page", "a.py"),
        _make_page("file_page", "b.py"),
        _make_page("file_page", "c.py"),
    ]
    git_meta = {
        "a.py": {
            "co_change_partners_json": json.dumps(
                [
                    {"file_path": "b.py", "co_change_count": 2},
                    {"file_path": "c.py", "co_change_count": 7},
                ]
            )
        }
    }

    attach_related_pages(pages, git_meta_map=git_meta)

    rel = _related(pages[0])
    assert [r["target_page_id"] for r in rel] == [
        "file_page:c.py",
        "file_page:b.py",
    ]
    assert rel[0]["weight"] == 7.0


def test_module_siblings_and_caps():
    """Same-module fills in last and per-reason/total caps hold."""
    member_paths = tuple(f"m/f{i}.py" for i in range(10))
    pages = [_make_page("file_page", p) for p in member_paths]
    groups = [_Group(key="m", file_paths=member_paths)]

    attach_related_pages(pages, module_groups=groups)

    rel = _related(pages[0])
    # Per-reason cap: at most 5 same-module entries despite 9 siblings.
    assert len(rel) == 5
    assert all(r["reason"] == "same-module" for r in rel)


def test_deleted_targets_drop_out():
    """Candidates without a page in this run's set resolve to nothing."""
    pages = [_make_page("file_page", "a.py")]

    attach_related_pages(pages, import_edges=[("a.py", "gone.py")])

    assert _related(pages[0]) == []


def test_non_file_pages_untouched():
    pages = [_make_page("module_page", "community-1")]

    attach_related_pages(pages, import_edges=[])

    assert "related_pages" not in pages[0].metadata


def test_prior_page_ids_widen_resolution_on_incremental_update():
    """Neighbors outside the regenerated subset resolve via persisted ids."""
    pages = [_make_page("file_page", "a.py")]

    attach_related_pages(
        pages,
        import_edges=[("a.py", "b.py")],
        prior_page_ids=["file_page:b.py", "module_page:community-1"],
    )

    rel = _related(pages[0])
    assert [r["target_page_id"] for r in rel] == ["file_page:b.py"]
    # Title falls back to the target path for prior-only pages.
    assert rel[0]["title"] == "b.py"


def test_current_run_page_wins_over_prior_id():
    """A page regenerated this run resolves to itself, not a stale prior id."""
    pages = [
        _make_page("file_page", "a.py"),
        _make_page("file_page", "b.py", title="Fresh B"),
    ]

    attach_related_pages(
        pages,
        import_edges=[("a.py", "b.py")],
        prior_page_ids=["file_page:b.py"],
    )

    rel = _related(pages[0])
    assert rel[0]["title"] == "Fresh B"


def test_bad_co_change_json_tolerated():
    pages = [
        _make_page("file_page", "a.py"),
        _make_page("file_page", "b.py"),
    ]
    git_meta = {"a.py": {"co_change_partners_json": "{not json"}}

    attach_related_pages(pages, import_edges=[("a.py", "b.py")], git_meta_map=git_meta)

    assert [r["reason"] for r in _related(pages[0])] == ["imports"]
