"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from tests.unit.dead_code._helpers import _build_graph


def test_unused_export_detected():
    """A public symbol with no importers should be flagged as unused export."""
    g = _build_graph(
        nodes={
            "pkg/utils.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 3,
                "symbols": [
                    {
                        "name": "helper_func",
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
        # main.py imports utils.py but does NOT import helper_func by name
        edges=[("pkg/main.py", "pkg/utils.py", {"imported_names": ["other_func"]})],
    )

    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )

    unused = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    sym_names = [f.symbol_name for f in unused]
    assert "helper_func" in sym_names

    finding = next(f for f in unused if f.symbol_name == "helper_func")
    assert finding.start_line == 1
    assert finding.end_line == 10


def test_unused_export_skipped_when_symbol_has_incoming_calls():
    """A public symbol with no file-level importers but at least one
    incoming ``calls`` edge on the symbol itself is still in use —
    typical for C++ intra-file helpers and ``Foo::method`` qualified
    definitions reached via call resolution rather than headers."""
    g = _build_graph(
        nodes={
            "pkg/utils.cpp": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "language": "cpp",
                "symbols": [
                    {
                        "name": "helper",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 10,
                        "complexity_estimate": 1,
                    },
                    {
                        "name": "caller",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 12,
                        "end_line": 20,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[
            ("pkg/utils.cpp::caller", "pkg/utils.cpp::helper", {"edge_type": "calls"}),
        ],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
        }
    )
    sym_names = [f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT]
    # ``helper`` is called by ``caller`` — must not be flagged.
    assert "helper" not in sym_names
    # ``caller`` has no callers — still flagged.
    assert "caller" in sym_names


def test_unused_export_skipped_when_file_imported_as_namespace():
    """A public symbol in a file that is imported by its module name
    (``from . import cargo``) is a dispatch-table candidate — the
    importer holds a binding to the module and can call any public
    attribute via ``cargo.<attr>(...)``. We cannot tell statically
    which attribute is used, so every public symbol in the file must
    be treated as live."""
    g = _build_graph(
        nodes={
            "pkg/external_systems/cargo.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "parse",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 12,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/external_systems/__init__.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 0,
                "symbols": [],
            },
        },
        edges=[
            (
                "pkg/external_systems/__init__.py",
                "pkg/external_systems/cargo.py",
                {"edge_type": "imports", "imported_names": ["cargo"]},
            ),
        ],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "parse" not in names


def test_unused_export_still_flagged_when_namespace_name_differs():
    """Negative test for the namespace-import rescue: if the importer
    pulls a *different* sibling module, the public symbol's file is
    still effectively orphan and the symbol must be flagged."""
    g = _build_graph(
        nodes={
            "pkg/external_systems/cargo.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "parse",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 12,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/external_systems/__init__.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 0,
                "symbols": [],
            },
        },
        edges=[
            # importer pulled "npm" only — does NOT mention "cargo"
            (
                "pkg/external_systems/__init__.py",
                "pkg/external_systems/cargo.py",
                {"edge_type": "imports", "imported_names": ["npm"]},
            ),
        ],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "parse" in names


def test_unused_export_not_rescued_for_index_stem():
    """``index.ts`` is the barrel filename in JS/TS package layouts —
    every workspace package's source root has one, and most imports of
    a package land on it. We must not over-rescue every public export
    of every ``index.ts`` just because a downstream importer happens
    to use the literal name ``index``. Same applies to Python
    ``__init__`` (already covered by the global never-flag list)."""
    g = _build_graph(
        nodes={
            "pkg/ui/src/index.ts": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "language": "typescript",
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "stranded_export",
                        "kind": "function",
                        "visibility": "public",
                        "language": "typescript",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 5,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/web/src/page.ts": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "language": "typescript",
                "symbol_count": 0,
                "symbols": [],
            },
        },
        edges=[
            (
                "pkg/web/src/page.ts",
                "pkg/ui/src/index.ts",
                {"edge_type": "imports", "imported_names": ["index"]},
            ),
        ],
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}
    assert "stranded_export" in names
