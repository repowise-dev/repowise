"""The wiki tree: shape, ordering, and stability across runs."""

from __future__ import annotations

import random

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_tree import assign_page_tree


def _page(page_type: str, target: str, **metadata) -> GeneratedPage:
    return GeneratedPage(
        page_id=f"{page_type}:{target}",
        page_type=page_type,
        title=target,
        content="body",
        source_hash="h" * 64,
        model_name="m",
        provider_name="template",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=0,
        target_path=target,
        created_at="2026-07-23T00:00:00+00:00",
        updated_at="2026-07-23T00:00:00+00:00",
        metadata=dict(metadata),
    )


def _wiki() -> list[GeneratedPage]:
    """A small wiki with two layers, two modules and files under each."""
    return [
        _page("repo_overview", "demo"),
        _page("onboarding", "onboarding/getting_started"),
        _page("onboarding", "onboarding/project_overview"),
        _page("architecture_diagram", "demo"),
        _page("module_page", "src/ingest", file_paths=["src/ingest/a.py", "src/ingest/b.py"]),
        _page("module_page", "src/web", file_paths=["src/web/app.tsx"]),
        _page("file_page", "src/ingest/a.py", layer_id="layer:service"),
        _page("file_page", "src/ingest/b.py", layer_id="layer:service"),
        _page("file_page", "src/web/app.tsx", layer_id="layer:ui"),
        _page("file_page", "scripts/tool.py", layer_id="layer:service"),
        _page("symbol_spotlight", "src/ingest/a.py::Parser"),
    ]


def _by_id(pages):
    return {p.page_id: p for p in pages}


class TestShape:
    def test_overview_is_the_root(self):
        pages = _wiki()
        assign_page_tree(pages)
        assert _by_id(pages)["repo_overview:demo"].parent_page_id is None

    def test_every_other_page_has_a_parent(self):
        pages = _wiki()
        assign_page_tree(pages)
        orphans = [
            p.page_id
            for p in pages
            if p.page_type != "repo_overview" and p.parent_page_id is None
        ]
        assert orphans == []

    def test_every_parent_exists(self):
        """A dangling parent is worse than no parent: it breaks the walk."""
        pages = _wiki()
        assign_page_tree(pages)
        known = {p.page_id for p in pages}
        dangling = [
            (p.page_id, p.parent_page_id)
            for p in pages
            if p.parent_page_id is not None and p.parent_page_id not in known
        ]
        assert dangling == []

    def test_file_sits_under_its_nearest_module(self):
        pages = _wiki()
        assign_page_tree(pages)
        assert _by_id(pages)["file_page:src/ingest/a.py"].parent_page_id == "module_page:src/ingest"

    def test_module_sits_under_the_overview_stamped_with_its_layer(self):
        pages = _wiki()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert by_id["module_page:src/ingest"].parent_page_id == "repo_overview:demo"
        assert by_id["module_page:src/ingest"].metadata["layer_id"] == "layer:service"
        assert by_id["module_page:src/web"].metadata["layer_id"] == "layer:ui"

    def test_file_with_no_module_falls_back_to_the_overview(self):
        pages = _wiki()
        assign_page_tree(pages)
        assert _by_id(pages)["file_page:scripts/tool.py"].parent_page_id == "repo_overview:demo"

    def test_spotlight_sits_under_the_file_it_documents(self):
        pages = _wiki()
        assign_page_tree(pages)
        spotlight = _by_id(pages)["symbol_spotlight:src/ingest/a.py::Parser"]
        assert spotlight.parent_page_id == "file_page:src/ingest/a.py"

    def test_no_cycles(self):
        pages = _wiki()
        assign_page_tree(pages)
        parent = {p.page_id: p.parent_page_id for p in pages}
        for start in parent:
            seen, node = set(), start
            while node is not None:
                assert node not in seen, f"cycle through {start}"
                seen.add(node)
                node = parent.get(node)


