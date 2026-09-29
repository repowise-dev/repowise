"""Unit tests for DeadCodeAnalyzer."""

from __future__ import annotations

from repowise.core.analysis.dead_code import (
    DeadCodeAnalyzer,
    DeadCodeKind,
)
from repowise.core.analysis.dead_code.name_occurrences import UNVERIFIED_INTERNAL_CONFIDENCE
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
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


# ---------------------------------------------------------------------------
# In-file use: the graph carries no ``reads`` edge for a constant, a callback
# passed as a value, or a type in an annotation, so the file's own text decides.
# ---------------------------------------------------------------------------


def _internals_over(path: str, source: str, symbols: list[dict], language: str = "python") -> dict:
    """Unused-internal findings for one file analysed with its own source."""
    g = _build_graph(
        nodes={
            path: {
                "is_entry_point": False,
                "is_test": False,
                "is_api_contract": False,
                "symbols": [
                    {
                        "visibility": "private",
                        "decorators": [],
                        "kind": "function",
                        "language": language,
                        **s,
                    }
                    for s in symbols
                ],
            },
        },
    )
    report = DeadCodeAnalyzer(g, git_meta_map={}, source_map={path: source.encode()}).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    return {f.symbol_name: f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL}


def test_ts_module_const_read_in_the_same_file_is_not_reported():
    source = (
        "const RETRY_LIMIT = 5;\n"  # 1
        "const STRANDED = 9;\n"  # 2
        "export function run(n: number) {\n"  # 3
        "  return n < RETRY_LIMIT;\n"  # 4
        "}\n"
    )
    found = _internals_over(
        "src/run.ts",
        source,
        [
            {"name": "RETRY_LIMIT", "kind": "constant", "start_line": 1, "end_line": 1},
            {"name": "STRANDED", "kind": "constant", "start_line": 2, "end_line": 2},
        ],
        language="typescript",
    )
    assert "RETRY_LIMIT" not in found
    assert "STRANDED" in found  # the control: a const nothing reads


def test_python_callback_passed_as_target_is_not_reported():
    source = (
        "import threading\n"  # 1
        "def _worker():\n"  # 2
        "    pass\n"  # 3
        "def start():\n"  # 4
        "    threading.Thread(target=_worker).start()\n"  # 5
    )
    found = _internals_over(
        "pkg/jobs.py", source, [{"name": "_worker", "start_line": 2, "end_line": 3}]
    )
    assert found == {}


def test_python_function_handed_to_re_sub_is_not_reported():
    source = (
        "import re\n"
        "def _escape(match):\n"
        "    return match.group(0)\n"
        "def clean(text):\n"
        "    return re.sub(r'x', _escape, text)\n"
    )
    found = _internals_over(
        "pkg/text.py", source, [{"name": "_escape", "start_line": 2, "end_line": 3}]
    )
    assert found == {}


def test_class_named_only_in_a_type_annotation_is_not_reported():
    source = (
        "class _Options:\n"  # 1
        "    verbose = False\n"  # 2
        "def configure(opts: '_Options') -> None:\n"  # 3
        "    print(opts)\n"
    )
    found = _internals_over(
        "pkg/opts.py",
        source,
        [{"name": "_Options", "kind": "class", "start_line": 1, "end_line": 2}],
    )
    assert found == {}


def test_recursive_only_function_stays_reported():
    source = (
        "def _walk(node):\n"  # 1
        "    for child in node:\n"  # 2
        "        _walk(child)\n"  # 3
    )
    found = _internals_over(
        "pkg/tree.py", source, [{"name": "_walk", "start_line": 1, "end_line": 3}]
    )
    assert "_walk" in found
    assert found["_walk"].confidence == 0.65


def test_unknown_span_is_kept_but_hidden_below_the_review_floor():
    source = "def _helper():\n    pass\n"
    found = _internals_over("pkg/util.py", source, [{"name": "_helper"}])
    assert found["_helper"].confidence == UNVERIFIED_INTERNAL_CONFIDENCE
    assert UNVERIFIED_INTERNAL_CONFIDENCE < RISK_CAP_CONFIDENCE
    assert "unverified" in found["_helper"].evidence[-1]


def test_a_mention_in_a_comment_counts_as_a_use():
    # The accepted textual ceiling: a comment naming the helper is read as a
    # use. It can only cost recall, never report a live symbol.
    source = "# see _helper below\ndef _helper():\n    pass\n"
    found = _internals_over(
        "pkg/util.py", source, [{"name": "_helper", "start_line": 2, "end_line": 3}]
    )
    assert found == {}


def test_without_source_the_finding_is_left_as_it_was():
    g = _build_graph(
        nodes={
            "pkg/util.py": {
                "symbols": [
                    {
                        "name": "_helper",
                        "kind": "function",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": 1,
                        "end_line": 2,
                    }
                ],
            },
        },
    )
    report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
        {"detect_unreachable_files": False, "detect_unused_exports": False, "min_confidence": 0.0}
    )
    [finding] = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
    assert finding.confidence == 0.65
