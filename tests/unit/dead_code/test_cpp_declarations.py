"""A C/C++ forward declaration is never an unused export (#1601).

The definition is the unit of deletion and now carries the use edges, so
reporting the header line too would only restate what the definition says —
or, when the definition is genuinely live, report a false positive.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer

from ._helpers import _build_graph


def _unused_export_symbols(graph) -> set[str]:
    report = DeadCodeAnalyzer(graph).analyze()
    return {f.symbol_name for f in report.findings if f.kind == "unused_export"}


def _cpp_nodes() -> dict[str, dict]:
    return {
        "src/shapes.h": {
            "node_type": "file",
            "language": "cpp",
            "symbols": [
                {
                    "name": "Area",
                    "kind": "function",
                    "visibility": "public",
                    "language": "cpp",
                    "is_declaration": True,
                    "defined_by": "src/shapes.cpp::Area",
                    "start_line": 2,
                    "end_line": 2,
                }
            ],
        },
        "src/shapes.cpp": {
            "node_type": "file",
            "language": "cpp",
            "symbols": [
                {
                    "name": "Area",
                    "kind": "function",
                    "visibility": "public",
                    "language": "cpp",
                    "is_declaration": False,
                    "start_line": 2,
                    "end_line": 2,
                }
            ],
        },
    }


class TestDeclarationNeverFlagged:
    def test_declaration_skipped_when_definition_is_used(self) -> None:
        graph = _build_graph(
            _cpp_nodes(),
            edges=[
                ("src/shapes.cpp", "src/shapes.h", {"edge_type": "imports"}),
                ("src/main.cpp::main", "src/shapes.cpp::Area", {"edge_type": "calls"}),
            ],
        )
        assert _unused_export_symbols(graph) == set()

    def test_definition_still_reported_when_truly_unused(self) -> None:
        # The declaration drops out, but the finding itself must survive and
        # point at the .cpp — suppressing declarations must not suppress real
        # dead code.
        graph = _build_graph(
            _cpp_nodes(),
            edges=[("src/shapes.cpp", "src/shapes.h", {"edge_type": "imports"})],
        )
        findings = [
            f
            for f in DeadCodeAnalyzer(graph).analyze().findings
            if f.kind == "unused_export" and f.symbol_name == "Area"
        ]
        assert [f.file_path for f in findings] == ["src/shapes.cpp"]

    def test_orphaned_prototype_is_still_reported(self) -> None:
        # No definition anywhere in the repo, so nothing carries ``defined_by``
        # and nothing else can carry the finding. Suppressing this one would
        # trade a false positive for a silent false negative.
        graph = _build_graph(
            {
                "src/orphan.h": {
                    "node_type": "file",
                    "language": "cpp",
                    "symbols": [
                        {
                            "name": "Vanished",
                            "kind": "function",
                            "visibility": "public",
                            "language": "cpp",
                            "is_declaration": True,
                            "start_line": 2,
                            "end_line": 2,
                        }
                    ],
                }
            }
        )
        assert _unused_export_symbols(graph) == {"Vanished"}
