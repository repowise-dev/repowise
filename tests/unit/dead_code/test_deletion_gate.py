"""The deletion gate withholds ``safe_to_delete`` and says why; it never grants it.

One test per reason, each on a finding that is otherwise deletion-ready, plus
the controls that keep the gate from withholding everything.
"""

from __future__ import annotations

from dataclasses import replace

import networkx as nx
import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.deletion_gate import (
    WEAK_GUESSED_SHARE,
    apply_deletion_gate,
    effective_safe_to_delete,
    weak_resolution_languages,
)
from repowise.core.analysis.dead_code.models import DeadCodeFindingData
from tests.unit.dead_code._helpers import _build_graph


def _finding(path: str = "lib/util.py", name: str = "lonely") -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=DeadCodeKind.UNUSED_EXPORT,
        file_path=path,
        symbol_name=name,
        symbol_kind="function",
        confidence=1.0,
        reason="no importers",
        last_commit_at=None,
        commit_count_90d=0,
        lines=3,
        evidence=["No file imports symbol"],
        safe_to_delete=True,
        primary_owner=None,
        age_days=None,
        start_line=1,
        end_line=3,
    )


def _graph(path: str = "lib/util.py", language: str = "python", **sym_attrs) -> nx.DiGraph:
    sym = {"name": "lonely", "kind": "function", "start_line": 1, "end_line": 3, **sym_attrs}
    return _build_graph({path: {"node_type": "file", "language": language, "symbols": [sym]}})


def _gate(finding: DeadCodeFindingData, graph: nx.DiGraph | None = None, repo_root=None):
    apply_deletion_gate([finding], graph if graph is not None else _graph(finding.file_path), repo_root)
    return finding


def test_ordinary_finding_stays_deletion_ready():
    finding = _gate(_finding())
    assert finding.safe_to_delete
    assert finding.evidence == ["No file imports symbol"]


@pytest.mark.parametrize(
    "path",
    ["docs/snippets/util.py", "examples/basic/util.py", "crates/grep/examples/simple.rs"],
)
def test_docs_and_tool_examples_are_never_deletion_ready(path):
    finding = _gate(_finding(path))
    assert not finding.safe_to_delete
    assert finding.confidence == 1.0
    assert any("Documentation or example code" in line for line in finding.evidence)


def test_docs_site_snippet_tree_is_never_deletion_ready(tmp_path):
    # A Writerside topic includes snippet files by line range.
    site = tmp_path / "documentation-website" / "Writerside"
    site.mkdir(parents=True)
    (site / "writerside.cfg").write_text("<ihp/>")
    path = "documentation-website/Writerside/snippets/src/Examples.kt"
    finding = _gate(_finding(path), _graph(path, "kotlin"), repo_root=tmp_path)
    assert not finding.safe_to_delete
    assert any("Documentation or example code" in line for line in finding.evidence)


def test_export_only_finding_is_never_deletion_ready():
    finding = _finding()
    finding.export_only = True  # the export-only pass's marker
    _gate(finding)
    assert not finding.safe_to_delete
    assert any("remove the export, not the symbol" in line for line in finding.evidence)


def test_language_with_partial_import_support_is_never_deletion_ready():
    finding = _gate(_finding("src/Util.scala"), _graph("src/Util.scala", "scala"))
    assert not finding.safe_to_delete
    assert any("import support: partial" in line for line in finding.evidence)


def _calls_graph(guessed: int, resolved: int) -> nx.DiGraph:
    graph = _graph()
    for i in range(guessed + resolved):
        origin = "global_unique" if i < guessed else "import_scoped"
        graph.add_node(f"a.py::f{i}", node_type="symbol", language="python")
        graph.add_edge(f"a.py::f{i}", "lib/util.py::lonely", edge_type="calls", resolution_origin=origin)
    return graph


def test_mostly_guessed_call_graph_is_never_deletion_ready():
    finding = _gate(_finding(), _calls_graph(guessed=6, resolved=4))
    assert not finding.safe_to_delete
    assert any("60% of python call edges" in line for line in finding.evidence)


def test_mostly_resolved_call_graph_keeps_deletion_readiness():
    graph = _calls_graph(guessed=4, resolved=6)
    assert WEAK_GUESSED_SHARE >= 0.4
    assert "python" not in weak_resolution_languages(graph)
    assert _gate(_finding(), graph).safe_to_delete


def test_public_api_symbol_is_never_deletion_ready():
    finding = _gate(_finding(), _graph(public_api="lib/__init__.py"))
    assert not finding.safe_to_delete
    assert any("exported from lib/__init__.py" in line for line in finding.evidence)


def test_symbol_checked_and_not_public_keeps_deletion_readiness():
    assert _gate(_finding(), _graph(public_api="")).safe_to_delete


def test_gate_never_grants_or_rescores():
    finding = replace(_finding("docs/x.py"), safe_to_delete=False, confidence=0.9)
    _gate(finding)
    assert not finding.safe_to_delete
    assert finding.confidence == 0.9
    assert finding.evidence == ["No file imports symbol"]


def test_read_time_check_shares_the_path_reasons():
    assert effective_safe_to_delete(1.0, "lib/util.py", True, "unused_export")
    assert not effective_safe_to_delete(1.0, "docs/snippets/util.py", True, "unused_export")
    assert not effective_safe_to_delete(1.0, "lib/util.py", True, "unreachable_file")
    assert not effective_safe_to_delete(0.69, "lib/util.py", True, "unused_export")


def test_analyzer_runs_the_gate():
    nodes = {
        "docs/snippets/util.py": {
            "language": "python",
            "symbols": [
                {"name": n, "kind": "function", "visibility": "public", "decorators": [],
                 "start_line": i, "end_line": i + 2, "language": "python"}
                for i, n in ((1, "used_one"), (5, "lonely"))
            ],
        },
        "app/main.py": {"language": "python", "is_entry_point": True},
    }
    edges = [("app/main.py", "docs/snippets/util.py", {"edge_type": "imports", "imported_names": ["used_one"]})]
    report = DeadCodeAnalyzer(_build_graph(nodes, edges)).analyze(
        {"detect_unreachable_files": False, "detect_unused_internals": False,
         "detect_zombie_packages": False, "min_confidence": 0.0}
    )
    [finding] = [f for f in report.findings if f.symbol_name == "lonely"]
    assert finding.confidence == pytest.approx(1.0)
    assert not finding.safe_to_delete

