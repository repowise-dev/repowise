"""The four displayed lists that are cut to a fixed length are ranked first.

Every assertion here is over a population **larger than the cap**, because
that is the only regime where the defect exists: below the cap a list is
complete whatever order it is in, and a test built on three items would pass
against the unranked code it was written to outlaw. The fixtures are
deliberately built so the item that must survive is the one the old
iteration-order cut would have dropped — inserted last, so a first-N-encountered
cut cannot keep it by accident.

The architectural half — "nobody adds a fifth unranked slice" — is
``tests/unit/test_no_unranked_truncation.py``.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.generation.context.graph_intelligence import (
    MAX_CALL_ENTRIES,
    MAX_HERITAGE_ENTRIES,
    build_symbol_index,
    extract_call_graph,
    extract_heritage,
)


def _graph_with_calls(n: int, *, target_file: str = "src/hub.py") -> nx.DiGraph:
    """One file whose symbol is called by *n* others, weakest callers first.

    The single high-confidence caller is added **last**, so it is outside any
    prefix of the insertion order that the cap would have kept.
    """
    graph = nx.DiGraph()
    graph.add_node(
        f"{target_file}::hub",
        node_type="symbol",
        file_path=target_file,
        name="hub",
        kind="function",
    )
    for i in range(n):
        caller = f"src/weak{i:03d}.py::caller{i:03d}"
        graph.add_node(
            caller,
            node_type="symbol",
            file_path=f"src/weak{i:03d}.py",
            name=f"caller{i:03d}",
            kind="function",
        )
        graph.add_edge(caller, f"{target_file}::hub", edge_type="calls", confidence=0.5)
    graph.add_node(
        "src/strong.py::the_real_caller",
        node_type="symbol",
        file_path="src/strong.py",
        name="the_real_caller",
        kind="function",
    )
    graph.add_edge(
        "src/strong.py::the_real_caller",
        f"{target_file}::hub",
        edge_type="calls",
        confidence=0.95,
    )
    return graph


def test_the_most_confident_call_survives_the_cut() -> None:
    graph = _graph_with_calls(MAX_CALL_ENTRIES + 10)

    entries = extract_call_graph("src/hub.py", graph)

    assert len(entries) == MAX_CALL_ENTRIES
    assert "the_real_caller" in {e["caller"] for e in entries}
    assert [e["confidence"] for e in entries] == sorted(
        (e["confidence"] for e in entries), reverse=True
    )


def test_a_call_list_under_the_cap_keeps_every_entry() -> None:
    """Ranking reorders; it must not drop anything that used to fit."""
    graph = _graph_with_calls(3)

    entries = extract_call_graph("src/hub.py", graph)

    assert len(entries) == 4
    assert "the_real_caller" in {e["caller"] for e in entries}


def test_the_call_cut_is_the_same_from_either_extraction_path() -> None:
    graph = _graph_with_calls(MAX_CALL_ENTRIES + 10)

    assert extract_call_graph("src/hub.py", graph, build_symbol_index(graph)) == (
        extract_call_graph("src/hub.py", graph)
    )


def _graph_with_heritage(n: int, *, target_file: str = "src/base.py") -> nx.DiGraph:
    """*n* guessed subclasses of one base, plus one resolved subclass, last."""
    graph = nx.DiGraph()
    graph.add_node(
        f"{target_file}::Base",
        node_type="symbol",
        file_path=target_file,
        name="Base",
        kind="class",
    )
    for i in range(n):
        child = f"src/guess{i:03d}.py::Guess{i:03d}"
        graph.add_node(
            child,
            node_type="symbol",
            file_path=f"src/guess{i:03d}.py",
            name=f"Guess{i:03d}",
            kind="class",
        )
        graph.add_edge(child, f"{target_file}::Base", edge_type="extends", confidence=0.5)
    graph.add_node(
        "src/real.py::ResolvedChild",
        node_type="symbol",
        file_path="src/real.py",
        name="ResolvedChild",
        kind="class",
    )
    graph.add_edge(
        "src/real.py::ResolvedChild",
        f"{target_file}::Base",
        edge_type="extends",
        confidence=0.95,
    )
    return graph


def test_the_resolved_subclass_survives_the_heritage_cut() -> None:
    graph = _graph_with_heritage(MAX_HERITAGE_ENTRIES + 10)

    entries = extract_heritage("src/base.py", graph)

    assert len(entries) == MAX_HERITAGE_ENTRIES
    assert "ResolvedChild" in {e["child"] for e in entries}
    assert [e["confidence"] for e in entries] == sorted(
        (e["confidence"] for e in entries), reverse=True
    )


def test_heritage_entries_carry_the_confidence_they_are_ranked_on() -> None:
    """The caller re-ranks these when it merges files, so the score travels."""
    graph = _graph_with_heritage(2)

    for entry in extract_heritage("src/base.py", graph):
        assert "confidence" in entry


def test_equal_confidence_is_broken_deterministically_not_by_graph_order() -> None:
    """Same edges, opposite insertion order, same answer.

    ``tests/integration/test_generation_determinism.py`` builds a repo twice
    with the graph reversed for this reason; below the page level it is this
    tiebreak that has to hold.
    """
    pairs = [(f"src/m{i}.py::C{i}", f"C{i}") for i in range(MAX_HERITAGE_ENTRIES + 5)]

    def build(order: list[tuple[str, str]]) -> list[dict]:
        graph = nx.DiGraph()
        graph.add_node(
            "src/base.py::Base",
            node_type="symbol",
            file_path="src/base.py",
            name="Base",
            kind="class",
        )
        for node_id, name in order:
            graph.add_node(
                node_id,
                node_type="symbol",
                file_path=node_id.split("::")[0],
                name=name,
                kind="class",
            )
            graph.add_edge(node_id, "src/base.py::Base", edge_type="extends", confidence=0.9)
        return extract_heritage("src/base.py", graph)

    assert build(pairs) == build(list(reversed(pairs)))


@pytest.mark.parametrize("extractor", [extract_call_graph, extract_heritage])
def test_a_file_with_no_symbols_is_still_empty(extractor) -> None:
    assert extractor("src/nothing.py", nx.DiGraph()) == []