class TestOrdering:
    def test_onboarding_follows_the_canonical_reading_order(self):
        """project_overview comes before getting_started in the slot order,
        and after it in the alphabet."""
        pages = _wiki()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert (
            by_id["onboarding:onboarding/project_overview"].display_order
            < by_id["onboarding:onboarding/getting_started"].display_order
        )

    def test_siblings_are_numbered_from_one_without_gaps(self):
        pages = _wiki()
        assign_page_tree(pages)
        groups: dict[str | None, list[int]] = {}
        for p in pages:
            if p.page_type != "repo_overview":
                groups.setdefault(p.parent_page_id, []).append(p.display_order)
        for orders in groups.values():
            assert sorted(orders) == list(range(1, len(orders) + 1))

    def test_section_numbers_are_the_literal_dotted_path(self):
        """Pinned literals. Prefix-nesting alone survives an off-by-one in the
        numbering, and the dotted number is what a reader is told to cite."""
        pages = _wiki()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert by_id["onboarding:onboarding/project_overview"].section_number == "1"
        assert by_id["module_page:src/web"].section_number == "5"
        # A file page is reference material: placed and ordered, never numbered.
        assert by_id["file_page:src/web/app.tsx"].section_number is None
        assert by_id["file_page:src/web/app.tsx"].display_order == 1

    def test_numbers_go_two_levels_deep_and_no_further(self):
        pages = _wiki()
        assign_page_tree(pages)
        numbers = [p.section_number for p in pages if p.section_number]
        assert numbers
        assert all(n.count(".") <= 1 for n in numbers), numbers


class TestStability:
    def test_input_order_does_not_change_the_tree(self):
        """Pages arrive in completion order, which varies between runs."""
        a, b = _wiki(), _wiki()
        random.Random(7).shuffle(b)
        assign_page_tree(a)
        assign_page_tree(b)
        placed = lambda ps: {  # noqa: E731
            p.page_id: (p.parent_page_id, p.display_order, p.section_number) for p in ps
        }
        assert placed(a) == placed(b)

    def test_running_it_twice_is_a_no_op(self):
        pages = _wiki()
        assign_page_tree(pages)
        first = {p.page_id: (p.parent_page_id, p.display_order, p.section_number) for p in pages}
        assign_page_tree(pages)
        second = {p.page_id: (p.parent_page_id, p.display_order, p.section_number) for p in pages}
        assert first == second


class TestPartialSets:
    """An incremental run holds only the pages it regenerated."""

    def test_a_lone_file_page_is_not_reparented_to_something_arbitrary(self):
        pages = [_page("file_page", "src/ingest/a.py", layer_id="layer:service")]
        # A sentinel, so a placement pass that never ran is distinguishable
        # from one that deliberately assigned nothing.
        pages[0].parent_page_id = "sentinel:not-a-page"
        assign_page_tree(pages)
        # No module, no layer page and no overview in the set: nothing to
        # point at, so it points at nothing rather than at a wrong parent.
        assert pages[0].parent_page_id is None

    def test_a_partial_set_still_gets_an_order(self):
        pages = [
            _page("file_page", "src/ingest/a.py", layer_id="layer:service"),
            _page("file_page", "src/ingest/b.py", layer_id="layer:service"),
        ]
        assign_page_tree(pages)
        assert sorted(p.display_order for p in pages) == [1, 2]

    def test_empty_input_is_harmless(self):
        assign_page_tree([])


