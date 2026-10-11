"""A `pub` symbol whose only consumer is a `tests/*.rs` integration test
(which compiles as a separate, external crate — exactly why the symbol
must stay `pub`) must not be flagged `unused_export`. Drives the real
parser, GraphBuilder and analyzer end-to-end against a crate where a
function and a struct are only referenced from an integration test.

`Report` is constructed inside `lib.rs` by `build_report`, so it is
already reachable via an intra-file edge regardless of any test consumer —
asserting `Report` isn't flagged doesn't exercise the test-consumer path.
`Ticket` is declared in `lib.rs` but never constructed there; the
integration test is its only consumer, so it is the type that actually
pins the behavior under test.
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

_LIB_RS = (
    "// Report's fields are only ever asserted from the integration\n"
    "// test below.\n"
    "pub struct Report { pub id: u32, pub ok: bool }\n\n"
    "pub fn build_report() -> Report { Report { id: 1, ok: true } }\n\n"
    "// Ticket is never constructed in this file — the integration test\n"
    "// is its only consumer.\n"
    "pub struct Ticket { pub number: u32 }\n\n"
    "// dead_fn is never referenced anywhere.\n"
    "pub fn dead_fn() {}\n"
)

_TEST_RS = (
    "use net::{build_report, Ticket};\n\n"
    "#[test]\n"
    "fn test_report_fields() {\n"
    "    let r = build_report();\n"
    "    assert_eq!(r.id, 1);\n"
    "    assert!(r.ok);\n"
    "    let t = Ticket { number: 7 };\n"
    "    assert_eq!(t.number, 7);\n"
    "}\n"
)

# A crate whose only consumer of `build_report` / `Ticket` is an integration
# test under `tests/`, which compiles as an external crate and therefore
# reaches them only through `use net::{build_report, Ticket}` — no
# intra-crate edge.
_SOURCES: dict[str, str] = {
    "Cargo.toml": '[package]\nname = "net"\nversion = "0.1.0"\n',
    "src/lib.rs": _LIB_RS,
    "tests/report_test.rs": _TEST_RS,
}

# Same crate with the integration test removed — the consumer-deleted case.
_SOURCES_NO_TEST: dict[str, str] = {
    "Cargo.toml": '[package]\nname = "net"\nversion = "0.1.0"\n',
    "src/lib.rs": _LIB_RS,
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


def _build_graph(repo: Path, sources: dict[str, str]) -> nx.DiGraph:
    for rel, body in sources.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    builder = GraphBuilder(repo_path=repo)
    for rel in sources:
        if not rel.endswith(".rs"):
            continue
        abs_path = str((repo / rel).resolve())
        parsed = _PARSER.parse_file(_file_info(rel, abs_path), (repo / rel).read_bytes())
        builder.add_file(parsed)
    return builder.build()


def _unused_exports(graph: nx.DiGraph) -> set[str]:
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze(
        {
            "detect_unreachable_files": False,
            "detect_zombie_packages": False,
            "detect_unused_internals": False,
            "min_confidence": 0.0,
        }
    )
    return {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}


class TestRustIntegrationTestConsumer:
    def test_function_only_called_from_integration_test_not_flagged(
        self, tmp_path: Path
    ) -> None:
        unused_exports = _unused_exports(_build_graph(tmp_path, _SOURCES))
        assert "build_report" not in unused_exports

    def test_struct_only_used_via_integration_test_not_flagged(self, tmp_path: Path) -> None:
        # Ticket, not Report — Report is already rescued by the intra-file
        # edge from build_report constructing it, so it can't exercise the
        # test-consumer path on its own.
        unused_exports = _unused_exports(_build_graph(tmp_path, _SOURCES))
        assert "Ticket" not in unused_exports

    def test_genuinely_dead_export_still_flagged(self, tmp_path: Path) -> None:
        # True-positive guard: the test-consumer edge must not turn into a
        # blanket exemption for every export in a crate with a tests/ dir.
        unused_exports = _unused_exports(_build_graph(tmp_path, _SOURCES))
        assert "dead_fn" in unused_exports


class TestRustIntegrationTestConsumerRemoved:
    """Deleting the only consumer must flip the finding back to flagged —
    the property the "not flagged" tests above are actually pinning.
    """

    def test_function_flagged_once_its_only_consumer_is_gone(self, tmp_path: Path) -> None:
        unused_exports = _unused_exports(_build_graph(tmp_path, _SOURCES_NO_TEST))
        assert "build_report" in unused_exports

    def test_struct_flagged_once_its_only_consumer_is_gone(self, tmp_path: Path) -> None:
        unused_exports = _unused_exports(_build_graph(tmp_path, _SOURCES_NO_TEST))
        assert "Ticket" in unused_exports
