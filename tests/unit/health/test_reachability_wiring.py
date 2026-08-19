"""The health engine reads ``reached_by_tests`` off the real graph.

The helper walks are unit-tested in ``tests/unit/analysis``; what this pins is
the wiring, because the walk can be right while the engine still hands the
biomarker ``False``. Runs the real analyzer over a real parsed tree with a real
graph, and asserts on the finding the biomarker actually emits.
"""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.traverser import FileTraverser

# Enough churn and centrality for untested_hotspot's other two gates to pass, so
# the only thing left deciding the finding is the test signal.
_HOTSPOT_META = {"is_hotspot": True, "commit_count_90d": 30, "commit_count_total": 30}


def _tree(tmp_path):
    """A source file whose only test is named for behaviour, not for the file."""
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "parser.py").write_text("def parse(s):\n    return s\n", encoding="utf-8")
    (tmp_path / "tests" / "test_round_trips.py").write_text(
        "from src.parser import parse\n\n\ndef test_it():\n    assert parse('a') == 'a'\n",
        encoding="utf-8",
    )
    return [
        parse_file(f, (tmp_path / f.path).read_bytes()) for f in FileTraverser(tmp_path).traverse()
    ]


def _graph(*edges):
    g = nx.DiGraph()
    for src, dst in edges:
        g.add_edge(src, dst, edge_type="imports")
    # Dependents for the centrality gate; untested_hotspot wants at least four.
    for i in range(6):
        g.add_edge(f"src/consumer_{i}.py", "src/parser.py", edge_type="imports")
    return g


def _findings(parsed, graph):
    report = HealthAnalyzer(
        graph,
        parsed_files=parsed,
        git_meta_map={"src/parser.py": dict(_HOTSPOT_META)},
    ).analyze()
    return [f for f in report.findings if f.biomarker_type == "untested_hotspot"]


def test_a_test_importing_the_file_clears_the_untested_hotspot(tmp_path):
    parsed = _tree(tmp_path)
    graph = _graph(("tests/test_round_trips.py", "src/parser.py"))
    assert [f for f in _findings(parsed, graph) if f.file_path == "src/parser.py"] == []


def test_without_that_edge_the_same_tree_still_fires(tmp_path):
    """The control: the finding is suppressed by the edge, not by the fixture."""
    parsed = _tree(tmp_path)
    fired = _findings(parsed, _graph())
    assert [f.file_path for f in fired if f.file_path == "src/parser.py"] == ["src/parser.py"]


@pytest.mark.parametrize("edge_type", ["co_changes", "defines"])
def test_a_non_dependency_edge_does_not_clear_it(tmp_path, edge_type):
    """Changing together, or containment, is not testing."""
    parsed = _tree(tmp_path)
    graph = _graph()
    graph.add_edge("tests/test_round_trips.py", "src/parser.py", edge_type=edge_type)
    fired = [f.file_path for f in _findings(parsed, graph) if f.file_path == "src/parser.py"]
    assert fired == ["src/parser.py"]


def test_no_graph_is_no_signal_not_a_crash(tmp_path):
    """The engine runs graphless on some paths; the walk must degrade, not raise.

    Asserting on the finding would prove nothing here: without a graph there is
    no dependents count either, so untested_hotspot's centrality gate closes
    before the test signal is ever consulted. What is worth pinning is that the
    pass completes and still scores the file.
    """
    report = HealthAnalyzer(
        None,
        parsed_files=_tree(tmp_path),
        git_meta_map={"src/parser.py": dict(_HOTSPOT_META)},
    ).analyze()
    assert "src/parser.py" in {m.file_path for m in report.metrics}


def test_the_stored_metric_agrees_with_the_biomarker(tmp_path):
    """The file table and the biomarker must not disagree about the same file.

    ``has_test_file`` on the persisted metric is what every UI renders as
    "has tests" / "untested" and what the MCP payload documents as "does
    something test this file". Left filename-only it labelled a file untested
    while ``untested_hotspot`` stayed silent about it - issue #1740's
    disagreement, one layer further out.
    """
    parsed = _tree(tmp_path)
    graph = _graph(("tests/test_round_trips.py", "src/parser.py"))
    report = HealthAnalyzer(
        graph,
        parsed_files=parsed,
        git_meta_map={"src/parser.py": dict(_HOTSPOT_META)},
    ).analyze()
    metric = next(m for m in report.metrics if m.file_path == "src/parser.py")
    assert metric.has_test_file is True