class TestDanglingParents:
    """Found on a real 1,775-page wiki, not on the fixture: a spotlight whose
    file never earned a page of its own pointed at a row that did not exist."""

    def test_spotlight_without_a_file_page_falls_back(self):
        pages = [
            _page("repo_overview", "demo"),
            _page("module_page", "src/ingest", file_paths=["src/ingest/a.py"]),
            _page("symbol_spotlight", "src/ingest/a.py::Parser"),
        ]
        assign_page_tree(pages)
        spotlight = _by_id(pages)["symbol_spotlight:src/ingest/a.py::Parser"]
        assert spotlight.parent_page_id == "module_page:src/ingest"
        assert spotlight.parent_page_id in {p.page_id for p in pages}

    def test_spotlight_with_no_home_at_all_lands_on_the_root(self):
        pages = [
            _page("repo_overview", "demo"),
            _page("symbol_spotlight", "vendor/x.py::Thing"),
        ]
        assign_page_tree(pages)
        assert _by_id(pages)["symbol_spotlight:vendor/x.py::Thing"].parent_page_id == (
            "repo_overview:demo"
        )

    def test_no_page_ever_points_outside_the_set(self):
        """The invariant, asserted over a set with several broken references."""
        pages = [
            _page("repo_overview", "demo"),
            _page("symbol_spotlight", "gone.py::A"),
            _page("file_page", "orphan.py", layer_id="layer:nonexistent"),
            _page("scc_page", "scc-abc", files=["gone.py", "orphan.py"]),
        ]
        assign_page_tree(pages)
        known = {p.page_id for p in pages}
        assert [
            (p.page_id, p.parent_page_id)
            for p in pages
            if p.parent_page_id is not None and p.parent_page_id not in known
        ] == []


class TestConceptReadingOrder:
    """Concept pages sort by the order the namer chose, not by the alphabet.

    Sibling order is what ``display_order`` and ``section_number`` are computed
    from, so this is the whole reader-visible payoff of naming the tree. It
    also has to survive the store: every ``update`` rebuilds the tree from
    ``TreeNode``s rehydrated out of ``metadata_json`` rather than from the
    pages a run just produced.
    """

    def _concepts(self, *specs):
        """Concept pages under one layer. Path order is deliberately reversed."""
        pages = [_page("repo_overview", "demo")]
        for target, order in specs:
            meta = {"file_paths": [f"{target}/a.py"], "layer_id": "layer:service"}
            if order is not None:
                meta["concept_order"] = order
            pages.append(_page("module_page", target, **meta))
        return pages

    def _ordered(self, pages):
        concepts = [p for p in pages if p.page_type == "module_page"]
        return [p.target_path for p in sorted(concepts, key=lambda p: p.display_order)]

    def test_the_namers_order_wins_over_the_path(self):
        # src/aaa sorts first alphabetically and is meant to be read last.
        pages = self._concepts(("src/aaa", 2), ("src/mmm", 1), ("src/zzz", 0))
        assign_page_tree(pages)

        assert self._ordered(pages) == ["src/zzz", "src/mmm", "src/aaa"]
        # The fixture is only meaningful if it disagrees with path order.
        assert self._ordered(pages) != sorted(p.target_path for p in pages if p.page_type == "module_page")

    def test_pages_written_before_naming_keep_their_path_order(self):
        """A wiki indexed before this existed carries no order at all."""
        pages = self._concepts(("src/aaa", None), ("src/mmm", None), ("src/zzz", None))
        assign_page_tree(pages)

        assert self._ordered(pages) == ["src/aaa", "src/mmm", "src/zzz"]

    def test_an_unnamed_page_sorts_after_every_named_one(self):
        """Position zero is a real place. An unnamed page must not borrow it."""
        pages = self._concepts(("src/aaa", None), ("src/mmm", 1), ("src/zzz", 0))
        assign_page_tree(pages)

        assert self._ordered(pages) == ["src/zzz", "src/mmm", "src/aaa"]

    def test_the_order_survives_the_store_round_trip(self):
        """``update`` rebuilds from JSON metadata, not from generated pages."""
        import json

        from repowise.core.generation.page_tree import TreeNode

        pages = self._concepts(("src/aaa", 2), ("src/mmm", 1), ("src/zzz", 0))
        nodes = [
            TreeNode(
                page_id=p.page_id,
                page_type=p.page_type,
                target_path=p.target_path,
                # Exactly what page_tree_sync does: store as JSON, read it back.
                metadata=json.loads(json.dumps(p.metadata)),
            )
            for p in pages
        ]
        assign_page_tree(nodes)

        assert self._ordered(nodes) == ["src/zzz", "src/mmm", "src/aaa"]
        assert [n.section_number for n in nodes if n.page_type == "module_page"]


