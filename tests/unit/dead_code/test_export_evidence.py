"""An unused export is deletion-ready only when a use would have been visible.

"No importer names this symbol" says something only when the file's importers
name what they take. A C ``#include`` or a C# ``using`` names nothing, and a
file with no importer at all gives nothing to compare against, so both stay
below the deletion-ready threshold. C and C++ never reach it: the preprocessor
uses a ``typedef struct _X {...} X`` tag through ``X`` and a function through a
``#define`` alias, neither of which an edge records.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.risk_factors import (
    SAFE_CONFIDENCE_THRESHOLD,
    UNPROVEN_EXPORT_CONFIDENCE,
)
from tests.unit.dead_code._helpers import _build_graph

_EXPORTS_ONLY = {
    "detect_unreachable_files": False,
    "detect_unused_internals": False,
    "detect_zombie_packages": False,
    "min_confidence": 0.0,
}


def _sym(name: str, language: str, kind: str = "function") -> dict:
    return {
        "name": name,
        "kind": kind,
        "visibility": "public",
        "decorators": [],
        "start_line": 1,
        "end_line": 3,
        "language": language,
    }


def _export(path: str, language: str, importer_edge: dict | None, kind: str = "function"):
    nodes = {
        path: {"language": language, "symbols": [_sym("used_one", language, kind), _sym("lonely", language, kind)]},
        "app/main.x": {"language": language, "is_entry_point": True},
    }
    edges = [("app/main.x", path, importer_edge)] if importer_edge is not None else []
    report = DeadCodeAnalyzer(_build_graph(nodes, edges)).analyze(dict(_EXPORTS_ONLY))
    [finding] = [
        f for f in report.findings
        if f.kind is DeadCodeKind.UNUSED_EXPORT and f.symbol_name == "lonely"
    ]
    return finding


def test_naming_importer_makes_the_export_deletion_ready():
    finding = _export(
        "lib/util.py", "python", {"edge_type": "imports", "imported_names": ["used_one"]}
    )
    assert finding.confidence == pytest.approx(1.0)
    assert finding.safe_to_delete


def test_file_with_no_importer_is_not_deletion_ready():
    finding = _export("lib/util.py", "python", None)
    assert finding.confidence == pytest.approx(UNPROVEN_EXPORT_CONFIDENCE)
    assert finding.confidence < SAFE_CONFIDENCE_THRESHOLD
    assert not finding.safe_to_delete


def test_importer_that_names_nothing_is_no_evidence():
    # A C ``#include``: the edge exists, but carries no names to be absent from.
    finding = _export("src/unwind.h", "c", {"edge_type": "imports", "imported_names": []})
    assert finding.confidence == pytest.approx(UNPROVEN_EXPORT_CONFIDENCE)
    assert not finding.safe_to_delete


@pytest.mark.parametrize("language", ["c", "cpp", "objectivec"])
def test_preprocessed_languages_are_never_deletion_ready(language):
    # Even when a type reference names a sibling symbol of the same header.
    finding = _export(
        "src/unwinder.h",
        language,
        {"edge_type": "type_use", "imported_names": ["used_one"]},
        kind="struct",
    )
    assert finding.confidence == pytest.approx(UNPROVEN_EXPORT_CONFIDENCE)
    assert not finding.safe_to_delete


def test_importer_naming_a_namespace_is_no_evidence():
    # A C# ``using Peek.Common.Extensions`` edge carries the namespace segment,
    # which names no symbol of the file.
    finding = _export(
        "src/Extensions/SizeExtensions.cs",
        "csharp",
        {"edge_type": "imports", "imported_names": ["Extensions"]},
        kind="class",
    )
    assert finding.confidence == pytest.approx(UNPROVEN_EXPORT_CONFIDENCE)
    assert not finding.safe_to_delete


def test_extension_method_container_is_not_deletion_ready():
    # ``sig.GetConventions()`` calls the method without naming the class, so a
    # type reference to a sibling enum says nothing about the container.
    path = "src/Interop/CallingConventions.cs"
    nodes = {
        path: {
            "language": "csharp",
            "symbols": [
                _sym("CallingConventions", "csharp", "enum"),
                _sym("CallingConventionExtensions", "csharp", "class"),
            ],
        },
        "src/Jit.cs": {"language": "csharp", "is_entry_point": True},
    }
    graph = _build_graph(
        nodes,
        [("src/Jit.cs", path, {"edge_type": "type_use", "imported_names": ["CallingConventions"]})],
    )
    method_id = f"{path}::CallingConventionExtensions::GetConventions"
    graph.add_node(
        method_id,
        node_type="symbol",
        kind="method",
        name="GetConventions",
        signature="GetConventions(this MethodSignature signature) -> CallingConventions",
    )
    graph.add_edge(f"{path}::CallingConventionExtensions", method_id, edge_type="has_method")
    report = DeadCodeAnalyzer(graph).analyze(dict(_EXPORTS_ONLY))
    [finding] = [f for f in report.findings if f.symbol_name == "CallingConventionExtensions"]
    assert finding.confidence == pytest.approx(UNPROVEN_EXPORT_CONFIDENCE)
    assert not finding.safe_to_delete
