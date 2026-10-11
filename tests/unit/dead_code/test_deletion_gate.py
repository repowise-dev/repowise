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
)
from repowise.core.analysis.dead_code.models import DeadCodeFindingData
from tests.unit.dead_code._helpers import _build_graph


def _finding(path: str = "lib/util.py", **overrides) -> DeadCodeFindingData:
    fields = {
        "kind": DeadCodeKind.UNUSED_EXPORT,
        "file_path": path,
        "symbol_name": "lonely",
        "symbol_kind": "function",
        "confidence": 1.0,
        "reason": "no importers",
        "last_commit_at": None,
        "commit_count_90d": 0,
        "lines": 3,
        "evidence": ["No file imports symbol"],
        "safe_to_delete": True,
        "primary_owner": None,
        "age_days": None,
        "start_line": 1,
        "end_line": 3,
    }
    return DeadCodeFindingData(**{**fields, **overrides})


def _graph(
    path: str = "lib/util.py", language: str | None = "python", origins: tuple = ("import_scoped",)
) -> nx.DiGraph:
    """A file defining ``lonely``, called once per entry of *origins*."""
    attrs = {"node_type": "file", "symbols": [{"name": "lonely", "kind": "function"}]}
    graph = _build_graph({path: {**attrs, "language": language}})
    for i, origin in enumerate(origins):
        caller = f"app.x::f{i}"
        graph.add_node(caller, node_type="symbol", language=language)
        graph.add_edge(caller, f"{path}::lonely", edge_type="calls", resolution_origin=origin)
    return graph


def _gate(finding: DeadCodeFindingData, graph: nx.DiGraph | None = None, repo_root=None):
    apply_deletion_gate([finding], graph if graph is not None else _graph(finding.file_path), repo_root)
    return finding


def _says(finding: DeadCodeFindingData, text: str) -> bool:
    return any(text in line for line in finding.evidence)


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
    assert _says(finding, "Documentation or example code")


def test_docs_site_snippet_tree_is_never_deletion_ready(tmp_path):
    # A Writerside topic includes snippet files by line range.
    site = tmp_path / "documentation-website" / "Writerside"
    site.mkdir(parents=True)
    (site / "writerside.cfg").write_text("<ihp/>")
    path = "documentation-website/Writerside/snippets/src/Examples.kt"
    finding = _gate(_finding(path), _graph(path, "kotlin"), repo_root=tmp_path)
    assert not finding.safe_to_delete
    assert _says(finding, "Documentation or example code")


def test_export_only_finding_is_never_deletion_ready():
    finding = _gate(_finding(export_only=True))
    assert not finding.safe_to_delete
    assert _says(finding, "remove the export, not the symbol")


def test_language_with_partial_import_support_is_never_deletion_ready():
    finding = _gate(_finding("src/Util.scala"), _graph("src/Util.scala", "scala"))
    assert not finding.safe_to_delete
    assert _says(finding, "import support: partial")


def test_mostly_guessed_call_graph_is_never_deletion_ready():
    origins = ("global_unique",) * 6 + ("import_scoped",) * 4
    finding = _gate(_finding(), _graph(origins=origins))
    assert not finding.safe_to_delete
    assert _says(finding, "60% of python call edges")


def test_mostly_resolved_call_graph_keeps_deletion_readiness():
    origins = ("global_unique",) * 4 + ("import_scoped",) * 6
    assert WEAK_GUESSED_SHARE >= 0.4
    assert _gate(_finding(), _graph(origins=origins)).safe_to_delete


def test_language_with_no_call_edge_is_unknown_and_withheld():
    finding = _gate(_finding(), _graph(origins=()))
    assert not finding.safe_to_delete
    assert _says(finding, "No python call edge in this repository has a recorded resolution")


def test_edges_without_an_origin_do_not_count_as_resolved():
    # An older or rehydrated graph: edges exist, but none says how it was bound.
    finding = _gate(_finding(), _graph(origins=(None, None, None)))
    assert not finding.safe_to_delete
    assert _says(finding, "has a recorded resolution")


def test_unrecorded_edges_stay_out_of_the_share():
    # 2 guessed of 3 known is 67%, not 2 of 6 (33%): unknown edges are not resolved ones.
    origins = (None, None, None, "global_unique", "global_unique", "import_scoped")
    finding = _gate(_finding(), _graph(origins=origins))
    assert not finding.safe_to_delete
    assert _says(finding, "67% of python call edges")


@pytest.mark.parametrize("language", [None, ""])
def test_file_with_no_language_is_withheld(language):
    finding = _gate(_finding(), _graph(language=language))
    assert not finding.safe_to_delete
    assert _says(finding, "language is unknown")


def test_file_missing_from_the_graph_is_withheld():
    finding = _gate(_finding("lib/elsewhere.py"), _graph())
    assert not finding.safe_to_delete
    assert _says(finding, "language is unknown")


def test_gate_never_grants_or_rescores():
    finding = _gate(replace(_finding("docs/x.py"), safe_to_delete=False, confidence=0.9))
    assert not finding.safe_to_delete
    assert finding.confidence == 0.9
    assert finding.evidence == ["No file imports symbol"]


def test_base_reason_names_the_condition():
    finding = _gate(_finding("lib/config/util.py"))
    assert _says(finding, "runtime-load risk factor (config)")
    finding = _gate(_finding(confidence=0.69))
    assert _says(finding, "Confidence 0.69 is below the deletion-ready bar 0.70")


def test_read_time_check_shares_the_path_reasons():
    assert effective_safe_to_delete(1.0, "lib/util.py", True, "unused_export")
    assert not effective_safe_to_delete(1.0, "docs/snippets/util.py", True, "unused_export")
    assert not effective_safe_to_delete(1.0, "lib/util.py", True, "unreachable_file")
    assert not effective_safe_to_delete(0.69, "lib/util.py", True, "unused_export")


def test_analyzer_runs_the_gate():
    sym = {"kind": "function", "visibility": "public", "decorators": [], "language": "python"}
    nodes = {
        "docs/snippets/util.py": {
            "language": "python",
            "symbols": [
                {**sym, "name": "used_one", "start_line": 1, "end_line": 3},
                {**sym, "name": "lonely", "start_line": 5, "end_line": 7},
            ],
        },
        "app/main.py": {"language": "python", "is_entry_point": True},
    }
    imports = {"edge_type": "imports", "imported_names": ["used_one"]}
    report = DeadCodeAnalyzer(
        _build_graph(nodes, [("app/main.py", "docs/snippets/util.py", imports)])
    ).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    [finding] = [f for f in report.findings if f.symbol_name == "lonely"]
    assert finding.confidence == pytest.approx(1.0)
    assert not finding.safe_to_delete
    assert _says(finding, "Documentation or example code")