def _wiki_with_rollup() -> list[GeneratedPage]:
    """Two leaf concept pages under a parent-dir rollup that owns no files.

    A rollup overview is a ``module_page`` whose ``target_path`` is a parent
    directory; it carries no ``file_paths`` because its children own them.
    """
    return [
        _page("repo_overview", "demo"),
        _page("module_page", "src"),
        _page("module_page", "src/ingest", file_paths=["src/ingest/a.py", "src/ingest/b.py"]),
        _page("module_page", "src/web", file_paths=["src/web/app.tsx"]),
        _page("file_page", "src/ingest/a.py", layer_id="layer:service"),
        _page("file_page", "src/ingest/b.py", layer_id="layer:service"),
        _page("file_page", "src/web/app.tsx", layer_id="layer:ui"),
    ]


class TestRollupPlacement:
    def test_rollup_borrows_its_children_layer(self):
        """A file-less rollup is stamped with its children's dominant layer."""
        pages = _wiki_with_rollup()
        assign_page_tree(pages)
        rollup = _by_id(pages)["module_page:src"]
        # service wins 2 member files to 1 across the two child modules.
        assert rollup.metadata["layer_id"] == "layer:service"

    def test_rollup_does_not_claim_its_children_files(self):
        """Borrowing members for placement must not re-home the files."""
        pages = _wiki_with_rollup()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert by_id["file_page:src/ingest/a.py"].parent_page_id == "module_page:src/ingest"
        assert by_id["file_page:src/web/app.tsx"].parent_page_id == "module_page:src/web"

    def test_an_unstamped_rollup_leaves_the_tree_flat(self):
        """A page written before chapters shipped must not be guessed at.

        ``src`` heads two module pages here and would qualify as a chapter by
        shape, but it carries no ``is_chapter``. Deriving the rule a second
        time in the tree is how the two records start disagreeing, so an
        un-stamped wiki stays exactly as flat as it was.
        """
        pages = _wiki_with_rollup()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert by_id["module_page:src/ingest"].parent_page_id == "repo_overview:demo"
        assert by_id["module_page:src/web"].parent_page_id == "repo_overview:demo"


def _wiki_with_chapters() -> list[GeneratedPage]:
    """A nested chapter spine: core > analysis > health, plus a loose module.

    ``core`` and ``analysis`` are chapters that also own loose files of their
    own; ``health`` is a chapter with no files. ``tools`` sits under no chapter
    at all, which is the majority case on a real repository.
    """
    return [
        _page("repo_overview", "demo"),
        _page("module_page", "src/core", is_chapter=True, file_paths=["src/core/main.py"]),
        _page(
            "module_page",
            "src/core/analysis",
            is_chapter=True,
            file_paths=["src/core/analysis/run.py"],
        ),
        _page("module_page", "src/core/analysis/health", is_chapter=True),
        _page(
            "module_page",
            "src/core/analysis/health/biomarkers",
            file_paths=["src/core/analysis/health/biomarkers/b.py"],
        ),
        _page(
            "module_page",
            "src/core/analysis/health/perf",
            file_paths=["src/core/analysis/health/perf/p.py"],
        ),
        _page("module_page", "src/core/ingest", file_paths=["src/core/ingest/a.py"]),
        _page("module_page", "tools", file_paths=["tools/t.tsx"]),
        _page("file_page", "src/core/main.py", layer_id="layer:service"),
        _page("file_page", "src/core/analysis/run.py", layer_id="layer:service"),
        _page("file_page", "src/core/analysis/health/biomarkers/b.py", layer_id="layer:service"),
        _page("file_page", "src/core/analysis/health/perf/p.py", layer_id="layer:service"),
        _page("file_page", "src/core/ingest/a.py", layer_id="layer:service"),
        _page("file_page", "tools/t.tsx", layer_id="layer:ui"),
    ]


