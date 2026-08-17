"""Unit tests for the shared heritage walk.

The rules pinned here are the ones a second copy of this walk would get
wrong: that the anchor identifies a declaration rather than a name, that a
cycle terminates, and that the depth cap bounds expansion without hiding
what a truncated anchor declares itself.
"""

from __future__ import annotations

from repowise.core.ingestion.heritage_resolver import heritage_ancestors


def _walk(edges: dict, root, depth: int = 6) -> set:
    return heritage_ancestors(root, lambda a: edges.get(a, ()), max_expand_depth=depth)


class TestHeritageAncestors:
    def test_the_root_is_included(self) -> None:
        assert _walk({}, "a.py::Solo") == {"a.py::Solo"}

    def test_a_chain_is_followed_to_the_end(self) -> None:
        edges = {"a::A": ["a::B"], "a::B": ["a::C"], "a::C": ["a::D"]}
        assert _walk(edges, "a::A") == {"a::A", "a::B", "a::C", "a::D"}

    def test_a_diamond_yields_each_anchor_once(self) -> None:
        edges = {"a::A": ["a::B", "a::C"], "a::B": ["a::D"], "a::C": ["a::D"]}
        assert _walk(edges, "a::A") == {"a::A", "a::B", "a::C", "a::D"}

    def test_a_cycle_terminates(self) -> None:
        edges = {"a::A": ["a::B"], "a::B": ["a::C"], "a::C": ["a::A"]}
        assert _walk(edges, "a::A") == {"a::A", "a::B", "a::C"}

    def test_self_reference_terminates(self) -> None:
        assert _walk({"a::A": ["a::A"]}, "a::A") == {"a::A"}


class TestDepthCap:
    def test_the_cap_bounds_expansion_not_reach(self) -> None:
        # At depth 0 the root is expanded, so its parents are reached — they
        # are simply not followed. A truncated anchor still counts as found,
        # which is what lets a caller read what it declares itself.
        edges = {"a::A": ["a::B"], "a::B": ["a::C"]}
        assert _walk(edges, "a::A", depth=0) == {"a::A", "a::B"}
        assert _walk(edges, "a::A", depth=1) == {"a::A", "a::B", "a::C"}

    def test_a_deep_chain_stops_at_the_cap(self) -> None:
        edges = {f"a::N{i}": [f"a::N{i + 1}"] for i in range(20)}
        assert _walk(edges, "a::N0", depth=6) == {f"a::N{i}" for i in range(8)}


class TestAnchorIdentity:
    def test_same_named_types_in_different_files_do_not_share_parents(self) -> None:
        # The defect a name-keyed walk has: two unrelated ``Engine`` classes
        # would union their parents and put ``Diesel`` above the electric one.
        edges = {
            "electric.py::Engine": ["electric.py::Motor"],
            "diesel.py::Engine": ["diesel.py::Diesel"],
        }
        assert _walk(edges, "electric.py::Engine") == {
            "electric.py::Engine",
            "electric.py::Motor",
        }

    def test_the_anchor_may_be_any_hashable_identity(self) -> None:
        # The Go structural pass anchors on (package dir, type name), which is
        # already scoped to one declaration.
        edges = {("pkg/a", "Reader"): [("pkg/a", "Seeker")]}
        assert _walk(edges, ("pkg/a", "Reader")) == {
            ("pkg/a", "Reader"),
            ("pkg/a", "Seeker"),
        }
