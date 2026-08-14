"""A `pub` symbol whose only consumer is a `tests/*.rs` integration test
(which compiles as a separate, external crate — exactly why the symbol
must stay `pub`) must not be flagged `unused_export`. Drives the real
parser, GraphBuilder and analyzer end-to-end against a crate where a
struct and the function returning it are only referenced via
`use net::build_report` from an integration test.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()

# A crate whose only consumer of `Report` / `build_report` is an integration
# test under `tests/`, which compiles as an external crate and therefore
# reaches them only through `use net::build_report` — no intra-crate edge.
_SOURCES: dict[str, str] = {
    "Cargo.toml": '[package]\nname = "net"\nversion = "0.1.0"\n',
    "src/lib.rs": (
        "// Report's fields are only ever asserted from the integration\n"
        "// test below.\n"
        "pub struct Report { pub id: u32, pub ok: bool }\n\n"
        "pub fn build_report() -> Report { Report { id: 1, ok: true } }\n\n"
        "// dead_fn is never referenced anywhere.\n"
        "pub fn dead_fn() {}\n"
    ),
    "tests/report_test.rs": (
        "use net::build_report;\n\n"
        "#[test]\n"
        "fn test_report_fields() {\n"
        "    let r = build_report();\n"
        "    assert_eq!(r.id, 1);\n"
        "    assert!(r.ok);\n"
        "}\n"
    ),
}


def _file_info(path: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language="rust",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=path.startswith("tests/"),
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_graph(repo: Path) -> nx.DiGraph:
    for rel, body in _SOURCES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    builder = GraphBuilder(repo_path=repo)
    for rel in _SOURCES:
        if not rel.endswith(".rs"):
            continue
        abs_path = str((repo / rel).resolve())
        parsed = _PARSER.parse_file(_file_info(rel, abs_path), (repo / rel).read_bytes())
        builder.add_file(parsed)
    return builder.build()


class TestRustIntegrationTestConsumer:
    def _report(self, graph: nx.DiGraph):
        analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
        return analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_zombie_packages": False,
                "detect_unused_internals": False,
                "min_confidence": 0.0,
            }
        )

    def test_function_only_called_from_integration_test_not_flagged(
        self, tmp_path: Path
    ) -> None:
        report = self._report(_build_graph(tmp_path))
        unused_exports = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "build_report" not in unused_exports

    def test_struct_only_used_via_integration_test_not_flagged(self, tmp_path: Path) -> None:
        report = self._report(_build_graph(tmp_path))
        unused_exports = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "Report" not in unused_exports

    def test_genuinely_dead_export_still_flagged(self, tmp_path: Path) -> None:
        # True-positive guard: the test-consumer edge must not turn into a
        # blanket exemption for every export in a crate with a tests/ dir.
        report = self._report(_build_graph(tmp_path))
        unused_exports = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "dead_fn" in unused_exports
