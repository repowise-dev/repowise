"""End-to-end C/C++ dead-code regression test for the cpp_sample fixture.

Runs the full FileTraverser -> ASTParser -> GraphBuilder -> DeadCodeAnalyzer
pipeline against tests/fixtures/cpp_sample, the small CMake-driven sample
exercising the Phase 1-4 C/C++ parity work:

- a public header (``coffee/brew.h``) with a project export macro
  (``COFFEE_EXPORT``) and matching implementation (``brew.cc``);
- an ``int main``-bearing app TU consuming the library;
- a GoogleTest TU exercising the framework_edges fixture rescue
  (``TEST_F(BrewFixture, ...)`` -> ``BrewFixture`` declared in a header
  consumed only by the test source);
- a planted-dead header (``legacy.h``) and a planted-dead source
  (``legacy_unused.cc``) that MUST stay flagged.

If a future change over-suppresses, the planted-dead assertions fail.
If a future change regresses to false positives, the live assertions
fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


CPP_SAMPLE = Path(__file__).parent.parent / "fixtures" / "cpp_sample"


@pytest.fixture(scope="module")
def cpp_report():
    traverser = FileTraverser(CPP_SAMPLE)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=CPP_SAMPLE)

    for fi in traverser.traverse():
        source = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source))

    graph = builder.build()
    builder.add_framework_edges([])
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        registry = HintRegistry()
        builder.add_dynamic_edges(registry.extract_all(CPP_SAMPLE))
    except Exception:
        pass

    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    return analyzer.analyze({"min_confidence": 0.0})


def _paths(report, kind) -> set[str]:
    return {f.file_path for f in report.findings if f.kind == kind}


def _names(report, kind) -> set[str]:
    return {f.symbol_name for f in report.findings if f.kind == kind}


class TestPlantedDeadStillFlagged:
    def test_legacy_header_is_unreachable(self, cpp_report) -> None:
        unreachable = _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        assert any("legacy.h" in p for p in unreachable), (
            "libcoffee/include/coffee/legacy.h has no #include and no symbol "
            "reference — it MUST remain unreachable_file. If this fails, the "
            "analyzer is over-suppressing."
        )

    def test_legacy_unused_source_is_unreachable(self, cpp_report) -> None:
        unreachable = _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        assert any("legacy_unused.cc" in p for p in unreachable), (
            "legacy_unused.cc has no caller and is not the only source in "
            "its directory's main-bearing siblings — it MUST remain flagged."
        )

    def test_genuinely_dead_class_is_flagged(self, cpp_report) -> None:
        all_dead_names = (
            _names(cpp_report, DeadCodeKind.UNUSED_EXPORT)
            | _names(cpp_report, DeadCodeKind.UNUSED_INTERNAL)
            | _names(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        )
        # Either the class itself flags as unused_export/internal, or its
        # carrier file flags as unreachable_file (already asserted above).
        # We assert at least one carries through.
        carrier_unreachable = any(
            "legacy_unused.cc" in p
            for p in _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        )
        assert "GenuinelyDead" in all_dead_names or carrier_unreachable


class TestLivePathNotFlagged:
    def test_public_header_reachable(self, cpp_report) -> None:
        unreachable = _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        assert not any("coffee/brew.h" in p for p in unreachable), (
            "brew.h is included by main.cc and brew.cc — must be reachable"
        )

    def test_types_header_reachable(self, cpp_report) -> None:
        unreachable = _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        assert not any("coffee/types.h" in p for p in unreachable), (
            "types.h is included by brew.h — must be reachable transitively"
        )

    def test_main_carrier_reachable(self, cpp_report) -> None:
        unreachable = _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        assert not any(p.endswith("app/main.cc") for p in unreachable), (
            "main.cc is the binary entry — must be rescued by main-carrier rule"
        )

    def test_implementation_file_reachable(self, cpp_report) -> None:
        unreachable = _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        assert not any("libcoffee/src/brew.cc" in p for p in unreachable), (
            "brew.cc implements brew.h — must be reachable"
        )

    def test_brewer_class_not_unused_export(self, cpp_report) -> None:
        exports = _names(cpp_report, DeadCodeKind.UNUSED_EXPORT)
        assert "Brewer" not in exports, (
            "coffee::Brewer is exported via COFFEE_EXPORT and used by main.cc "
            "— must not be flagged unused_export"
        )

    def test_gtest_fixture_class_not_unused(self, cpp_report) -> None:
        exports = _names(cpp_report, DeadCodeKind.UNUSED_EXPORT)
        assert "BrewFixture" not in exports, (
            "BrewFixture is referenced via TEST_F(BrewFixture, ...) — the "
            "GoogleTest framework_edges handler must emit a type_use rescue"
        )

    def test_test_tu_marked_entry(self, cpp_report) -> None:
        # tests/ tree is also never-flag by the Phase 3 globs, so absence
        # from unreachable_file is the assertion that matters.
        unreachable = _paths(cpp_report, DeadCodeKind.UNREACHABLE_FILE)
        assert not any("brew_test.cc" in p for p in unreachable)
