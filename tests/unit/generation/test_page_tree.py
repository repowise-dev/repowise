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
        _page("layer_page", "layer:service"),
        _page("layer_page", "layer:ui"),
        _page("module_page", "src/ingest", file_paths=["src/ingest/a.py", "src/ingest/b.py"]),
        _page("module_page", "src/web", file_paths=["src/web/app.tsx"]),
        _page("file_page", "src/ingest/a.py", layer_id="layer:service"),
        _page("file_page", "src/ingest/b.py", layer_id="layer:service"),
        _page("file_page", "src/web/app.tsx", layer_id="layer:ui"),
        _page("file_page", "scripts/tool.py", layer_id="layer:service"),
        _page("symbol_spotlight", "src/ingest/a.py::Parser"),
    ]


# Deliberately the reverse of alphabetical order, so a test that claims the
# spine beats the alphabet actually discriminates between them.
LAYER_ORDER = ["layer:ui", "layer:service"]


def _by_id(pages):
    return {p.page_id: p for p in pages}


class TestShape:
    def test_overview_is_the_root(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        assert _by_id(pages)["repo_overview:demo"].parent_page_id is None

    def test_every_other_page_has_a_parent(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        orphans = [
            p.page_id
            for p in pages
            if p.page_type != "repo_overview" and p.parent_page_id is None
        ]
        assert orphans == []

    def test_every_parent_exists(self):
        """A dangling parent is worse than no parent: it breaks the walk."""
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        known = {p.page_id for p in pages}
        dangling = [
            (p.page_id, p.parent_page_id)
            for p in pages
            if p.parent_page_id is not None and p.parent_page_id not in known
        ]
        assert dangling == []

    def test_file_sits_under_its_nearest_module(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        assert _by_id(pages)["file_page:src/ingest/a.py"].parent_page_id == "module_page:src/ingest"

    def test_module_sits_under_its_dominant_layer(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        assert _by_id(pages)["module_page:src/ingest"].parent_page_id == "layer_page:layer:service"
        assert _by_id(pages)["module_page:src/web"].parent_page_id == "layer_page:layer:ui"

    def test_file_with_no_module_falls_back_to_its_layer(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        assert _by_id(pages)["file_page:scripts/tool.py"].parent_page_id == "layer_page:layer:service"

    def test_spotlight_sits_under_the_file_it_documents(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        spotlight = _by_id(pages)["symbol_spotlight:src/ingest/a.py::Parser"]
        assert spotlight.parent_page_id == "file_page:src/ingest/a.py"

    def test_no_cycles(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        parent = {p.page_id: p.parent_page_id for p in pages}
        for start in parent:
            seen, node = set(), start
            while node is not None:
                assert node not in seen, f"cycle through {start}"
                seen.add(node)
                node = parent.get(node)


class TestOrdering:
    def test_layers_follow_the_spine_not_the_alphabet(self):
        """`layer:service` sorts first alphabetically; the spine puts
        `layer:ui` first, and the spine must win."""
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        by_id = _by_id(pages)
        assert (
            by_id["layer_page:layer:ui"].display_order
            < by_id["layer_page:layer:service"].display_order
        )

    def test_without_a_spine_layers_fall_back_to_a_stable_order(self):
        pages = _wiki()
        assign_page_tree(pages, [])
        by_id = _by_id(pages)
        assert (
            by_id["layer_page:layer:service"].display_order
            < by_id["layer_page:layer:ui"].display_order
        )

    def test_onboarding_follows_the_canonical_reading_order(self):
        """project_overview comes before getting_started in the slot order,
        and after it in the alphabet."""
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        by_id = _by_id(pages)
        assert (
            by_id["onboarding:onboarding/project_overview"].display_order
            < by_id["onboarding:onboarding/getting_started"].display_order
        )

    def test_siblings_are_numbered_from_one_without_gaps(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
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
        assign_page_tree(pages, LAYER_ORDER)
        by_id = _by_id(pages)
        assert by_id["onboarding:onboarding/project_overview"].section_number == "1"
        assert by_id["layer_page:layer:ui"].section_number == "4"
        assert by_id["module_page:src/web"].section_number == "4.1"
        assert by_id["file_page:src/web/app.tsx"].section_number == "4.1.1"

    def test_section_number_reflects_depth(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        by_id = _by_id(pages)
        layer = by_id["layer_page:layer:service"]
        module = by_id["module_page:src/ingest"]
        assert module.section_number.startswith(layer.section_number + ".")
        assert by_id["file_page:src/ingest/a.py"].section_number.startswith(
            module.section_number + "."
        )


class TestStability:
    def test_input_order_does_not_change_the_tree(self):
        """Pages arrive in completion order, which varies between runs."""
        a, b = _wiki(), _wiki()
        random.Random(7).shuffle(b)
        assign_page_tree(a, LAYER_ORDER)
        assign_page_tree(b, LAYER_ORDER)
        placed = lambda ps: {  # noqa: E731
            p.page_id: (p.parent_page_id, p.display_order, p.section_number) for p in ps
        }
        assert placed(a) == placed(b)

    def test_running_it_twice_is_a_no_op(self):
        pages = _wiki()
        assign_page_tree(pages, LAYER_ORDER)
        first = {p.page_id: (p.parent_page_id, p.display_order, p.section_number) for p in pages}
        assign_page_tree(pages, LAYER_ORDER)
        second = {p.page_id: (p.parent_page_id, p.display_order, p.section_number) for p in pages}
        assert first == second


class TestPartialSets:
    """An incremental run holds only the pages it regenerated."""

    def test_a_lone_file_page_is_not_reparented_to_something_arbitrary(self):
        pages = [_page("file_page", "src/ingest/a.py", layer_id="layer:service")]
        # A sentinel, so a placement pass that never ran is distinguishable
        # from one that deliberately assigned nothing.
        pages[0].parent_page_id = "sentinel:not-a-page"
        assign_page_tree(pages, LAYER_ORDER)
        # No module, no layer page and no overview in the set: nothing to
        # point at, so it points at nothing rather than at a wrong parent.
        assert pages[0].parent_page_id is None

    def test_a_partial_set_still_gets_an_order(self):
        pages = [
            _page("file_page", "src/ingest/a.py", layer_id="layer:service"),
            _page("file_page", "src/ingest/b.py", layer_id="layer:service"),
        ]
        assign_page_tree(pages, LAYER_ORDER)
        assert sorted(p.display_order for p in pages) == [1, 2]

    def test_empty_input_is_harmless(self):
        assign_page_tree([], LAYER_ORDER)


class TestDanglingParents:
    """Found on a real 1,775-page wiki, not on the fixture: a spotlight whose
    file never earned a page of its own pointed at a row that did not exist."""

    def test_spotlight_without_a_file_page_falls_back(self):
        pages = [
            _page("repo_overview", "demo"),
            _page("layer_page", "layer:service"),
            _page("module_page", "src/ingest", file_paths=["src/ingest/a.py"]),
            _page("symbol_spotlight", "src/ingest/a.py::Parser"),
        ]
        assign_page_tree(pages, LAYER_ORDER)
        spotlight = _by_id(pages)["symbol_spotlight:src/ingest/a.py::Parser"]
        assert spotlight.parent_page_id == "module_page:src/ingest"
        assert spotlight.parent_page_id in {p.page_id for p in pages}

    def test_spotlight_with_no_home_at_all_lands_on_the_root(self):
        pages = [
            _page("repo_overview", "demo"),
            _page("symbol_spotlight", "vendor/x.py::Thing"),
        ]
        assign_page_tree(pages, LAYER_ORDER)
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
        assign_page_tree(pages, LAYER_ORDER)
        known = {p.page_id for p in pages}
        assert [
            (p.page_id, p.parent_page_id)
            for p in pages
            if p.parent_page_id is not None and p.parent_page_id not in known
        ] == []
