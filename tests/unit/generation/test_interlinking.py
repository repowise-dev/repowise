"""Unit tests for the interlinking post-processor."""

from __future__ import annotations

from repowise.core.generation.interlinking import (
    LinkIndex,
    attach_wiki_links_and_backlinks,
)
from repowise.core.generation.models import GeneratedPage


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


def test_resolve_links_for_file_reference():
    """A backtick-wrapped file path resolves to that file's page_id."""
    pages = [
        _make_page(
            "file_page",
            "src/auth/service.py",
            content="See `src/auth/utils.py` for helper functions.",
        ),
        _make_page("file_page", "src/auth/utils.py", content="utility functions"),
    ]

    attach_wiki_links_and_backlinks(pages, parsed_files=None)

    service_links = pages[0].metadata["wiki_links"]
    assert any(
        link["target_page_id"] == "file_page:src/auth/utils.py"
        for link in service_links
    )

    utils_backlinks = pages[1].metadata["backlinks"]
    assert any(
        bl["source_page_id"] == "file_page:src/auth/service.py"
        for bl in utils_backlinks
    )


def test_dedup_same_target_referenced_multiple_times():
    """Multiple mentions of one target collapse to one link."""
    pages = [
        _make_page(
            "file_page",
            "a.py",
            content="`b.py` does X. Earlier we noted `b.py` and then `b.py`.",
        ),
        _make_page("file_page", "b.py", content=""),
    ]

    attach_wiki_links_and_backlinks(pages)

    assert len(pages[0].metadata["wiki_links"]) == 1


def test_self_reference_excluded():
    """A page referencing itself does not create a self-link."""
    pages = [
        _make_page(
            "file_page",
            "a.py",
            content="This file `a.py` documents itself.",
        ),
    ]

    attach_wiki_links_and_backlinks(pages)

    assert pages[0].metadata["wiki_links"] == []


def test_unresolved_refs_are_dropped_silently():
    """Refs that point to non-existent pages produce no metadata noise."""
    pages = [
        _make_page(
            "file_page",
            "a.py",
            content="See `does/not/exist.py` for context.",
        ),
    ]

    attach_wiki_links_and_backlinks(pages)

    assert pages[0].metadata["wiki_links"] == []
    assert pages[0].metadata["backlinks"] == []


def test_index_resolves_basename_when_unique():
    """`utils.py` alone resolves if there's exactly one matching page."""
    pages = [
        _make_page("file_page", "src/lib/utils.py", content=""),
        _make_page("file_page", "src/app/main.py", content="Calls into `utils.py`."),
    ]

    attach_wiki_links_and_backlinks(pages)

    main_links = pages[1].metadata["wiki_links"]
    assert any(
        link["target_page_id"] == "file_page:src/lib/utils.py" for link in main_links
    )


def test_prior_page_ids_widen_forward_resolution():
    """On an incremental update, refs to pages outside the run's subset
    resolve via the persisted prior ids; backlinks stay run-local."""
    pages = [
        _make_page(
            "file_page",
            "src/app/main.py",
            content="Delegates to `src/lib/utils.py` and reuses `helpers.py`.",
        ),
    ]

    attach_wiki_links_and_backlinks(
        pages,
        prior_page_ids=[
            "file_page:src/lib/utils.py",
            "file_page:src/lib/helpers.py",
            "module_page:community-3",  # not path-shaped, ignored
        ],
    )

    targets = {link["target_page_id"] for link in pages[0].metadata["wiki_links"]}
    assert targets == {"file_page:src/lib/utils.py", "file_page:src/lib/helpers.py"}


def test_current_run_pages_win_over_prior_ids():
    """A page regenerated this run resolves to itself, not a stale prior id."""
    pages = [
        _make_page("file_page", "a.py", content="Uses `b.py`."),
        _make_page("file_page", "b.py", content=""),
    ]

    attach_wiki_links_and_backlinks(pages, prior_page_ids=["file_page:b.py"])

    assert pages[0].metadata["wiki_links"] == [
        {"anchor": "b.py", "target_page_id": "file_page:b.py", "kind": "file"}
    ]
    # b.py is in the run, so its backlink from a.py is materialized.
    assert pages[1].metadata["backlinks"][0]["source_page_id"] == "file_page:a.py"


def test_link_index_build_returns_expected_keys():
    pages = [
        _make_page("file_page", "src/x.py"),
        _make_page("module_page", "community-3"),
    ]
    idx = LinkIndex.build(pages, parsed_files=None)
    assert idx.by_path["src/x.py"] == "file_page:src/x.py"
    assert idx.by_target["community-3"] == "module_page:community-3"
    assert idx.by_basename["x.py"] == "file_page:src/x.py"


def test_backlinks_deterministic_ordering_above_cap():
    """Two runs with the same pages in different input orders produce
    identical backlinks lists (same order and same 25 elements kept)."""
    def _create_pages():
        hub = _make_page("file_page", "hub.py", content="")
        sources = [
            _make_page(
                "file_page",
                f"src/mod_{i:02d}.py",
                content="Calls into `hub.py` for setup.",
            )
            for i in range(30)
        ]
        return hub, sources

    hub1, sources1 = _create_pages()
    pages1 = [hub1, *sources1]
    attach_wiki_links_and_backlinks(pages1)

    hub2, sources2 = _create_pages()
    # Reverse the sources order for the second run
    pages2 = [hub2, *reversed(sources2)]
    attach_wiki_links_and_backlinks(pages2)

    assert len(hub1.metadata["backlinks"]) == 25
    assert len(hub2.metadata["backlinks"]) == 25
    assert hub1.metadata["backlinks"] == hub2.metadata["backlinks"]
    # Verify the kept entries are the first 25 alphabetically
    expected_ids = [f"file_page:src/mod_{i:02d}.py" for i in range(25)]
    actual_ids = [b["source_page_id"] for b in hub1.metadata["backlinks"]]
    assert actual_ids == expected_ids


def test_backlinks_deterministic_ordering_below_cap():
    """Fewer than 25 sources still sort deterministically across different input orders."""
    def _create_pages():
        hub = _make_page("file_page", "hub.py", content="")
        sources = [
            _make_page(
                "file_page",
                f"src/mod_{i:02d}.py",
                content="Calls into `hub.py` for setup.",
            )
            for i in (5, 2, 8, 1, 9)
        ]
        return hub, sources

    hub1, sources1 = _create_pages()
    attach_wiki_links_and_backlinks([hub1, *sources1])

    hub2, sources2 = _create_pages()
    attach_wiki_links_and_backlinks([hub2, *reversed(sources2)])

    assert hub1.metadata["backlinks"] == hub2.metadata["backlinks"]
    actual_ids = [b["source_page_id"] for b in hub1.metadata["backlinks"]]
    assert actual_ids == [
        "file_page:src/mod_01.py",
        "file_page:src/mod_02.py",
        "file_page:src/mod_05.py",
        "file_page:src/mod_08.py",
        "file_page:src/mod_09.py",
    ]

