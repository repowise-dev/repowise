"""Unit tests for c4_builder.labels — relation verbs + coupling buckets."""

from __future__ import annotations

import pytest

from repowise.server.services.c4_builder.labels import coupling_strength, relation_label


@pytest.mark.parametrize(
    "edge_types,expected",
    [
        (("imports",), "imports"),
        (("co_changes",), "co-changes"),
        # A concrete code dependency outranks a historical co-change signal.
        (("co_changes", "imports"), "imports"),
        # Synonyms fold into one verb, not "imports +1".
        (("dynamic_imports", "imports"), "imports"),
        (("dynamic_imports",), "imports"),
        (("calls", "imports"), "calls"),
        # Tokens the extractors really emit must all be named. These three were
        # missing, so a lazy import, a conftest link and a subclass all reported
        # as "depends on" — the label reserved for a wholly-unknown set.
        (("dynamic_uses",), "uses"),
        (("framework",), "uses"),
        (("extends",), "inherits from"),
        # A static import describes the same dependency more precisely.
        (("framework", "imports"), "imports"),
        # ...but a subclass relationship outranks it, as `calls` does.
        (("extends", "imports"), "inherits from"),
        # Empty / unknown never yields a bare token or empty string.
        ((), "depends on"),
        (("totally_unknown",), "depends on"),
    ],
)
def test_relation_label(edge_types, expected):
    assert relation_label(edge_types) == expected


def test_relation_label_accepts_any_iterable_order():
    assert relation_label({"imports", "co_changes"}) == "imports"


@pytest.mark.parametrize(
    "count,expected",
    [
        (1, "loose"),
        (9, "loose"),
        (10, "moderate"),
        (49, "moderate"),
        (50, "tight"),
        (666, "tight"),
    ],
)
def test_coupling_strength(count, expected):
    assert coupling_strength(count) == expected
