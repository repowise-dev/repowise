"""Rust sibling test modules should not be reported as dead code."""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def test_rust_sibling_tests_module_never_flagged() -> None:
    analyzer = DeadCodeAnalyzer(_build_graph(nodes={}), git_meta_map={})

    assert analyzer._should_never_flag("noor-server/src/server/routes/tests.rs", set())


def test_rust_sibling_tests_module_not_reported_unreachable() -> None:
    analyzer = DeadCodeAnalyzer(
        _build_graph(
            nodes={
                "noor-server/src/server/routes/tests.rs": {
                    "is_entry_point": False,
                    "language": "rust",
                    "symbol_count": 1,
                    "symbols": [],
                },
                "noor-server/src/lib.rs": {
                    "is_entry_point": True,
                    "language": "rust",
                    "symbol_count": 1,
                    "symbols": [],
                },
            }
        ),
        git_meta_map={},
    )

    report = analyzer.analyze(
        {
            "detect_unused_exports": False,
            "detect_unused_internal": False,
            "detect_zombie_packages": False,
        }
    )

    unreachable = [
        finding for finding in report.findings if finding.kind == DeadCodeKind.UNREACHABLE_FILE
    ]
    assert unreachable == []
