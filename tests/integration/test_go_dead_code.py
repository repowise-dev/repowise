"""End-to-end Go dead-code regression test for the go_sample fixture.

Runs the full FileTraverser → ASTParser → GraphBuilder → DeadCodeAnalyzer
pipeline against tests/fixtures/go_sample and proves the Go-parity work
(Phases 1-4) holds together: the one planted genuinely-dead exported
function is flagged, the genuinely-unimported package is unreachable, and
everything that is actually used — across multi-file packages, package
boundaries, structural interface satisfaction, and field-only type use —
is NOT flagged.

This is the regression guard that keeps the Go analyzer honest: if a future
change makes it over-suppress (the live assertions hold but the dead ones
fail) or regress to false positives (the dead assertions hold but a live one
fails), this test breaks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

GO_SAMPLE = Path(__file__).parent.parent / "fixtures" / "go_sample"


@pytest.fixture(scope="module")
def go_report():
    traverser = FileTraverser(GO_SAMPLE)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=GO_SAMPLE)

    for fi in traverser.traverse():
        source = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source))

    graph = builder.build()
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze({"min_confidence": 0.0})
    return report


def _names(report, kind) -> set[str]:
    return {f.symbol_name for f in report.findings if f.kind == kind}


def _paths(report, kind) -> set[str]:
    return {f.file_path for f in report.findings if f.kind == kind}


class TestGoSampleHonesty:
    def test_planted_dead_export_is_flagged(self, go_report) -> None:
        exports = _names(go_report, DeadCodeKind.UNUSED_EXPORT)
        assert "DeadExport" in exports

    def test_orphan_package_is_unreachable(self, go_report) -> None:
        unreachable = _paths(go_report, DeadCodeKind.UNREACHABLE_FILE)
        assert any("orphan/orphan.go" in p for p in unreachable)


class TestGoSampleNoFalsePositives:
    def test_live_exports_not_flagged(self, go_report) -> None:
        exports = _names(go_report, DeadCodeKind.UNUSED_EXPORT)
        # Store/ReadWriter via interface satisfaction; New via cross-package
        # call; NewMemStore via cmd/app; Config via field-only type_use;
        # MemStore via constructor return + implements.
        for live in ("Store", "Config", "New", "NewMemStore", "MemStore"):
            assert live not in exports, f"{live} wrongly flagged unused_export"

    def test_sibling_helper_not_flagged_internal(self, go_report) -> None:
        internals = _names(go_report, DeadCodeKind.UNUSED_INTERNAL)
        # greeting() is private but called from the sibling service.go.
        assert "greeting" not in internals

    def test_live_packages_not_unreachable(self, go_report) -> None:
        unreachable = _paths(go_report, DeadCodeKind.UNREACHABLE_FILE)
        for live in (
            "cmd/app/main.go",
            "store/store.go",
            "store/memory.go",
            "service/service.go",
            "service/helpers.go",
            "doc.go",  # exempt via never-flag convention despite no importer
        ):
            assert not any(live in p for p in unreachable), f"{live} wrongly unreachable"

    def test_test_file_not_flagged(self, go_report) -> None:
        unreachable = _paths(go_report, DeadCodeKind.UNREACHABLE_FILE)
        exports = _names(go_report, DeadCodeKind.UNUSED_EXPORT)
        assert not any("service_test.go" in p for p in unreachable)
        assert "TestRun" not in exports
