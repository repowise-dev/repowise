"""The declared edge-type vocabulary stays true for its consumers and docs.

The two directions that read the whole source tree (nothing emits an
undeclared type, nothing consumes one no one emits) and the resolution-origin
checks live in ``tests/unit/test_source_tree_guards.py``. This module keeps the
checks that need no source walk: the computed dynamic kinds, the export map,
the code views, the docs, and the removed phantoms.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from repowise.core.ingestion.models import EDGE_TYPE_VALUES


def test_every_dynamic_kind_maps_into_the_vocabulary() -> None:
    """The one computed edge type that matters, pinned to its real transform.

    Mirrors `EdgesMixin.add_dynamic_edges`. If a new `DynamicKind` lands
    without its prefixed form being declared, this fails rather than shipping
    an edge type no consumer can see.
    """
    from typing import get_args

    from repowise.core.ingestion.models import DynamicKind

    for kind in get_args(DynamicKind):
        emitted = kind if kind.startswith("dynamic") else f"dynamic_{kind}"
        assert emitted in EDGE_TYPE_VALUES, (
            f"DynamicKind {kind!r} reaches the graph as {emitted!r}, which EdgeType"
            " does not declare."
        )


def test_edge_type_map_covers_the_vocabulary() -> None:
    """The knowledge-graph export must map every dependency edge type.

    `build_knowledge_graph_skeleton` drops an unmapped type silently, and six
    real ones were missing — framework and the dynamic_* family among them —
    so those relations never reached the graph at all.
    """
    from repowise.core.analysis.knowledge_graph import _EDGE_TYPE_MAP
    from repowise.core.ingestion.models import TEMPORAL_EDGE_TYPES

    # Temporal edges are excluded on purpose; see the comment on _EDGE_TYPE_MAP.
    # `documents` is exported as its own relation, so only temporal is left out.
    expected = EDGE_TYPE_VALUES - TEMPORAL_EDGE_TYPES
    assert not (expected - _EDGE_TYPE_MAP.keys()), (
        "edge type(s) silently dropped from the knowledge-graph export: "
        f"{sorted(expected - _EDGE_TYPE_MAP.keys())}"
    )
    assert not (_EDGE_TYPE_MAP.keys() - EDGE_TYPE_VALUES), (
        f"export mapping keyed on a type nothing emits: {sorted(_EDGE_TYPE_MAP.keys() - EDGE_TYPE_VALUES)}"
    )
    assert not (_EDGE_TYPE_MAP.keys() & TEMPORAL_EDGE_TYPES), (
        "a temporal edge reached the dependency export — that is how a co-change"
        " partner starts looking like an import"
    )


def test_non_code_edges_stay_out_of_code_views() -> None:
    """No code view may hold a history or prose edge."""
    from repowise.core.ingestion import models as m

    for code_view in (
        m.FILE_DEPENDENCY_EDGE_TYPES,
        m.SYMBOL_USE_EDGE_TYPES,
        m.EXECUTION_EDGE_TYPES,
        m.REACHABILITY_USE_EDGE_TYPES,
    ):
        assert not (code_view & m.NON_CODE_EDGE_TYPES)


def test_graph_docs_origin_badge_matches_vocabulary() -> None:
    from repowise.core.ingestion.models import RESOLUTION_ORIGIN_VALUES

    root = pathlib.Path(__file__).resolve().parents[3]
    graph_doc = (root / "docs/layers/GRAPH.md").read_text(encoding="utf-8")
    match = re.search(r"badge/(\d+)-resolution_origins-", graph_doc)

    assert match is not None, "GRAPH.md resolution-origin badge is missing"
    assert int(match.group(1)) == len(RESOLUTION_ORIGIN_VALUES)


def test_language_support_origin_table_matches_vocabulary() -> None:
    from repowise.core.ingestion.models import RESOLUTION_ORIGIN_VALUES

    root = pathlib.Path(__file__).resolve().parents[3]
    text = (root / "docs/layers/LANGUAGE_SUPPORT.md").read_text(encoding="utf-8")
    section = text.split("### Every call edge says how it was resolved", 1)[1]
    table, remainder = section.split("That is the complete vocabulary of ", 1)
    names = re.findall(r"^\| `([a-z_]+)` \|", table, re.MULTILINE)

    assert len(names) == len(set(names)), "origin table has duplicate rows"
    assert set(names) == RESOLUTION_ORIGIN_VALUES
    assert int(remainder.split(".", 1)[0]) == len(RESOLUTION_ORIGIN_VALUES)


@pytest.mark.parametrize("phantom", ["has_property", "method_overrides", "dynamic"])
def test_the_removed_phantoms_stay_removed(phantom: str) -> None:
    """Each measured at 0 rows across 42 local indexes with no producer in the tree.

    Named individually so a re-add has to argue with the specific string rather
    than a count.
    """
    assert phantom not in EDGE_TYPE_VALUES