class TestChapterNesting:
    def test_a_module_hangs_off_its_chapter_not_its_layer(self):
        pages = _wiki_with_chapters()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert (
            by_id["module_page:src/core/ingest"].parent_page_id == "module_page:src/core"
        )

    def test_chapters_nest_nearest_first(self):
        """The spine is a path, not a fan: each chapter sits under the next one up."""
        pages = _wiki_with_chapters()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert (
            by_id["module_page:src/core/analysis/health/biomarkers"].parent_page_id
            == "module_page:src/core/analysis/health"
        )
        assert (
            by_id["module_page:src/core/analysis/health"].parent_page_id
            == "module_page:src/core/analysis"
        )
        assert (
            by_id["module_page:src/core/analysis"].parent_page_id == "module_page:src/core"
        )

    def test_the_top_chapter_falls_back_to_the_overview(self):
        pages = _wiki_with_chapters()
        assign_page_tree(pages)
        assert _by_id(pages)["module_page:src/core"].parent_page_id == "repo_overview:demo"

    def test_a_module_under_no_chapter_sits_under_the_overview(self):
        pages = _wiki_with_chapters()
        assign_page_tree(pages)
        assert _by_id(pages)["module_page:tools"].parent_page_id == "repo_overview:demo"

    def test_a_chapter_that_owns_files_keeps_them(self):
        """Nesting places pages; it must not move a single file's ownership."""
        pages = _wiki_with_chapters()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        assert by_id["file_page:src/core/main.py"].parent_page_id == "module_page:src/core"
        assert (
            by_id["file_page:src/core/analysis/run.py"].parent_page_id
            == "module_page:src/core/analysis"
        )
        assert (
            by_id["file_page:src/core/ingest/a.py"].parent_page_id
            == "module_page:src/core/ingest"
        )

    def test_no_page_is_its_own_ancestor(self):
        """A cycle breaks every walk of the tree, so assert the spine terminates."""
        pages = _wiki_with_chapters()
        assign_page_tree(pages)
        by_id = _by_id(pages)
        for page in pages:
            seen = {page.page_id}
            cursor = page.parent_page_id
            while cursor:
                assert cursor not in seen, f"cycle through {page.page_id}"
                seen.add(cursor)
                cursor = by_id[cursor].parent_page_id if cursor in by_id else None


class TestDirectoryNesting:
    """A module nests under the page for the directory it sits in."""

    def _pages(self):
        return [
            _page("repo_overview", "demo"),
            _page("module_page", "src/pipeline", file_paths=["src/pipeline/run.py"]),
            _page("module_page", "src/pipeline/resume", file_paths=["src/pipeline/resume/r.py"]),
            # Targeted at a parent it holds no files directly in, the way a
            # roll-up of sibling packages is.
            _page("module_page", "pkgs", file_paths=["pkgs/a/x.py", "pkgs/b/y.py"]),
            _page("module_page", "pkgs/big", file_paths=["pkgs/big/z.py"]),
            _page("file_page", "src/pipeline/run.py"),
            _page("file_page", "src/pipeline/resume/r.py"),
            _page("api_contract", "src/pipeline/resume/openapi.py"),
        ]

    def test_a_leaf_that_owns_its_directory_heads_the_modules_beneath_it(self):
        pages = self._pages()
        assign_page_tree(pages, [])
        by_id = _by_id(pages)
        assert (
            by_id["module_page:src/pipeline/resume"].parent_page_id
            == "module_page:src/pipeline"
        )

    def test_a_page_that_only_borrows_a_directory_name_heads_nothing(self):
        pages = self._pages()
        assign_page_tree(pages, [])
        assert _by_id(pages)["module_page:pkgs/big"].parent_page_id == "repo_overview:demo"

    def test_a_contract_sits_under_the_page_for_its_directory(self):
        pages = self._pages()
        # Claimed by a page for another directory, as a concept page that
        # gathered siblings can.
        _by_id(pages)["module_page:pkgs/big"].metadata["file_paths"].append(
            "src/pipeline/resume/openapi.py"
        )
        assign_page_tree(pages, [])
        assert (
            _by_id(pages)["api_contract:src/pipeline/resume/openapi.py"].parent_page_id
            == "module_page:src/pipeline/resume"
        )


