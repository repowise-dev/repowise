"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


def test_unused_internal_default_on():
    """detect_unused_internals defaults to True; private symbols with no callers are flagged."""
    g = _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "_helper",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 12,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )

    internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
    assert any(f.symbol_name == "_helper" for f in internals)
    assert all(f.safe_to_delete is False for f in internals)


def test_unused_internal_explicit_opt_out():
    """Setting detect_unused_internals=False disables the detector."""
    g = _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "_helper",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 12,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "detect_unused_internals": False,
            "min_confidence": 0.0,
        }
    )

    internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
    assert internals == []


def test_unused_internal_skipped_when_imported_by_name():
    """A private helper imported by name into a sibling module (typical
    dispatch-table pattern: ``HANDLERS = {"python": _extract_py, ...}``)
    is reached at runtime via dict lookup — no direct ``calls`` edge
    will exist, but the ``imports`` edge carries the symbol name. Such
    helpers must not be flagged as unused internals."""
    g = _build_graph(
        nodes={
            "pkg/python_handler.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "_extract_python",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 15,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/dispatch.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 0,
                "symbols": [],
            },
        },
        edges=[
            (
                "pkg/dispatch.py",
                "pkg/python_handler.py",
                {"edge_type": "imports", "imported_names": ["_extract_python"]},
            ),
        ],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL}
    assert "_extract_python" not in names


def test_unused_internal_still_flagged_when_imports_dont_carry_name():
    """Sanity check: an ``imports`` edge that does NOT list the private
    symbol's name (e.g. the importer pulls a different sibling symbol)
    must not rescue the helper from the unused-internal pass."""
    g = _build_graph(
        nodes={
            "pkg/helpers.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [
                    {
                        "name": "_unused_helper",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 8,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/consumer.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 0,
                "symbols": [],
            },
        },
        edges=[
            (
                "pkg/consumer.py",
                "pkg/helpers.py",
                {"edge_type": "imports", "imported_names": ["something_else"]},
            ),
        ],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL}
    assert "_unused_helper" in names
