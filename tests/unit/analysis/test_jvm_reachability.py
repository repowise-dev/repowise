"""Regression tests: JVM package-aware reachability + never-flag rules.

Asserts:

- ``is_jvm_file_reachable`` rescues siblings of an imported package and
  files defining stereotype-annotated classes / ``main`` carriers;
- never-flag globs cover ``module-info.java``, ``package-info.java``,
  test source-set patterns (``apacheTest``, ``intTest``, …), and the
  Gradle / Maven generated-source roots;
- JVM contract methods (``equals``, ``hashCode``, ``readObject``,
  ``componentN``, …) are protected;
- ``_FRAMEWORK_DECORATORS`` skips Spring stereotype / JUnit / Spring
  routing decorators in both unused-export and unused-internal passes.
"""

from __future__ import annotations

import fnmatch

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.constants import (
    _FRAMEWORK_DECORATORS,
    _NEVER_FLAG_PATTERNS,
)
from repowise.core.analysis.dead_code.contract_methods import is_contract_method
from repowise.core.analysis.dead_code.jvm_reachability import (
    build_jvm_package_files,
    is_jvm_file_reachable,
)


# ---------------------------------------------------------------------------
# Graph helper
# ---------------------------------------------------------------------------


def _build_graph(nodes: dict[str, dict], edges: list | None = None) -> nx.DiGraph:
    g = nx.DiGraph()
    for name, attrs in nodes.items():
        attrs = dict(attrs)
        sym_list = attrs.pop("symbols", [])
        g.add_node(name, **attrs)
        for sym in sym_list:
            sym_id = f"{name}::{sym['name']}"
            g.add_node(sym_id, node_type="symbol", file_path=name, **sym)
            g.add_edge(name, sym_id, edge_type="defines")
    for edge in edges or []:
        if len(edge) == 3:
            g.add_edge(edge[0], edge[1], **(edge[2]))
        else:
            g.add_edge(edge[0], edge[1])
    return g


def _java_file(**overrides: object) -> dict:
    base = {
        "language": "java",
        "is_entry_point": False,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": 0,
        "symbols": [],
    }
    base.update(overrides)
    return base


def _kt_file(**overrides: object) -> dict:
    base = {
        "language": "kotlin",
        "is_entry_point": False,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": 0,
        "symbols": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# is_jvm_file_reachable
# ---------------------------------------------------------------------------


class TestJvmFileReachable:
    def test_in_degree_positive_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "pkg/A.java": _java_file(),
                "pkg/B.java": _java_file(),
            },
            edges=[("pkg/B.java", "pkg/A.java", {"edge_type": "imports"})],
        )
        pkgs = build_jvm_package_files(graph)
        assert is_jvm_file_reachable("pkg/A.java", graph, pkgs) is True

    def test_sibling_with_importer_rescues(self) -> None:
        graph = _build_graph(
            {
                "app/svc/User.java": _java_file(),
                "app/svc/Helper.java": _java_file(),
                "app/web/Controller.java": _java_file(),
            },
            edges=[
                ("app/web/Controller.java", "app/svc/User.java", {"edge_type": "imports"}),
            ],
        )
        pkgs = build_jvm_package_files(graph)
        # Helper.java has no direct importer but its sibling User.java does.
        assert is_jvm_file_reachable("app/svc/Helper.java", graph, pkgs) is True

    def test_stereotype_annotated_class_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "app/svc/UserService.java": _java_file(
                    symbols=[{
                        "name": "UserService",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": ["@Service"],
                        "language": "java",
                    }],
                ),
            },
        )
        pkgs = build_jvm_package_files(graph)
        assert is_jvm_file_reachable("app/svc/UserService.java", graph, pkgs) is True

    def test_spring_boot_application_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "app/App.java": _java_file(
                    symbols=[{
                        "name": "App",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": ["@SpringBootApplication"],
                        "language": "java",
                    }],
                ),
            },
        )
        pkgs = build_jvm_package_files(graph)
        assert is_jvm_file_reachable("app/App.java", graph, pkgs) is True

    def test_main_method_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "app/Launcher.java": _java_file(
                    symbols=[{
                        "name": "main",
                        "kind": "method",
                        "visibility": "public",
                        "language": "java",
                    }],
                ),
            },
        )
        pkgs = build_jvm_package_files(graph)
        assert is_jvm_file_reachable("app/Launcher.java", graph, pkgs) is True

    def test_fqn_qualified_annotation_recognised(self) -> None:
        graph = _build_graph(
            {
                "app/User.java": _java_file(
                    symbols=[{
                        "name": "User",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": ["@jakarta.persistence.Entity"],
                        "language": "java",
                    }],
                ),
            },
        )
        pkgs = build_jvm_package_files(graph)
        assert is_jvm_file_reachable("app/User.java", graph, pkgs) is True

    def test_orphan_non_annotated_class_is_unreachable(self) -> None:
        graph = _build_graph(
            {
                "lib/Dead.java": _java_file(
                    symbols=[{
                        "name": "Dead",
                        "kind": "class",
                        "visibility": "public",
                        "language": "java",
                    }],
                ),
            },
        )
        pkgs = build_jvm_package_files(graph)
        assert is_jvm_file_reachable("lib/Dead.java", graph, pkgs) is False

    def test_kotlin_object_with_companion_main_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "app/Main.kt": _kt_file(
                    symbols=[{
                        "name": "main",
                        "kind": "function",
                        "visibility": "public",
                        "language": "kotlin",
                    }],
                ),
            },
        )
        pkgs = build_jvm_package_files(graph)
        assert is_jvm_file_reachable("app/Main.kt", graph, pkgs) is True