class TestReaderOrder:
    """Top-level chapters read foundations first, interfaces after, tooling last."""

    LAYERS = (
        ("apps/web", "layer:ui", 3),
        ("apps/cli", "layer:cli", 2),
        ("scripts", "layer:docs-tooling", 9),
        ("lib/engine", "layer:service", 2),
        ("lib/small", "layer:service", 1),
    )
    # Both apps import the engine, the web app imports the small lib, and the
    # tooling imports everything: its edges must not crown the engine on
    # their own.
    EDGES = (
        ("apps/web/f0.py", "lib/engine/f0.py"),
        ("apps/cli/f0.py", "lib/engine/f1.py"),
        ("apps/web/f1.py", "lib/small/f0.py"),
        ("scripts/f0.py", "apps/web/f0.py"),
    )

    def _pages(self):
        pages = [_page("repo_overview", "demo"), _page("onboarding", "onboarding/getting_started")]
        for target, layer, n in self.LAYERS:
            files = [f"{target}/f{i}.py" for i in range(n)]
            pages.append(_page("module_page", target, file_paths=files, concept_order=0))
            pages += [_page("file_page", f, layer_id=layer) for f in files]
        return pages

    def _top(self, pages):
        top = [p for p in pages if p.page_type == "module_page"]
        return [p.target_path for p in sorted(top, key=lambda p: p.display_order)]

    def test_foundations_come_before_interfaces_and_tooling_last(self):
        pages = self._pages()
        assign_page_tree(pages, list(self.EDGES))
        assert self._top(pages) == ["lib/engine", "lib/small", "apps/cli", "apps/web", "scripts"]

    def test_without_edges_the_layer_bucket_then_the_larger_chapter_leads(self):
        """Engine before interfaces even with no edge to say so; size breaks ties."""
        pages = self._pages()
        assign_page_tree(pages)
        assert self._top(pages) == ["lib/engine", "lib/small", "apps/web", "apps/cli", "scripts"]

    def test_inside_a_section_the_module_a_walkthrough_enters_reads_first(self):
        pages = [
            _page("repo_overview", "demo"),
            _page("module_page", "app", is_chapter=True, file_paths=["app/x.py"]),
            _page("module_page", "app/big", file_paths=[f"app/big/f{i}.py" for i in range(5)]),
            _page("module_page", "app/cmd", file_paths=["app/cmd/main.py"]),
            _page("flow_page", "app/cmd/main.py::main"),
        ]
        assign_page_tree(pages, [])
        by_id = _by_id(pages)
        assert by_id["module_page:app/cmd"].section_number == "1.1"
        assert by_id["module_page:app/big"].section_number == "1.2"

    def test_a_rebuild_without_edges_keeps_the_measured_order(self):
        """``update`` rebuilds from stored metadata and reads no edges."""
        import json

        from repowise.core.generation.page_tree import TreeNode

        pages = self._pages()
        assign_page_tree(pages, list(self.EDGES))
        nodes = [
            TreeNode(
                page_id=p.page_id,
                page_type=p.page_type,
                target_path=p.target_path,
                metadata=json.loads(json.dumps(p.metadata)),
            )
            for p in pages
        ]
        assign_page_tree(nodes)
        assert self._top(nodes) == self._top(pages)

    def test_onboarding_still_leads(self):
        pages = self._pages()
        assign_page_tree(pages, list(self.EDGES))
        assert _by_id(pages)["onboarding:onboarding/getting_started"].section_number == "1"

    def test_a_head_belongs_to_the_layer_of_the_subtree_it_heads(self):
        """A package's config files at its root do not make it a tooling chapter."""
        pages = [
            _page("repo_overview", "demo"),
            _page("module_page", "pkg/ui", is_chapter=True, file_paths=["pkg/ui/vite.config.ts"]),
            _page("module_page", "pkg/ui/views", file_paths=["pkg/ui/views/a.tsx", "pkg/ui/views/b.tsx"]),
            _page("file_page", "pkg/ui/vite.config.ts", layer_id="layer:config"),
            _page("file_page", "pkg/ui/views/a.tsx", layer_id="layer:ui"),
            _page("file_page", "pkg/ui/views/b.tsx", layer_id="layer:ui"),
        ]
        assign_page_tree(pages)
        assert _by_id(pages)["module_page:pkg/ui"].metadata["layer_id"] == "layer:ui"


