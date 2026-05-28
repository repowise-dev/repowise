"""Regression tests: C/C++ directory-aware reachability + never-flag rules.

Asserts:

- ``is_cpp_file_reachable`` rescues ``int main`` / ``WinMain`` / fuzzer
  carriers, public-API headers whose symbols are referenced, internal
  headers next to their implementation, and conditional-compile
  alternates that share a stem prefix;
- never-flag globs cover ``apps/``, ``demos/``, ``examples/``,
  ``benchmarks/``, ``tools/``, ``tests/{perf,fuzz,unit,manual}`` trees,
  generated source patterns (``moc_*.cpp``, ``*.pb.cc``,
  ``*.grpc.pb.cc``, ``*.tab.c``, ``*_wrap.cxx``), conventional port
  example headers, and the broader ``external/extern/contrib/submodules``
  vendor roots;
- C/C++ contract methods (constructors, destructors, operator
  overloads, conversion operators, STL CPOs, coroutine machinery) are
  protected from the unused-export / unused-internal passes;
- ``LLVMFuzzerTestOneInput`` is recognised as an entry-point symbol
  name.
"""

from __future__ import annotations

import fnmatch

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.analyzer import _ENTRY_POINT_SYMBOL_NAMES
from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
from repowise.core.analysis.dead_code.contract_methods import is_contract_method
from repowise.core.analysis.dead_code.cpp_reachability import (
    build_cpp_package_files,
    is_cpp_file_reachable,
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


def _cpp_file(**overrides: object) -> dict:
    base = {
        "language": "cpp",
        "is_entry_point": False,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": 0,
        "symbols": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# is_cpp_file_reachable
# ---------------------------------------------------------------------------


class TestCppFileReachable:
    def test_in_degree_positive_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "a/foo.h": _cpp_file(),
                "a/foo.cc": _cpp_file(),
            },
            edges=[("a/foo.cc", "a/foo.h", {"edge_type": "imports"})],
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("a/foo.h", graph, pkgs) is True

    def test_main_carrier_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "apps/runner/main.cc": _cpp_file(
                    symbols=[{
                        "name": "main",
                        "kind": "function",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("apps/runner/main.cc", graph, pkgs) is True

    def test_winmain_carrier_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "src/gui/app.cpp": _cpp_file(
                    symbols=[{
                        "name": "WinMain",
                        "kind": "function",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("src/gui/app.cpp", graph, pkgs) is True

    def test_libfuzzer_entry_carrier_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "fuzz/parse_fuzz.cc": _cpp_file(
                    symbols=[{
                        "name": "LLVMFuzzerTestOneInput",
                        "kind": "function",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("fuzz/parse_fuzz.cc", graph, pkgs) is True

    def test_header_with_referenced_symbol_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "include/leveldb/cache.h": _cpp_file(
                    symbols=[{
                        "name": "Cache",
                        "kind": "class",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
                "src/consumer.cc": _cpp_file(
                    symbols=[{
                        "name": "use_it",
                        "kind": "function",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
            edges=[
                ("src/consumer.cc::use_it", "include/leveldb/cache.h::Cache",
                 {"edge_type": "type_use"}),
            ],
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable(
            "include/leveldb/cache.h", graph, pkgs
        ) is True

    def test_header_with_implements_edge_is_reachable(self) -> None:
        graph = _build_graph(
            {
                "include/iface.hpp": _cpp_file(
                    symbols=[{
                        "name": "IFace",
                        "kind": "interface",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
                "src/impl.cc": _cpp_file(
                    symbols=[{
                        "name": "Impl",
                        "kind": "class",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
            edges=[
                ("src/impl.cc::Impl", "include/iface.hpp::IFace",
                 {"edge_type": "implements"}),
            ],
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("include/iface.hpp", graph, pkgs) is True

    def test_internal_header_rescued_by_sibling_with_importer(self) -> None:
        # builder.h has no direct importer, but builder.cc next to it does.
        graph = _build_graph(
            {
                "db/builder.h": _cpp_file(),
                "db/builder.cc": _cpp_file(),
                "db/version_set.cc": _cpp_file(),
            },
            edges=[
                ("db/version_set.cc", "db/builder.cc", {"edge_type": "imports"}),
            ],
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("db/builder.h", graph, pkgs) is True

    def test_conditional_alt_pair_rescued(self) -> None:
        # env_posix.cc has an importer; env_windows.cc does not but is the
        # mutually-exclusive conditional alternative.
        graph = _build_graph(
            {
                "util/env_posix.cc": _cpp_file(),
                "util/env_windows.cc": _cpp_file(),
                "util/consumer.cc": _cpp_file(),
            },
            edges=[
                ("util/consumer.cc", "util/env_posix.cc", {"edge_type": "imports"}),
            ],
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("util/env_windows.cc", graph, pkgs) is True

    def test_sibling_of_main_file_is_reachable(self) -> None:
        # An apps directory: main.cc carries main(); helper.cc next to it
        # has no importer but is part of the same binary.
        graph = _build_graph(
            {
                "apps/runner/main.cc": _cpp_file(
                    symbols=[{
                        "name": "main",
                        "kind": "function",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
                "apps/runner/helper.cc": _cpp_file(),
            },
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable(
            "apps/runner/helper.cc", graph, pkgs
        ) is True

    def test_orphan_unrelated_header_is_unreachable(self) -> None:
        # A pure orphan header with no symbol references and no live
        # siblings should still surface as dead.
        graph = _build_graph(
            {
                "lib/legacy.h": _cpp_file(
                    symbols=[{
                        "name": "Legacy",
                        "kind": "class",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("lib/legacy.h", graph, pkgs) is False

    def test_orphan_source_with_no_sibling_signal_is_unreachable(self) -> None:
        graph = _build_graph(
            {
                "lib/dead.cc": _cpp_file(),
            },
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("lib/dead.cc", graph, pkgs) is False


# ---------------------------------------------------------------------------
# Never-flag patterns
# ---------------------------------------------------------------------------


def _matches_any(path: str) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)


class TestCppNeverFlag:
    def test_apps_tree_never_flagged(self) -> None:
        assert _matches_any("apps/io_tester/io_tester.cc")
        assert _matches_any("seastar/apps/memcached/memcache.cc")
        assert _matches_any("apps/runner/main.cpp")

    def test_demos_tree_never_flagged(self) -> None:
        assert _matches_any("demos/echo_demo.cc")
        assert _matches_any("seastar/demos/hello_demo.cpp")

    def test_examples_tree_never_flagged(self) -> None:
        assert _matches_any("examples/hello.cc")
        assert _matches_any("project/examples/snippet.cpp")
        assert _matches_any("examples/dir/sub/nested.cc")

    def test_benchmarks_tree_never_flagged(self) -> None:
        assert _matches_any("benchmarks/db_bench.cc")
        assert _matches_any("project/benchmarks/throughput.cpp")

    def test_tools_tree_never_flagged(self) -> None:
        assert _matches_any("tools/leveldbutil.cc")

    def test_tests_perf_fuzz_unit_manual_never_flagged(self) -> None:
        assert _matches_any("seastar/tests/perf/allocator_perf.cc")
        assert _matches_any("seastar/tests/fuzz/sstring_fuzz.cc")
        assert _matches_any("seastar/tests/unit/metrics_tester.cc")
        assert _matches_any("seastar/tests/manual/io-trace-parse.py")

    def test_test_suffix_files_never_flagged(self) -> None:
        assert _matches_any("foo_test.cc")
        assert _matches_any("nested/dir/bar_unittest.cpp")
        assert _matches_any("baz_perf.cc")
        assert _matches_any("qux_benchmark.cpp")
        assert _matches_any("net_fuzz.cc")

    def test_port_example_never_flagged(self) -> None:
        assert _matches_any("port/port_example.h")
        assert _matches_any("leveldb/port/port_example.h")

    def test_generated_qt_protobuf_grpc_globs(self) -> None:
        assert _matches_any("moc_widget.cpp")
        assert _matches_any("ui_widget.h")
        assert _matches_any("qrc_resources.cpp")
        assert _matches_any("api.pb.cc")
        assert _matches_any("api.pb.h")
        assert _matches_any("api.grpc.pb.cc")
        assert _matches_any("api.grpc.pb.h")

    def test_generated_yacc_flex_swig_globs(self) -> None:
        assert _matches_any("parser.tab.c")
        assert _matches_any("parser.tab.h")
        assert _matches_any("lexer.yy.c")
        assert _matches_any("module_wrap.cxx")

    def test_external_extern_contrib_submodules_globs(self) -> None:
        assert _matches_any("external/spdlog/spdlog.cc")
        assert _matches_any("project/external/fmt/format.h")
        assert _matches_any("extern/zlib/zlib.h")
        assert _matches_any("contrib/jemalloc/jemalloc.c")
        assert _matches_any("submodules/anything/x")

    def test_ordinary_source_not_flagged(self) -> None:
        # Don't accidentally never-flag normal source.
        assert not _matches_any("src/db/version_set.cc")
        assert not _matches_any("lib/foo.cpp")
        assert not _matches_any("include/proj/api.h")


# ---------------------------------------------------------------------------
# Contract methods
# ---------------------------------------------------------------------------


class TestCppContractMethods:
    def test_operator_overloads_protected(self) -> None:
        for op in ("operator==", "operator!=", "operator<", "operator<=>",
                   "operator+", "operator*", "operator()", "operator[]",
                   "operator->", "operator new", "operator delete"):
            assert is_contract_method(op, "method", "cpp"), op

    def test_stl_customization_points_protected(self) -> None:
        for n in ("begin", "end", "cbegin", "cend", "swap", "size",
                  "empty", "data", "hash_value"):
            assert is_contract_method(n, "method", "cpp"), n

    def test_coroutine_machinery_protected(self) -> None:
        for n in ("await_ready", "await_suspend", "await_resume",
                  "get_return_object", "initial_suspend", "final_suspend"):
            assert is_contract_method(n, "method", "cpp"), n

    def test_constructor_destructor_protected(self) -> None:
        # Constructor: kind=constructor.
        assert is_contract_method("MyClass", "constructor", "cpp")
        # Destructor: kind=method with leading ``~``.
        assert is_contract_method("~MyClass", "method", "cpp")
        # Or kind=destructor.
        assert is_contract_method("~MyClass", "destructor", "cpp")

    def test_conversion_operator_protected(self) -> None:
        assert is_contract_method("operator bool", "method", "cpp")
        assert is_contract_method("operator MyType", "method", "cpp")

    def test_unrelated_language_not_protected(self) -> None:
        assert not is_contract_method("operator==", "method", "python")
        assert not is_contract_method("begin", "method", "java")

    def test_random_user_function_not_protected(self) -> None:
        assert not is_contract_method("do_thing", "method", "cpp")


# ---------------------------------------------------------------------------
# Entry-point symbol names
# ---------------------------------------------------------------------------


class TestCppEntryPointSymbols:
    def test_libfuzzer_entry_recognised(self) -> None:
        assert "LLVMFuzzerTestOneInput" in _ENTRY_POINT_SYMBOL_NAMES
        assert "LLVMFuzzerInitialize" in _ENTRY_POINT_SYMBOL_NAMES

    def test_windows_main_variants_recognised(self) -> None:
        # Already covered before Phase 3; spot-check they didn't regress.
        for n in ("main", "WinMain", "wWinMain", "wmain", "DllMain"):
            assert n in _ENTRY_POINT_SYMBOL_NAMES


# ---------------------------------------------------------------------------
# End-to-end via DeadCodeAnalyzer
# ---------------------------------------------------------------------------


class TestCppEndToEnd:
    def test_main_carrier_not_flagged_unreachable(self) -> None:
        graph = _build_graph(
            {
                "src/runner.cc": _cpp_file(
                    symbols=[{
                        "name": "main",
                        "kind": "function",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
        )
        analyzer = DeadCodeAnalyzer(graph)
        report = analyzer.analyze({
            "detect_unused_exports": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
        })
        assert not any(
            f.kind == DeadCodeKind.UNREACHABLE_FILE
            and f.file_path == "src/runner.cc"
            for f in report.findings
        )

    def test_public_header_with_type_use_not_flagged_unreachable(self) -> None:
        graph = _build_graph(
            {
                "include/proj/api.h": _cpp_file(
                    symbols=[{
                        "name": "Api",
                        "kind": "class",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
                "src/use.cc": _cpp_file(
                    symbols=[{
                        "name": "client",
                        "kind": "function",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
            edges=[
                ("src/use.cc::client", "include/proj/api.h::Api",
                 {"edge_type": "type_use"}),
            ],
        )
        analyzer = DeadCodeAnalyzer(graph)
        report = analyzer.analyze({
            "detect_unused_exports": False,
            "detect_unused_internals": False,
            "detect_zombie_packages": False,
        })
        unreachable_paths = {
            f.file_path for f in report.findings
            if f.kind == DeadCodeKind.UNREACHABLE_FILE
        }
        assert "include/proj/api.h" not in unreachable_paths

    def test_operator_overload_not_flagged_unused_export(self) -> None:
        # A struct's ``operator==`` with no static caller — the compiler
        # synthesizes ``std::equal_to`` / ``std::set`` lookups, no graph
        # edge. Should not appear in unused-export findings.
        graph = _build_graph(
            {
                "lib/widget.cc": _cpp_file(
                    symbols=[{
                        "name": "operator==",
                        "kind": "method",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
            edges=[("driver.cc", "lib/widget.cc", {"edge_type": "imports"})],
        )
        graph.add_node("driver.cc", language="cpp", symbol_count=0)
        analyzer = DeadCodeAnalyzer(graph)
        report = analyzer.analyze({"min_confidence": 0.0})
        unused_export_safe = [
            f for f in report.findings
            if f.kind == DeadCodeKind.UNUSED_EXPORT
            and f.symbol_name == "operator=="
            and f.safe_to_delete
        ]
        assert not unused_export_safe

    def test_apps_tree_never_flag_short_circuits(self) -> None:
        graph = _build_graph(
            {
                "apps/io_tester/io_tester.cc": _cpp_file(
                    symbols=[{
                        "name": "context",
                        "kind": "class",
                        "visibility": "public",
                        "language": "cpp",
                    }],
                ),
            },
        )
        analyzer = DeadCodeAnalyzer(graph)
        report = analyzer.analyze({"min_confidence": 0.0})
        paths = {f.file_path for f in report.findings}
        assert "apps/io_tester/io_tester.cc" not in paths
