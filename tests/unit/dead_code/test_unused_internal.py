"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from tests.unit.dead_code._helpers import _build_graph


def _ts_local_refs(tmp_path, filename, source):
    path = str(tmp_path / filename)
    info = FileInfo(
        path=path,
        abs_path=path,
        language=("javascript" if filename.endswith((".js", ".jsx")) else "typescript"),
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ASTParser().parse_file(info, source.encode()).local_refs


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


def test_ts_value_references_rescue_only_referenced_private_symbols(tmp_path):
    path = str(tmp_path / "handlers.ts")
    source = (
        "function shutdown() {}\n"
        "function register() { process.on('SIGTERM', shutdown); }\n"
        "function recursive() { recursive(); }\n"
    )
    local_refs = _ts_local_refs(tmp_path, "handlers.ts", source)
    assert "shutdown" in local_refs
    assert "recursive" not in local_refs

    graph = _build_graph(
        nodes={
            path: {
                "is_entry_point": False,
                "is_test": False,
                "local_refs": local_refs,
                "symbols": [
                    {"name": name, "kind": "function", "visibility": "private"}
                    for name in ("shutdown", "register", "recursive")
                ],
            }
        }
    )
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    unused = {
        finding.symbol_name
        for finding in report.findings
        if finding.kind == DeadCodeKind.UNUSED_INTERNAL
    }
    assert "shutdown" not in unused
    assert "recursive" in unused


def test_ts_local_use_does_not_hide_unused_export():
    path = "sample/src/routes.ts"
    graph = _build_graph(
        nodes={
            path: {
                "is_entry_point": False,
                "is_test": False,
                "local_refs": frozenset({"LOCAL_ONLY_ROUTES"}),
                "symbols": [
                    {
                        "name": "LOCAL_ONLY_ROUTES",
                        "kind": "variable",
                        "language": "typescript",
                        "visibility": "public",
                    }
                ],
            }
        }
    )
    report = DeadCodeAnalyzer(graph).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    assert any(
        finding.kind == DeadCodeKind.UNUSED_EXPORT
        and finding.symbol_name == "LOCAL_ONLY_ROUTES"
        for finding in report.findings
    )


@pytest.mark.parametrize(
    "usage",
    [
        "const element = <Widget value={X} />;",
        "const [value] = useState(() => X);",
        "object.handler = X;",
        "const spread = { ...X };",
        "const shorthand = { X };",
        "type AsType = X;",
        "type AsValueType = typeof X;",
        "export { X };",
        "export default X;",
    ],
)
def test_ts_same_file_reference_shapes_are_recorded(tmp_path, usage):
    source = f"const X: any = {{}};\n{usage}\n"
    assert "X" in _ts_local_refs(tmp_path, "refs.tsx", source)


def test_ts_same_file_reference_ignores_shadowed_name(tmp_path):
    source = "const X = 1;\nfunction local(X: number) { return X; }\n"
    assert "X" not in _ts_local_refs(tmp_path, "shadow.ts", source)


def test_ts_genuinely_unused_const_is_not_recorded(tmp_path):
    source = "const neverUsed = 1;\n"
    assert "neverUsed" not in _ts_local_refs(tmp_path, "unused.ts", source)


def test_js_value_reference_is_recorded(tmp_path):
    source = "const shutdown = () => {};\nprocess.on('SIGTERM', shutdown);\n"
    assert "shutdown" in _ts_local_refs(tmp_path, "handlers.js", source)


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


def test_unused_internal_skipped_when_subclassed():
    """A private base class reached only via ``extends`` is still used."""
    g = _build_graph(
        nodes={
            "pkg/base.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "_BaseThing",
                        "kind": "class",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 12,
                        "complexity_estimate": 1,
                    },
                ],
            },
            "pkg/child.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "Thing",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 8,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[
            (
                "pkg/child.py::Thing",
                "pkg/base.py::_BaseThing",
                {"edge_type": "extends"},
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
    assert "_BaseThing" not in names


def test_unused_internal_skipped_when_referenced_as_a_value():
    """A private helper named in a dispatch table is used without being called."""
    g = _build_graph(
        nodes={
            "pkg/handlers.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 2,
                "symbols": [
                    {
                        "name": "_handle",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 6,
                        "complexity_estimate": 1,
                    },
                    {
                        "name": "register",
                        "kind": "function",
                        "visibility": "public",
                        "decorators": [],
                        "start_line": 8,
                        "end_line": 12,
                        "complexity_estimate": 1,
                    },
                ],
            },
        },
        edges=[
            (
                "pkg/handlers.py::register",
                "pkg/handlers.py::_handle",
                {"edge_type": "references"},
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
    assert "_handle" not in names


def test_unused_internal_still_flagged_with_only_containment_edges():
    """A ``defines`` / ``has_method`` containment edge alone must not count as use."""
    g = _build_graph(
        nodes={
            "pkg/lonely.py": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "_orphan",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 6,
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
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL}
    assert "_orphan" in names


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


def test_unused_internal_rust_impl_uncallable():
    """An impl block is an uncallable structural container; it must not be flagged as unused internal."""
    g = _build_graph(
        nodes={
            "src/lib.rs": {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "language": "rust",
                "symbol_count": 1,
                "symbols": [
                    {
                        "name": "MyStruct",
                        "kind": "impl",
                        "visibility": "private",
                        "language": "rust",
                        "decorators": [],
                        "start_line": 10,
                        "end_line": 25,
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
    names = {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL}
    assert "MyStruct" not in names