def test_walkthroughs_read_after_the_sections_as_one_numbered_section():
    pages = [
        _page("repo_overview", "demo"),
        _page("module_page", "src/a", file_paths=["src/a/x.py"]),
        _page("flow_page", "flows/indexing"),
        _page("onboarding", "onboarding/getting_started"),
    ]
    assign_page_tree(pages, [])
    by_id = _by_id(pages)
    assert by_id["flow_page:flows/indexing"].parent_page_id == "repo_overview:demo"
    assert [
        by_id[i].section_number
        for i in ("onboarding:onboarding/getting_started", "flow_page:flows/indexing", "module_page:src/a")
    ] == ["1", "3.1", "2"]


def test_project_activity_closes_the_outline_unnumbered():
    pages = [
        _page("repo_overview", "demo"),
        _page("onboarding", "onboarding/active_landscape"),
        _page("module_page", "src/a", file_paths=["src/a/x.py"]),
        _page("onboarding", "onboarding/getting_started"),
    ]
    assign_page_tree(pages, [])
    by_id = _by_id(pages)
    activity = by_id["onboarding:onboarding/active_landscape"]
    assert activity.section_number is None
    assert activity.display_order == 3
    assert by_id["module_page:src/a"].section_number == "2"


def test_a_chapter_that_is_most_of_its_section_hands_its_parts_to_the_section():
    """"Core > Pkg Core > Ingestion" reads "Core > Ingestion"."""
    inner = "pkg/core/src/core"
    pages = [
        _page("repo_overview", "demo"),
        _page("module_page", "pkg/core", is_chapter=True, file_paths=["pkg/core/setup.py"]),
        _page("module_page", inner, is_chapter=True, file_paths=[f"{inner}/main.py"]),
        _page("module_page", f"{inner}/ingest", file_paths=[f"{inner}/ingest/a{i}.py" for i in range(4)]),
        _page("module_page", f"{inner}/graph", file_paths=[f"{inner}/graph/g{i}.py" for i in range(4)]),
    ]
    pages += [
        _page("file_page", f, layer_id="layer:service")
        for p in pages
        if p.page_type == "module_page"
        for f in p.metadata["file_paths"]
    ]
    assign_page_tree(pages, [])
    by_id = _by_id(pages)
    section = "module_page:pkg/core"
    assert by_id[f"module_page:{inner}/ingest"].parent_page_id == section
    assert by_id[f"module_page:{inner}"].parent_page_id == section
    assert sorted(
        by_id[f"module_page:{p}"].section_number for p in (inner, f"{inner}/ingest", f"{inner}/graph")
    ) == ["1.1", "1.2", "1.3"]


def test_many_small_top_level_packages_read_as_one_supporting_section():
    pages = [_page("repo_overview", "demo")]
    for i in range(16):
        n = 20 - i
        pages.append(_page("module_page", f"p{i:02d}", file_paths=[f"p{i:02d}/f{j}.go" for j in range(n)]))
    assign_page_tree(pages, [])
    numbers = {p.target_path: p.section_number for p in pages if p.page_type == "module_page"}
    # The twelve largest are sections; the rest are numbered inside section 13.
    assert numbers["p00"] == "1" and numbers["p11"] == "12"
    assert [numbers[f"p{i}"] for i in range(12, 16)] == ["13.1", "13.2", "13.3", "13.4"]


