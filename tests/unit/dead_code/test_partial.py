"""Tests for DeadCodeAnalyzer.analyze_partial (incremental update path)."""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def _graph_with_unused_export():
    """utils.py exports an unused `orphan`; main.py (entry) imports utils
    but not `orphan` by name."""
    return _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "orphan",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 2,
                    },
                ],
            },
            "pkg/main.py": {
                "is_entry_point": True,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 10,
                "symbols": [],
            },
        },
        edges=[("pkg/main.py", "pkg/utils.py", {"imported_names": ["other_func"]})],
    )


def test_analyze_partial_reports_unused_export_for_changed_file():
    """analyze_partial must run all detectors (not just unreachable files),
    so a changed file's unused export is reported."""
    analyzer = DeadCodeAnalyzer(_graph_with_unused_export(), git_meta_map={})

    partial = analyzer.analyze_partial(["pkg/utils.py"])

    by_symbol = {(f.file_path, f.symbol_name): f.kind for f in partial.findings}
    assert ("pkg/utils.py", "orphan") in by_symbol
    assert by_symbol[("pkg/utils.py", "orphan")] == DeadCodeKind.UNUSED_EXPORT


def test_analyze_partial_scopes_findings_to_affected_files():
    """Findings for files outside the affected set are not returned."""
    analyzer = DeadCodeAnalyzer(_graph_with_unused_export(), git_meta_map={})

    partial = analyzer.analyze_partial(["pkg/utils.py"])

    assert partial.findings
    assert all(f.file_path == "pkg/utils.py" for f in partial.findings)
