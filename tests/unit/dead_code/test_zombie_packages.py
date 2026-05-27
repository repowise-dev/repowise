"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


def test_zombie_package_detected():
    """A package with no incoming inter-package imports should be flagged as zombie."""
    g = _build_graph(
        nodes={
            "pkgA/mod1.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 5,
                "symbols": [],
            },
            "pkgA/mod2.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [],
            },
            "pkgB/mod1.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 7,
                "symbols": [],
            },
        },
        edges=[
            # pkgA/mod1 imports from pkgA/mod2 (intra-package only)
            ("pkgA/mod1.py", "pkgA/mod2.py"),
            # pkgB has no inter-package importers either, but we focus on pkgA
            # having NO imports from pkgB -> pkgA
        ],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "min_confidence": 0.0,
        }
    )

    zombie = [f for f in report.findings if f.kind == DeadCodeKind.ZOMBIE_PACKAGE]
    pkgs = [f.package for f in zombie]
    # Both pkgA and pkgB are zombie since neither has inter-package importers
    assert "pkgA" in pkgs
    assert "pkgB" in pkgs