def test_tests_and_tooling_give_up_their_section_before_runtime_packages():
    pages = [_page("repo_overview", "demo")]
    for i in range(13):
        pages.append(_page("module_page", f"p{i:02d}", file_paths=[f"p{i:02d}/f.go"]))
        pages.append(_page("file_page", f"p{i:02d}/f.go", layer_id="layer:service"))
    big_docs = [f"docs/d{j}.md" for j in range(30)]
    pages.append(_page("module_page", "docs", file_paths=big_docs))
    pages += [_page("file_page", f, layer_id="layer:docs-tooling") for f in big_docs]
    assign_page_tree(pages, [])
    assert "." in _by_id(pages)["module_page:docs"].section_number


def test_a_page_stamped_as_project_activity_closes_the_outline():
    pages = [
        _page("repo_overview", "demo"),
        _page("onboarding", "onboarding/getting_started"),
        _page("onboarding", "onboarding/recent_work", nav_section="project_activity"),
        _page("module_page", "src/a", file_paths=["src/a/x.py"]),
    ]
    assign_page_tree(pages, [])
    stamped = _by_id(pages)["onboarding:onboarding/recent_work"]
    assert stamped.section_number is None
    assert stamped.display_order == 3


def test_a_part_named_as_its_section_takes_no_number():
    """It folds into the section's row, so the parts after it count from 1."""
    pages = [
        _page("repo_overview", "acme"),
        _page("module_page", "pkg/core", is_chapter=True, file_paths=["pkg/core/setup.py"]),
        _page("module_page", "pkg/core/src/acme/core", is_chapter=True, file_paths=["pkg/core/src/acme/core/m.py"]),
        _page("module_page", "pkg/core/src/acme/core/ingest", file_paths=[f"pkg/core/src/acme/core/ingest/a{i}.py" for i in range(5)]),
        _page("module_page", "pkg/core/src/acme/core/ingest/langs", file_paths=[f"pkg/core/src/acme/core/ingest/langs/l{i}.py" for i in range(5)]),
    ]
    titles = {"module_page:pkg/core": "Core Overview", "module_page:pkg/core/src/acme/core": "Acme Core Overview"}
    for p in pages:
        p.title = titles.get(p.page_id, p.title)
    pages += [
        _page("file_page", f, layer_id="layer:service")
        for p in list(pages)
        if p.page_type == "module_page"
        for f in p.metadata["file_paths"]
    ]
    assign_page_tree(pages, [])
    by_id = _by_id(pages)
    assert by_id["module_page:pkg/core/src/acme/core"].section_number is None
    assert by_id["module_page:pkg/core/src/acme/core/ingest"].section_number == "1.1"


def test_what_a_folded_part_holds_is_numbered_as_the_section_s_parts():
    pages = [
        _page("repo_overview", "acme"),
        _page("module_page", "web", is_chapter=True, file_paths=["web/next.config.js"]),
        _page("module_page", "web/src", is_chapter=True, file_paths=["web/src/i.ts"]),
        _page("module_page", "web/src/hooks", file_paths=["web/src/hooks/h.ts"]),
        _page("module_page", "web/src/views", file_paths=["web/src/views/v.ts"]),
        _page("module_page", "web/pages", file_paths=[f"web/pages/p{i}.ts" for i in range(12)]),
    ]
    for p in pages:
        if p.page_id == "module_page:web/src":
            p.title = "Web Source"
    assign_page_tree(pages, [])
    numbers = sorted(
        p.section_number for p in pages if p.page_type == "module_page" and p.section_number
    )
    # web/src folds into Web; its two parts take numbers beside web/pages.
    assert numbers == ["1", "1.1", "1.2", "1.3"]
