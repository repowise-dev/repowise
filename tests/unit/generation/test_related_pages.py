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


def test_pages_that_are_neither_file_nor_module_are_untouched():
    pages = [
        _make_page("onboarding", "onboarding/getting_started"),
        _make_page("symbol_spotlight", "a.py::Thing"),
    ]

    attach_related_pages(pages, import_edges=[])

    assert all("related_pages" not in p.metadata for p in pages)


def _module(target: str, members: list[str]) -> GeneratedPage:
    page = _make_page("module_page", target)
    page.metadata["file_paths"] = members
    return page


class TestModulePages:
    """A module's neighbours are its members' neighbours, lifted and counted."""

    def test_import_edges_between_members_become_module_edges(self):
        pages = [
            _module("src/ingest", ["src/ingest/a.py", "src/ingest/b.py"]),
            _module("src/store", ["src/store/db.py"]),
            _make_page("file_page", "src/ingest/a.py"),
            _make_page("file_page", "src/store/db.py"),
        ]

        attach_related_pages(
            pages, import_edges=[("src/ingest/a.py", "src/store/db.py")]
        )

        ingest = _related(pages[0])
        assert [(r["target_page_id"], r["reason"]) for r in ingest] == [
            ("module_page:src/store", "imports")
        ]
        # The edge is symmetric: the target learns who depends on it.
        assert [(r["target_page_id"], r["reason"]) for r in _related(pages[1])] == [
            ("module_page:src/ingest", "imported-by")
        ]

    def test_weight_counts_the_edges_crossing_the_boundary(self):
        """Two subsystems joined by three edges outrank two joined by one."""
        pages = [
            _module("src/ingest", ["src/ingest/a.py", "src/ingest/b.py"]),
            _module("src/store", ["src/store/db.py", "src/store/q.py"]),
            _module("src/util", ["src/util/u.py"]),
        ]

        attach_related_pages(
            pages,
            import_edges=[
                ("src/ingest/a.py", "src/store/db.py"),
                ("src/ingest/a.py", "src/store/q.py"),
                ("src/ingest/b.py", "src/store/db.py"),
                ("src/ingest/b.py", "src/util/u.py"),
            ],
        )

        ingest = _related(pages[0])
        assert [(r["target_page_id"], r["weight"]) for r in ingest] == [
            ("module_page:src/store", 3.0),
            ("module_page:src/util", 1.0),
        ]

    def test_edges_inside_a_module_are_dropped(self):
        """A module is not related to itself, and self-edges would swamp the rest."""
        pages = [
            _module("src/ingest", ["src/ingest/a.py", "src/ingest/b.py"]),
            _module("src/store", ["src/store/db.py"]),
        ]

        attach_related_pages(
            pages,
            import_edges=[
                ("src/ingest/a.py", "src/ingest/b.py"),
                ("src/ingest/b.py", "src/ingest/a.py"),
                ("src/ingest/a.py", "src/store/db.py"),
            ],
        )

        assert [r["target_page_id"] for r in _related(pages[0])] == [
            "module_page:src/store"
        ]

    def test_same_module_is_never_a_reason_for_a_module(self):
        """It *is* the module; the reason is meaningless at this scale."""
        pages = [
            _module("src/ingest", ["src/ingest/a.py", "src/ingest/b.py"]),
            _module("src/store", ["src/store/db.py"]),
        ]

        attach_related_pages(
            pages,
            import_edges=[("src/ingest/a.py", "src/store/db.py")],
            module_groups=[_Group("src/ingest", ("src/ingest/a.py", "src/ingest/b.py"))],
        )

        assert all(r["reason"] != "same-module" for r in _related(pages[0]))

    def test_co_change_partners_lift_to_their_modules(self):
        pages = [
            _module("src/ingest", ["src/ingest/a.py"]),
            _module("src/store", ["src/store/db.py"]),
        ]
        git_meta = {
            "src/ingest/a.py": {
                "co_change_partners_json": json.dumps(
                    [{"file_path": "src/store/db.py", "co_change_count": 9}]
                )
            }
        }

        attach_related_pages(pages, import_edges=[], git_meta_map=git_meta)

        assert [(r["target_page_id"], r["reason"]) for r in _related(pages[0])] == [
            ("module_page:src/store", "co-changes-with")
        ]

    def test_a_member_owned_by_no_module_is_ignored(self):
        """A file with no documenting page cannot lift an edge anywhere."""
        pages = [_module("src/ingest", ["src/ingest/a.py"])]

        attach_related_pages(pages, import_edges=[("src/ingest/a.py", "vendor/x.py")])

        assert _related(pages[0]) == []

    def test_a_chapter_with_no_files_inherits_its_subtree(self):
        """Otherwise the page whose whole job is orientation has nothing across."""
        pages = [
            _module("src/ingest", []),  # the chapter: all its files are its children's
            _module("src/ingest/lang", ["src/ingest/lang/a.py"]),
            _module("src/ingest/graph", ["src/ingest/graph/b.py"]),
            _module("src/store", ["src/store/db.py"]),
        ]

        attach_related_pages(
            pages,
            import_edges=[
                ("src/ingest/lang/a.py", "src/store/db.py"),
                ("src/ingest/graph/b.py", "src/store/db.py"),
            ],
        )

        assert [(r["target_page_id"], r["weight"]) for r in _related(pages[0])] == [
            ("module_page:src/store", 2.0)
        ]

    def test_an_inherited_neighbour_inside_the_subtree_is_dropped(self):
        """It already links down to that page; repeating it across is not news."""
        pages = [
            _module("src/ingest", []),
            _module("src/ingest/lang", ["src/ingest/lang/a.py"]),
            _module("src/ingest/graph", ["src/ingest/graph/b.py"]),
        ]

        attach_related_pages(
            pages, import_edges=[("src/ingest/lang/a.py", "src/ingest/graph/b.py")]
        )

        assert _related(pages[0]) == []
        # The children still see each other; only the chapter filters them out.
        assert [r["target_page_id"] for r in _related(pages[1])] == [
            "module_page:src/ingest/graph"
        ]

    def test_a_chapter_that_owns_files_keeps_its_own_edges(self):
        """It has real evidence, so it is not given its subtree's second-hand set."""
        pages = [
            _module("src/ingest", ["src/ingest/main.py"]),
            _module("src/ingest/lang", ["src/ingest/lang/a.py"]),
            _module("src/store", ["src/store/db.py"]),
            _module("src/util", ["src/util/u.py"]),
        ]

        attach_related_pages(
            pages,
            import_edges=[
                ("src/ingest/main.py", "src/store/db.py"),
                ("src/ingest/lang/a.py", "src/util/u.py"),
            ],
        )

        assert [r["target_page_id"] for r in _related(pages[0])] == [
            "module_page:src/store"
        ]

    def test_prose_links_still_win(self):
        """Related fills the gaps left by prose, and never duplicates one."""
        pages = [
            _module("src/ingest", ["src/ingest/a.py"]),
            _module("src/store", ["src/store/db.py"]),
        ]
        pages[0].metadata["wiki_links"] = [
            {"anchor": "src/store", "target_page_id": "module_page:src/store", "kind": "file"}
        ]

        attach_related_pages(
            pages, import_edges=[("src/ingest/a.py", "src/store/db.py")]
        )

        assert _related(pages[0]) == []


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


def test_malformed_co_change_records_do_not_raise():
    """The column is untrusted: a bare string, a missing path, and a
    non-numeric weight must each drop their record rather than raise."""
    pages = [_make_page("file_page", "a.py"), _make_page("file_page", "b.py")]
    git_meta = {
        "a.py": {
            "co_change_partners_json": json.dumps(
                [
                    "b.py",
                    {"co_change_count": 5},
                    {"file_path": "b.py", "co_change_count": "many"},
                    {"file_path": "b.py", "co_change_count": 4},
                ]
            )
        }
    }

    attach_related_pages(pages, git_meta_map=git_meta)

    rel = _related(pages[0])
    assert [r["target_page_id"] for r in rel] == ["file_page:b.py"]
    assert rel[0]["weight"] == 4.0
