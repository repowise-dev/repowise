"""A file's dependents are the files that depend on it in code, not its co-change partners.

The graph carries ``co_changes`` edges beside the import edges, so a raw
in-degree counted every file ever committed alongside this one as a dependent.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.engine import _dependents_count
from repowise.core.analysis.health.models import Severity
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.traverser import FileTraverser

_TARGET = "src/parser.py"


def _graph(importers: int, co_change_partners: int) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_node(_TARGET, node_type="file", language="python")
    for i in range(importers):
        g.add_edge(f"src/consumer_{i}.py", _TARGET, edge_type="imports")
    for i in range(co_change_partners):
        # Co-change is undirected history; the builder records both directions.
        g.add_edge(f"src/sibling_{i}.py", _TARGET, edge_type="co_changes")
        g.add_edge(_TARGET, f"src/sibling_{i}.py", edge_type="co_changes")
    # The file's own symbols are containment, not dependents.
    g.add_edge(_TARGET, f"{_TARGET}::parse", edge_type="defines")
    g.nodes[f"{_TARGET}::parse"]["node_type"] = "symbol"
    return g


def test_two_importers_and_fifty_co_change_partners_is_two_dependents():
    graph = _graph(importers=2, co_change_partners=50)
    assert graph.in_degree(_TARGET) == 52  # the number the engine used to report
    assert _dependents_count(graph, _TARGET) == 2


def test_the_engine_reports_importers_only(tmp_path):
    """End to end: untested_hotspot prints the count the engine handed it."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parser.py").write_text("def parse(s):\n    return s\n", encoding="utf-8")
    parsed = [
        parse_file(f, (tmp_path / f.path).read_bytes()) for f in FileTraverser(tmp_path).traverse()
    ]
    report = HealthAnalyzer(
        _graph(importers=4, co_change_partners=50),
        parsed_files=parsed,
        git_meta_map={
            _TARGET: {"is_hotspot": True, "commit_count_90d": 30, "commit_count_total": 30}
        },
    ).analyze()
    [finding] = [f for f in report.findings if f.biomarker_type == "untested_hotspot"]
    assert finding.reason.split("— ")[-1] == "4 dependents"
    # Ten or more dependents would have made it critical; four at 0% is high.
    assert finding.severity == Severity.HIGH
