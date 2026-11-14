"""A patched C/C++ graph is kept only when no existing file's include reach moved."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx

from repowise.core.pipeline.incremental.include_reach import include_reach_refusal


def _parsed(*paths: str, language: str = "cpp") -> dict[str, SimpleNamespace]:
    return {p: SimpleNamespace(file_info=SimpleNamespace(language=language)) for p in paths}


def _seed(edges: list[tuple[str, str, str]]) -> dict[str, list]:
    nodes = sorted({n for u, v, _ in edges for n in (u, v)})
    return {
        "nodes": [(n, {}) for n in nodes],
        "edges": [(u, v, {"edge_type": kind}) for u, v, kind in edges],
    }


def _graph(edges: list[tuple[str, str, str]]) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_edges_from((u, v, {"edge_type": kind}) for u, v, kind in edges)
    return graph


def test_a_new_include_of_an_already_reached_header_keeps_the_patch() -> None:
    before = [("a.cpp", "b.h", "imports"), ("b.h", "c.h", "imports")]
    after = [*before, ("a.cpp", "c.h", "imports")]

    assert include_reach_refusal(_graph(after), _seed(before), _parsed("a.cpp", "b.h", "c.h")) is None


def test_a_type_use_that_reaches_a_new_header_refuses() -> None:
    before = [("a.cpp", "b.h", "imports")]
    after = [*before, ("b.h", "c.h", "type_use")]

    reason = include_reach_refusal(_graph(after), _seed(before), _parsed("a.cpp", "b.h", "c.h"))

    assert reason == "b.h: the files its includes reach changed"


def test_an_inferred_type_use_carries_no_reach() -> None:
    before = [("a.cpp", "b.h", "imports")]
    graph = _graph(before)
    graph.add_edge("b.h", "c.h", edge_type="type_use", no_scope_widening=True)

    assert include_reach_refusal(graph, _seed(before), _parsed("a.cpp", "b.h", "c.h")) is None


def test_a_deleted_header_that_bridged_to_another_refuses() -> None:
    """a.cpp reached c.h only through r.h; with r.h gone it no longer does."""
    before = [("a.cpp", "r.h", "imports"), ("r.h", "c.h", "imports")]

    reason = include_reach_refusal(_graph([]), _seed(before), _parsed("a.cpp", "c.h"))

    assert reason == "a.cpp: the files its includes reach changed"


def test_an_added_file_has_no_earlier_reach_to_compare() -> None:
    before = [("a.cpp", "b.h", "imports")]
    after = [*before, ("new.cpp", "b.h", "imports")]

    assert include_reach_refusal(_graph(after), _seed(before), _parsed("a.cpp", "b.h", "new.cpp")) is None


def test_other_languages_are_not_compared() -> None:
    before = [("a.py", "b.py", "imports")]
    after = [("a.py", "c.py", "imports")]

    parsed = _parsed("a.py", "b.py", "c.py", language="python")
    assert include_reach_refusal(_graph(after), _seed(before), parsed) is None


def test_a_pass_that_runs_after_import_resolution_carries_no_reach() -> None:
    """A full build reads import targets before it pairs headers with sources."""
    before = [("a.cpp", "b.h", "imports")]
    graph = _graph(before)
    graph.add_edge("b.h", "b.cpp", edge_type="imports", hint_source="header_source_pair")
    graph.add_edge("b.cpp", "c.h", edge_type="imports")

    assert include_reach_refusal(graph, _seed(before), _parsed("a.cpp", "b.h", "b.cpp", "c.h")) is None


def test_an_edge_with_no_type_carries_reach_as_an_import() -> None:
    before = [("a.cpp", "b.h", "imports")]
    graph = _graph(before)
    graph.add_edge("b.h", "c.h")

    reason = include_reach_refusal(graph, _seed(before), _parsed("a.cpp", "b.h", "c.h"))

    assert reason == "b.h: the files its includes reach changed"


def test_every_import_target_edge_type_carries_reach() -> None:
    from repowise.core.ingestion.graph._scoped import IMPORT_TARGET_EDGE_TYPES

    for edge_type in IMPORT_TARGET_EDGE_TYPES:
        before = [("a.cpp", "b.h", "imports")]
        after = [*before, ("b.h", "c.h", edge_type)]

        reason = include_reach_refusal(_graph(after), _seed(before), _parsed("a.cpp", "b.h", "c.h"))

        assert reason == "b.h: the files its includes reach changed", edge_type