# ---------------------------------------------------------------------------
# Never-flag patterns
# ---------------------------------------------------------------------------


def _matches_any(path: str) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)


class TestJvmNeverFlag:
    def test_module_info_never_flagged(self) -> None:
        assert _matches_any("app/src/main/java/module-info.java")
        assert _matches_any("module-info.java")

    def test_package_info_never_flagged(self) -> None:
        assert _matches_any("lib/src/main/java/com/example/package-info.java")

    def test_caffeine_test_source_sets_never_flagged(self) -> None:
        # These exact paths surface as false positives in the baseline.
        assert _matches_any(
            "caffeine/src/apacheTest/java/com/foo/CacheTest.java"
        )
        assert _matches_any(
            "caffeine/src/eclipseTest/java/com/foo/SuiteTest.java"
        )
        assert _matches_any(
            "caffeine/src/frayTest/java/com/foo/SomeFrayTest.java"
        )

    def test_integration_test_source_set(self) -> None:
        assert _matches_any("module/src/integrationTest/java/com/foo/IT.java")
        assert _matches_any("module/src/it/java/com/foo/IT.java")

    def test_test_suffix_files(self) -> None:
        assert _matches_any("UserServiceTest.java")
        assert _matches_any("PluginIT.java")
        assert _matches_any("WidgetSpec.kt")

    def test_generated_roots_never_flagged(self) -> None:
        assert _matches_any("module/build/generated/foo/Bar.java")
        assert _matches_any(
            "module/build/generated/source/kapt/main/com/foo/Generated.java"
        )
        assert _matches_any("module/target/generated-sources/Q_User.java")

    def test_ordinary_main_source_not_flagged(self) -> None:
        # Don't accidentally never-flag normal main source.
        assert not _matches_any("module/src/main/java/com/foo/Helper.java")
        assert not _matches_any("module/src/main/kotlin/com/foo/Helper.kt")


# ---------------------------------------------------------------------------
# Contract methods
# ---------------------------------------------------------------------------


