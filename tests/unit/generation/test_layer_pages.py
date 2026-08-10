"""Layer pages are retired; the grouping they carried is not.

Every layer page opened with the same sentence, differing in two integers, and
every pair of them scored as the worst duplication in the wiki. What the pages
were actually load-bearing for was structure: the docs tree said "this module
belongs to the Analysis layer" by parenting the module onto the Analysis layer
*page*, so the only way to keep the grouping was to keep the pages.

The grouping now lives on the pages that are grouped — each carries the id and
display name of its layer — so nothing has to emit a page for a reader-facing
tree to build layer rows.

The page type stays registered on purpose. Indexes written before this change
hold ``layer_page`` rows, and a stored page whose type the serving layer does
not know is a broken page for every one of them. New ids are not minted; old
ones still render, and an inbound link to one still resolves.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.cost_estimator.estimator import (
    STRUCTURAL_PAGE_TYPES as COST_STRUCTURAL_PAGE_TYPES,
)
from repowise.core.generation.models import (
    GENERATION_LEVELS,
    STRUCTURALLY_KEYED_PAGE_TYPES,
    PageType,
)
from repowise.core.generation.page_redirects import repo_wide_successor_type
from repowise.core.generation.page_tree import TreeNode, assign_page_tree

_TEMPLATE_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "src"
    / "repowise"
    / "core"
    / "generation"
    / "templates"
)


class TestLayerPageStaysRegistered:
    """A stored layer page has to keep rendering and keep resolving."""

    def test_layer_page_is_still_a_page_type(self):
        assert "layer_page" in PageType.__args__

    def test_layer_page_still_has_a_generation_level(self):
        assert GENERATION_LEVELS["layer_page"] == 5

    def test_layer_page_is_still_structurally_keyed(self):
        assert "layer_page" in STRUCTURALLY_KEYED_PAGE_TYPES

    def test_layer_page_still_costs_nothing(self):
        """A stored layer page was rendered from structure, never prompted.

        Dropping it from the cost table would price historical telemetry as if
        a model had written one.
        """
        assert "layer_page" in COST_STRUCTURAL_PAGE_TYPES

    def test_a_retired_layer_id_hands_off_to_the_overview(self):
        assert repo_wide_successor_type("layer_page:layer:core") == "repo_overview"


class TestNothingEmitsALayerPage:
    def test_the_generator_has_no_layer_page_method(self):
        from repowise.core.generation.page_generator import PageGenerator

        assert not hasattr(PageGenerator, "generate_layer_page")
        assert not hasattr(PageGenerator, "_structural_layer_page")

    def test_there_is_no_layer_page_template(self):
        assert not (_TEMPLATE_DIR / "layer_page.j2").exists()

    def test_there_is_no_layer_page_context(self):
        from repowise.core.generation import context_assembler

        assert not hasattr(context_assembler, "LayerPageContext")

    def test_there_is_no_level_5_builder(self):
        from repowise.core.generation.page_generator import levels

        assert not hasattr(levels, "build_level5_coros")

    def test_a_layer_page_id_is_never_seeded_by_scope_resolution(self):
        """Scope adds unwritten structural pages so a coverage upgrade fills
        the navigation. A page type nothing writes can never be filled, so
        seeding one would ask generation for a page it will not produce."""
        from repowise.core.generation.scope import _STRUCTURAL_PAGE_TYPES

        assert "layer_page" not in _STRUCTURAL_PAGE_TYPES


class TestGroupingSurvivesWithoutTheirPages:
    """The trap: the tree used to derive its layer set from the layer pages."""

    @staticmethod
    def _node(page_id: str, page_type: str, target_path: str, **metadata) -> TreeNode:
        return TreeNode(
            page_id=page_id,
            page_type=page_type,
            target_path=target_path,
            metadata=dict(metadata),
        )

    def _repo(self) -> list[TreeNode]:
        node = self._node
        return [
            node("repo_overview:demo", "repo_overview", "demo"),
            node(
                "file_page:core/analysis/health.py",
                "file_page",
                "core/analysis/health.py",
                layer_id="layer:analysis",
                layer_name="Analysis",
            ),
            node(
                "file_page:core/analysis/risk.py",
                "file_page",
                "core/analysis/risk.py",
                layer_id="layer:analysis",
                layer_name="Analysis",
            ),
            node(
                "module_page:core/analysis",
                "module_page",
                "core/analysis",
                file_paths=["core/analysis/health.py", "core/analysis/risk.py"],
            ),
            node(
                "scc_page:cycle-1",
                "scc_page",
                "cycle-1",
                files=["core/analysis/health.py", "core/analysis/risk.py"],
            ),
        ]

    def test_every_module_is_stamped_when_no_layer_page_exists(self):
        nodes = self._repo()
        assert not any(n.page_type == "layer_page" for n in nodes)
        assign_page_tree(nodes, ["layer:analysis"])
        module = next(n for n in nodes if n.page_id == "module_page:core/analysis")
        assert module.metadata["layer_id"] == "layer:analysis"
        assert module.metadata["layer_name"] == "Analysis"

    def test_every_cycle_is_stamped_when_no_layer_page_exists(self):
        nodes = self._repo()
        assign_page_tree(nodes, ["layer:analysis"])
        cycle = next(n for n in nodes if n.page_id == "scc_page:cycle-1")
        assert cycle.metadata["layer_id"] == "layer:analysis"
        assert cycle.metadata["layer_name"] == "Analysis"

    def test_members_parent_onto_the_overview_not_a_missing_page(self):
        nodes = self._repo()
        assign_page_tree(nodes, ["layer:analysis"])
        for page_id in ("module_page:core/analysis", "scc_page:cycle-1"):
            node = next(n for n in nodes if n.page_id == page_id)
            assert node.parent_page_id == "repo_overview:demo"

    def test_a_stored_layer_page_still_gets_a_place_in_the_tree(self):
        """An index written before the retirement is rebuilt from its store."""
        nodes = [
            *self._repo(),
            self._node("layer_page:layer:analysis", "layer_page", "layer:analysis"),
        ]
        assign_page_tree(nodes, ["layer:analysis"])
        stored = next(n for n in nodes if n.page_id == "layer_page:layer:analysis")
        assert stored.parent_page_id == "repo_overview:demo"
        assert stored.section_number is not None


class TestTheRunCountsWhatGroupingReached:
    """Losing the grouping is now a silent failure unless something counts it.

    With the pages gone, nothing errors when provenance fails to land: the
    tree still builds, every page still has a parent, and the reader simply
    gets a flat wiki. So the run counts the pages that carry a layer against
    the pages that were supposed to, and reports it.
    """

    @staticmethod
    def _page(page_id: str, page_type: str, **metadata):
        from datetime import UTC, datetime

        from repowise.core.generation.models import GeneratedPage

        now = datetime.now(UTC)
        return GeneratedPage(
            page_id=page_id,
            page_type=page_type,
            title=page_id,
            content="",
            source_hash="",
            model_name="",
            provider_name="template",
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            generation_level=0,
            target_path=page_id.split(":", 1)[1],
            created_at=now,
            updated_at=now,
            metadata=dict(metadata),
        )

    def test_counts_stamped_and_unstamped_members(self):
        from repowise.core.generation.page_tree import measure_layer_grouping

        report = measure_layer_grouping(
            [
                self._page("module_page:a", "module_page", layer_id="layer:core"),
                self._page("scc_page:c1", "scc_page", layer_id="layer:core"),
                self._page("module_page:b", "module_page"),
                # Not grouped under a layer row, so not counted either way.
                self._page("file_page:a.py", "file_page", layer_id="layer:core"),
                self._page("repo_overview:demo", "repo_overview"),
            ]
        )
        assert report.grouped == 2
        assert report.ungrouped == 1
        assert report.total == 3

    def test_nothing_to_group_is_not_a_clean_result(self):
        """A run with no groupable page proves nothing about the grouping."""
        from repowise.core.generation.page_tree import measure_layer_grouping

        report = measure_layer_grouping([self._page("repo_overview:demo", "repo_overview")])
        assert report.total == 0
        assert not report.measured
        assert "not computed" in report.summary_line()

    def test_a_blank_layer_id_does_not_count_as_grouped(self):
        from repowise.core.generation.page_tree import measure_layer_grouping

        report = measure_layer_grouping([self._page("module_page:a", "module_page", layer_id="")])
        assert report.grouped == 0
        assert report.ungrouped == 1

    def test_the_generation_report_carries_the_count(self):
        from repowise.core.generation.report import GenerationReport

        report = GenerationReport.from_pages(
            [
                self._page("module_page:a", "module_page", layer_id="layer:core"),
                self._page("module_page:b", "module_page"),
            ]
        )
        assert report.layer_grouping.grouped == 1
        assert report.layer_grouping.ungrouped == 1

    def test_the_rendered_report_always_shows_the_row(self):
        """Hiding it on a zero would make "not measured" look like "all fine"."""
        from rich.console import Console

        from repowise.core.generation.report import GenerationReport, render_report

        console = Console(record=True, width=200)
        render_report(GenerationReport.from_pages([]), console)
        assert "Layer grouping" in console.export_text()
