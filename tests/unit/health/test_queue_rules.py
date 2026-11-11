"""The shared facet fold both materialized queues count their controls with."""

from __future__ import annotations

from repowise.core.analysis.health.queue_rules import FilterRule, fold_facets
from repowise.core.analysis.health.refactoring.serving import FACETS

_FACETS = (("kind", "kind", "kinds"), ("size", "size", None))
_RULES = (FilterRule("kinds", "kind", "in", "set"), FilterRule("size", "size", "eq", "set"))
_GROUPS = [("a", "S", 2), ("a", None, 1), ("b", "S", 3), (None, "L", 4)]


def test_plain_fold_skips_null_and_sums_counts() -> None:
    assert fold_facets(_GROUPS, _FACETS) == {
        "kind": {"a": 3, "b": 3},
        "size": {"S": 5, "L": 4},
    }


def test_null_label_counts_an_empty_value() -> None:
    folded = fold_facets(_GROUPS, _FACETS, null="none")
    assert folded["kind"] == {"a": 3, "b": 3, "none": 4}
    assert folded["size"] == {"S": 5, "none": 1, "L": 4}


def test_each_facet_is_cross_filtered_by_the_other_selections_only() -> None:
    folded = fold_facets(
        _GROUPS, _FACETS, rules=_RULES, selection={"kinds": frozenset({"a"})}, null="none"
    )
    # The kind control ignores its own selection, so "b" stays choosable.
    assert folded["kind"] == {"a": 3, "b": 3, "none": 4}
    # The size control is narrowed by the kind selection.
    assert folded["size"] == {"S": 2, "none": 1}


def test_without_null_label_an_empty_string_is_its_own_key() -> None:
    folded = fold_facets([("", "S", "high", 1)], FACETS)
    assert folded["lead_type"] == {"": 1}


def test_no_groups_still_returns_every_facet() -> None:
    assert fold_facets([], FACETS) == {name: {} for name, _, _ in FACETS}
