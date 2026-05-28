"""End-to-end TS/JS dead-code regression test for the ts_sample fixture.

Runs the full FileTraverser -> ASTParser -> GraphBuilder -> DeadCodeAnalyzer
pipeline against tests/fixtures/ts_sample and proves the JS/TS-parity work
(Phases 1-3) holds together: the one planted genuinely-dead interface is
flagged, the genuinely-orphaned module is unreachable, and everything that
is actually live -- across workspace packages, exports-wildcard entry
points, never-flag convention files (tests/Next.js app router),
script-runner entry detection, and Phase 2 type-use edges -- is NOT
flagged.

This is the regression guard that keeps the TS analyzer honest: if a future
change makes it over-suppress (the live assertions hold but the dead ones
fail) or regress to false positives (the dead assertions hold but a live
one fails), this test breaks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

TS_SAMPLE = Path(__file__).parent.parent / "fixtures" / "ts_sample"


@pytest.fixture(scope="module")
def ts_report():
    traverser = FileTraverser(TS_SAMPLE)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=TS_SAMPLE)

    for fi in traverser.traverse():
        source = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source))

    graph = builder.build()
    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    return analyzer.analyze({"min_confidence": 0.0})


def _names(report, kind) -> set[str]:
    return {f.symbol_name for f in report.findings if f.kind == kind}


def _paths(report, kind) -> set[str]:
    return {f.file_path for f in report.findings if f.kind == kind}


class TestTsSampleHonesty:
    """The planted true positives must still be flagged."""

    def test_planted_dead_interface_is_flagged(self, ts_report) -> None:
        exports = _names(ts_report, DeadCodeKind.UNUSED_EXPORT)
        assert "UnusedConfigShape" in exports

    def test_orphan_module_is_unreachable(self, ts_report) -> None:
        unreachable = _paths(ts_report, DeadCodeKind.UNREACHABLE_FILE)
        assert any("packages/lib/src/orphan.ts" in p for p in unreachable)


class TestTsSampleNoFalsePositives:
    """Live code must NOT be flagged."""

    def test_interface_used_only_as_type_is_live(self, ts_report) -> None:
        exports = _names(ts_report, DeadCodeKind.UNUSED_EXPORT)
        # ``User`` is referenced only as a field type / parameter type. Phase 2's
        # type_use resolution must keep it off the unused list.
        assert "User" not in exports

    def test_workspace_main_entry_not_unreachable(self, ts_report) -> None:
        unreachable = _paths(ts_report, DeadCodeKind.UNREACHABLE_FILE)
        for live in (
            "packages/lib/src/index.ts",
            "packages/lib/src/user-service.ts",
            "packages/app/src/main.ts",
            "packages/app/src/helper.ts",
        ):
            assert not any(live in p for p in unreachable), f"{live} wrongly unreachable"

    def test_exports_wildcard_locales_not_unreachable(self, ts_report) -> None:
        unreachable = _paths(ts_report, DeadCodeKind.UNREACHABLE_FILE)
        # Both locales are reached only through ``"./locales/*"`` in
        # package.json#exports. Phase 1's TsWorkspaceIndex must mark them
        # is_entry_point so the analyzer doesn't flag them.
        for locale in ("en.ts", "fr.ts"):
            assert not any(f"packages/lib/src/locales/{locale}" in p for p in unreachable)

    def test_test_file_not_unreachable(self, ts_report) -> None:
        unreachable = _paths(ts_report, DeadCodeKind.UNREACHABLE_FILE)
        # ``**/*.test.ts`` is never-flagged.
        assert not any("helper.test.ts" in p for p in unreachable)

    def test_next_app_router_files_not_unreachable(self, ts_report) -> None:
        unreachable = _paths(ts_report, DeadCodeKind.UNREACHABLE_FILE)
        # ``app/page.tsx`` and ``app/route.ts`` are filesystem-convention
        # entry points and must not be flagged.
        for fname in ("page.tsx", "route.ts"):
            assert not any(f"app/{fname}" in p for p in unreachable)

    def test_npm_script_entry_not_unreachable(self, ts_report) -> None:
        unreachable = _paths(ts_report, DeadCodeKind.UNREACHABLE_FILE)
        # ``scripts/build.ts`` is referenced from package.json#scripts.build
        # via ``tsx scripts/build.ts``. Phase 3's script scanner must
        # surface it as live.
        assert not any("scripts/build.ts" in p for p in unreachable)
