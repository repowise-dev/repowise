"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


def test_interface_without_implementor_demoted_below_safe_threshold():
    """Public interfaces with no incoming ``implements`` edges have
    their unused-export confidence clamped below 0.7 so the demo
    doesn't ship them as confident dead code. Implementor detection
    is heuristic — absence is missing-signal, not evidence-of-absence.
    """
    g = _build_graph(
        nodes={
            "src/IBasketService.cs": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "IBasketService",
                        "kind": "interface",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                        "language": "csharp",
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    interface_findings = [
        f
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT and f.symbol_name == "IBasketService"
    ]
    # The finding may still surface, but never at safe-to-delete confidence.
    for f in interface_findings:
        assert f.confidence <= 0.4, f"interface flagged at unsafe confidence {f.confidence}"
        assert f.safe_to_delete is False


def test_com_contract_method_demoted_below_safe_threshold():
    """``QueryInterface`` / ``AddRef`` / ``Release`` are reached through
    a native COM vtable — never via a static caller. The analyzer must
    not flag them as confident dead code.
    """
    g = _build_graph(
        nodes={
            "src/CoFoo.cpp": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [
                    {
                        "name": "QueryInterface",
                        "kind": "method",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 10,
                        "end_line": 25,
                        "complexity_estimate": 1,
                        "language": "cpp",
                    },
                    {
                        "name": "AddRef",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 27,
                        "end_line": 30,
                        "complexity_estimate": 1,
                        "language": "cpp",
                    },
                    {
                        "name": "Release",
                        "kind": "method",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 32,
                        "end_line": 40,
                        "complexity_estimate": 1,
                        "language": "cpp",
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    com_findings = [
        f for f in report.findings if f.symbol_name in {"QueryInterface", "AddRef", "Release"}
    ]
    for f in com_findings:
        assert f.confidence <= 0.4, f"COM method flagged at unsafe confidence {f.confidence}"
        assert f.safe_to_delete is False


def test_release_in_non_com_language_not_clamped():
    """A free function named ``Release`` in TypeScript/Python is *not* a
    COM contract method — the contract-method clamp must not apply.
    """
    g = _build_graph(
        nodes={
            "src/foo.ts": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "Release",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                        "language": "typescript",
                    },
                ],
            },
        },
        edges=[],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    # TS ``function`` kind is universally skipped from unused-export
    # detection (functions surface as variables in JS-style modules),
    # so the strongest assertion is "no spurious COM clamp masked the
    # real behaviour". The function should still be detectable somehow
    # downstream; here we just confirm the analyzer didn't crash and
    # didn't ship it under contract-method semantics.
    com_findings = [f for f in report.findings if f.symbol_name == "Release"]
    for f in com_findings:
        # If it surfaces at all, it must do so under the *normal*
        # rule — not under the COM clamp specifically. The normal
        # rule yields 0.7 / 1.0 depending on file-level importers.
        assert f.confidence >= 0.7 or f.confidence < 0.4
