"""End-to-end JVM dead-code regression test for the jvm_sample fixture.

Runs the full FileTraverser -> ASTParser -> GraphBuilder -> DeadCodeAnalyzer
pipeline against tests/fixtures/jvm_sample and proves the JVM-parity work
(Phases 1-4) holds together end-to-end across a multi-module Gradle layout
mixing Java + Kotlin + Spring Boot + Lombok + JPA + Spring Data +
META-INF/services + JPMS provides...with + a non-main integrationTest
source set:

- the one planted genuinely-dead class is flagged,
- module-info.java / package-info.java are never flagged,
- every framework- or convention-loaded class is reached,
- same-package implicit access keeps internal siblings reachable,
- the integrationTest source set is reachable.

This is the regression guard that keeps the JVM analyzer honest: if a future
change over-suppresses (the live assertions hold but the dead assertions
fail) or regresses to false positives (the dead assertions hold but a live
assertion fails), this test breaks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

JVM_SAMPLE = Path(__file__).parent.parent / "fixtures" / "jvm_sample"


@pytest.fixture(scope="module")
def jvm_report():
    traverser = FileTraverser(JVM_SAMPLE)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=JVM_SAMPLE)

    for fi in traverser.traverse():
        source = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source))

    graph = builder.build()

    # Mirror the production pipeline: framework + dynamic edges after build.
    builder.add_framework_edges([])
    try:
        from repowise.core.ingestion.dynamic_hints import HintRegistry

        registry = HintRegistry()
        builder.add_dynamic_edges(registry.extract_all(JVM_SAMPLE))
    except Exception:
        pass

    analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
    report = analyzer.analyze({"min_confidence": 0.0})
    return report


def _names(report, kind) -> set[str]:
    return {f.symbol_name for f in report.findings if f.kind == kind}


def _paths(report, kind) -> set[str]:
    return {f.file_path for f in report.findings if f.kind == kind}


class TestJvmSampleHonesty:
    def test_planted_dead_file_is_unreachable(self, jvm_report) -> None:
        unreachable = _paths(jvm_report, DeadCodeKind.UNREACHABLE_FILE)
        assert any("dead/ObviouslyDead.java" in p for p in unreachable), (
            "ObviouslyDead.java should be flagged unreachable — it has no "
            "importer, no framework annotation, no main, no META-INF entry"
        )

    def test_planted_dead_export_is_flagged(self, jvm_report) -> None:
        exports = _names(jvm_report, DeadCodeKind.UNUSED_EXPORT)
        assert "deadGreeting" in exports or "ObviouslyDead" in exports


class TestJvmSampleNoFalsePositives:
    def test_module_info_and_package_info_never_flagged(self, jvm_report) -> None:
        unreachable = _paths(jvm_report, DeadCodeKind.UNREACHABLE_FILE)
        for never_flag in ("module-info.java", "package-info.java"):
            assert not any(p.endswith(never_flag) for p in unreachable), (
                f"{never_flag} should never be flagged unreachable"
            )

    def test_spring_boot_entry_point_reachable(self, jvm_report) -> None:
        unreachable = _paths(jvm_report, DeadCodeKind.UNREACHABLE_FILE)
        assert not any("SampleApplication.java" in p for p in unreachable)

    def test_spring_stereotype_files_reachable(self, jvm_report) -> None:
        unreachable = _paths(jvm_report, DeadCodeKind.UNREACHABLE_FILE)
        for live in (
            "web/UserController.java",
            "svc/UserService.kt",
            "svc/UserRepository.java",
            "model/User.java",
        ):
            assert not any(live in p for p in unreachable), (
                f"{live} wrongly unreachable — Spring stereotype / "
                "JpaRepository should rescue it"
            )

    def test_jpa_repository_methods_not_dead_exports(self, jvm_report) -> None:
        exports = _names(jvm_report, DeadCodeKind.UNUSED_EXPORT)
        # Derived-query methods on a Spring Data repository are entry
        # points — Spring synthesizes implementations at runtime.
        assert "findByEmailAndStatus" not in exports

    def test_service_loader_and_jpms_provides_rescue_my_plugin(self, jvm_report) -> None:
        unreachable = _paths(jvm_report, DeadCodeKind.UNREACHABLE_FILE)
        assert not any("lib/MyPlugin.java" in p for p in unreachable), (
            "MyPlugin should be reachable via META-INF/services and "
            "JPMS provides...with"
        )

    def test_same_package_sibling_reachable(self, jvm_report) -> None:
        unreachable = _paths(jvm_report, DeadCodeKind.UNREACHABLE_FILE)
        # HelperUser has no explicit importer; it should be reachable via
        # the package fan-out from MyPlugin's import of Helper.
        assert not any("internal/HelperUser.java" in p for p in unreachable)
        assert not any("internal/Helper.java" in p for p in unreachable)

    def test_integration_test_source_set_reachable(self, jvm_report) -> None:
        unreachable = _paths(jvm_report, DeadCodeKind.UNREACHABLE_FILE)
        exports = _names(jvm_report, DeadCodeKind.UNUSED_EXPORT)
        assert not any("PluginIT.java" in p for p in unreachable), (
            "integrationTest source-set files must be never-flagged"
        )
        assert "loadsPlugin" not in exports

    def test_kotlin_cross_language_reachable(self, jvm_report) -> None:
        # UserService.kt calls User.empty() (Java) and is called from
        # UserController.java — cross-language resolution should keep
        # both sides reachable (covered above) and the Kotlin file's
        # exported class shouldn't be flagged unused.
        exports = _names(jvm_report, DeadCodeKind.UNUSED_EXPORT)
        assert "UserService" not in exports