class TestJvmContractMethods:
    def test_equals_hashcode_protected(self) -> None:
        assert is_contract_method("equals", "method", "java")
        assert is_contract_method("hashCode", "method", "java")
        assert is_contract_method("toString", "method", "java")

    def test_serialization_protected(self) -> None:
        for n in ("readObject", "writeObject", "readResolve", "writeReplace"):
            assert is_contract_method(n, "method", "java")

    def test_kotlin_data_class_synthesised_protected(self) -> None:
        assert is_contract_method("component1", "method", "kotlin")
        assert is_contract_method("component12", "method", "kotlin")
        assert is_contract_method("copy", "method", "kotlin")

    def test_enum_helpers_protected(self) -> None:
        assert is_contract_method("values", "method", "java")
        assert is_contract_method("valueOf", "method", "java")

    def test_unrelated_language_not_protected(self) -> None:
        assert not is_contract_method("equals", "method", "python")


# ---------------------------------------------------------------------------
# Framework decorators recognised
# ---------------------------------------------------------------------------


class TestJvmFrameworkDecorators:
    def test_spring_stereotypes_present(self) -> None:
        for d in ("Component", "Service", "Repository", "RestController",
                  "SpringBootApplication", "Configuration"):
            assert d in _FRAMEWORK_DECORATORS

    def test_routing_annotations_present(self) -> None:
        for d in ("GetMapping", "PostMapping", "RequestMapping",
                  "MessageMapping", "ExceptionHandler"):
            assert d in _FRAMEWORK_DECORATORS

    def test_test_markers_present(self) -> None:
        for d in ("Test", "ParameterizedTest", "BeforeEach", "AfterAll"):
            assert d in _FRAMEWORK_DECORATORS

    def test_lifecycle_and_messaging_present(self) -> None:
        for d in ("PostConstruct", "PreDestroy", "EventListener",
                  "Scheduled", "KafkaListener"):
            assert d in _FRAMEWORK_DECORATORS


# ---------------------------------------------------------------------------
# End-to-end via DeadCodeAnalyzer
# ---------------------------------------------------------------------------


class TestJvmEndToEnd:
    def test_service_annotated_class_not_flagged_unreachable(self) -> None:
        graph = _build_graph(
            {
                "app/svc/UserService.java": _java_file(
                    symbols=[{
                        "name": "UserService",
                        "kind": "class",
                        "visibility": "public",
                        "decorators": ["@Service"],
                        "language": "java",
                    }],
                ),
            },
        )
        analyzer = DeadCodeAnalyzer(graph)
        report = analyzer.analyze({"detect_unused_exports": False,
                                   "detect_unused_internals": False,
                                   "detect_zombie_packages": False})
        kinds = [f.kind for f in report.findings]
        assert DeadCodeKind.UNREACHABLE_FILE not in kinds

    def test_module_info_not_flagged(self) -> None:
        graph = _build_graph(
            {
                "lib/src/main/java/module-info.java": _java_file(),
            },
        )
        analyzer = DeadCodeAnalyzer(graph)
        report = analyzer.analyze({"detect_unused_exports": False,
                                   "detect_unused_internals": False,
                                   "detect_zombie_packages": False})
        kinds = [f.kind for f in report.findings]
        assert DeadCodeKind.UNREACHABLE_FILE not in kinds

    def test_private_post_construct_method_not_flagged_internal(self) -> None:
        # A private @PostConstruct method has no caller in source, but the
        # container invokes it. Should NOT be flagged unused_internal.
        graph = _build_graph(
            {
                "app/svc/UserService.java": _java_file(
                    symbols=[{
                        "name": "init",
                        "kind": "method",
                        "visibility": "private",
                        "decorators": ["@PostConstruct"],
                        "language": "java",
                    }],
                ),
            },
        )
        analyzer = DeadCodeAnalyzer(graph)
        report = analyzer.analyze({"detect_unused_exports": False,
                                   "detect_zombie_packages": False,
                                   "detect_unreachable_files": False,
                                   "detect_unused_internals": True})
        # The init method should not surface as unused_internal.
        kinds = [(f.kind, f.symbol_name) for f in report.findings]
        assert (DeadCodeKind.UNUSED_INTERNAL, "init") not in kinds
