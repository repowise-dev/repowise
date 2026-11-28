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
from pathlib import Path

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

    def test_co_change_is_not_an_importer(self) -> None:
        # Committed together is history: no include reaches the file or its sibling.
        graph = _build_graph(
            {
                "a/foo.cc": _cpp_file(),
                "a/foo_impl.cc": _cpp_file(),
                "b/bar.cc": _cpp_file(),
            },
            edges=[
                ("b/bar.cc", "a/foo.cc", {"edge_type": "co_changes"}),
                ("b/bar.cc", "a/foo_impl.cc", {"edge_type": "co_changes"}),
            ],
        )
        pkgs = build_cpp_package_files(graph)
        assert is_cpp_file_reachable("a/foo.cc", graph, pkgs) is False
        assert is_cpp_file_reachable("a/foo_impl.cc", graph, pkgs) is False

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

    def test_a_large_directory_reads_each_sibling_once(self, monkeypatch) -> None:
        # Every importer-less file asks about the same siblings; reading them
        # per file made a 1,000-file directory cost a million in-edge scans.
        from repowise.core.analysis.dead_code import file_reachability

        n = 300
        graph = _build_graph(
            {f"src/Functions/f{i}.cpp": _cpp_file() for i in range(n)}
            | {"src/Functions/keyed_a.cpp": _cpp_file(), "src/Functions/keyed_b.cpp": _cpp_file()},
            edges=[("src/Functions/f0.cpp", "src/Functions/keyed_a.cpp", {"edge_type": "imports"})],
        )
        pkgs = build_cpp_package_files(graph)
        calls = 0
        real = file_reachability.has_dependency_importer

        def counting(g, path):
            nonlocal calls
            calls += 1
            return real(g, path)

        monkeypatch.setattr(file_reachability, "has_dependency_importer", counting)
        answers = {f: is_cpp_file_reachable(f, graph, pkgs) for f in pkgs["src/Functions"]}
        assert calls <= 2 * (n + 2)
        # Same answers as a plain map, which reads the directory per call.
        assert answers == {f: is_cpp_file_reachable(f, graph, dict(pkgs)) for f in answers}
        assert answers["src/Functions/keyed_b.cpp"] is True  # conditional pair of keyed_a
        assert answers["src/Functions/f1.cpp"] is False


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


def test_build_listed_cpp_file_is_reachable() -> None:
    # Issue 3820: a file stamped build_listed is reachable
    from repowise.core.analysis.dead_code.cpp_reachability import is_cpp_file_reachable

    g = nx.DiGraph()
    g.add_node("WorkspacesLib/SteamGameHelper.cpp", build_listed=True, language="cpp")
    g.add_node("Orphan.cpp", build_listed=False, language="cpp")

    assert is_cpp_file_reachable("WorkspacesLib/SteamGameHelper.cpp", g, {}) is True
    assert is_cpp_file_reachable("Orphan.cpp", g, {}) is False


def test_stamp_cpp_build_listed_ignores_headers(tmp_path: Path) -> None:
    # Review finding: headers in CMake targets or vcxproj ClInclude are not compilation units
    from unittest.mock import MagicMock

    from repowise.core.ingestion.graph_warmups import _stamp_cpp_build_listed
    from repowise.core.ingestion.resolvers import ResolverContext

    (tmp_path / "App.vcxproj").write_text(
        '<Project>\n'
        '  <ItemGroup>\n'
        '    <ClCompile Include="src/main.cpp" />\n'
        '    <ClInclude Include="include/stale.h" />\n'
        '  </ItemGroup>\n'
        '</Project>\n'
    )
    (tmp_path / "app.rc").write_text('#include "include/branding.h"\n')

    g = nx.DiGraph()
    g.add_node("src/main.cpp")
    g.add_node("include/stale.h")
    g.add_node("include/branding.h")
    g.add_node("cmake_src.cpp")
    g.add_node("cmake_hdr.h")

    target = MagicMock()
    target.sources = ("cmake_src.cpp",)
    target.public_headers = ("cmake_hdr.h",)
    target.private_headers = ()
    index = MagicMock()
    index.targets = {"t": target}

    ctx = ResolverContext(path_set=set(), stem_map={}, graph=None, repo_path=tmp_path)
    _stamp_cpp_build_listed(ctx, g, index)

    assert g.nodes["src/main.cpp"].get("build_listed") is True
    assert g.nodes["cmake_src.cpp"].get("build_listed") is True
    assert g.nodes["include/branding.h"].get("build_listed") is True
    assert "build_listed" not in g.nodes["include/stale.h"]
    assert "build_listed" not in g.nodes["cmake_hdr.h"]


def test_header_only_target_header_rescued_by_another_member() -> None:
    g = nx.DiGraph()
    stamp = {"language": "cpp", "cpp_header_only_targets": ("cmake:lib",)}
    g.add_node("include/lib/core.h", **stamp)
    g.add_node("include/lib/detail/format.h", **stamp)
    g.add_node("app/main.cc", language="cpp")
    g.add_edge("app/main.cc", "include/lib/core.h", edge_type="imports")
    packages = build_cpp_package_files(g)
    assert is_cpp_file_reachable("include/lib/detail/format.h", g, packages) is True


def test_header_only_target_with_no_importer_rescues_nothing() -> None:
    g = nx.DiGraph()
    stamp = {"language": "cpp", "cpp_header_only_targets": ("cmake:lib",)}
    g.add_node("include/lib/core.h", **stamp)
    g.add_node("include/lib/detail/format.h", **stamp)
    # An imported header of another target, in another directory.
    g.add_node("other/api.h", language="cpp")
    g.add_node("app/main.cc", language="cpp")
    g.add_edge("app/main.cc", "other/api.h", edge_type="imports")
    packages = build_cpp_package_files(g)
    assert is_cpp_file_reachable("include/lib/detail/format.h", g, packages) is False


def test_header_only_target_imported_only_by_its_own_headers_rescues_nothing() -> None:
    g = nx.DiGraph()
    stamp = {"language": "cpp", "cpp_header_only_targets": ("cmake:lib",)}
    for node in ("include/lib/a.h", "include/lib/b/b.h", "include/lib/c/c.h"):
        g.add_node(node, **stamp)
    # a.h includes b.h; nothing outside the library includes either.
    g.add_edge("include/lib/a.h", "include/lib/b/b.h", edge_type="imports")
    packages = build_cpp_package_files(g)
    assert is_cpp_file_reachable("include/lib/c/c.h", g, packages) is False
    assert is_cpp_file_reachable("include/lib/a.h", g, packages) is False


def test_stamp_marks_headers_of_header_only_targets(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from repowise.core.ingestion.graph_warmups import _stamp_cpp_build_listed
    from repowise.core.ingestion.resolvers import ResolverContext

    g = nx.DiGraph()
    for node in ("lib/a.h", "lib/sub/b.h", "core/c.h", "core/c.cc"):
        g.add_node(node)
    index = SimpleNamespace(
        targets={
            "hdr": SimpleNamespace(
                sources=(), public_headers=("lib/a.h",), private_headers=("lib/sub/b.h",)
            ),
            "core": SimpleNamespace(
                sources=("core/c.cc",), public_headers=("core/c.h",), private_headers=()
            ),
        }
    )
    ctx = ResolverContext(path_set=set(), stem_map={}, graph=None, repo_path=tmp_path)
    _stamp_cpp_build_listed(ctx, g, index)
    assert g.nodes["lib/a.h"]["cpp_header_only_targets"] == ("hdr",)
    assert g.nodes["lib/sub/b.h"]["cpp_header_only_targets"] == ("hdr",)
    assert "cpp_header_only_targets" not in g.nodes["core/c.h"]


def test_stamp_replaces_what_a_seeded_node_carried(tmp_path: Path) -> None:
    # A patch build seeds untouched nodes from the last snapshot, stamps included.
    from types import SimpleNamespace

    from repowise.core.ingestion.graph_warmups import _stamp_cpp_build_listed
    from repowise.core.ingestion.resolvers import ResolverContext

    g = nx.DiGraph()
    g.add_node("lib/a.h", cpp_header_only_targets=("hdr",))
    g.add_node("lib/sub/b.h", cpp_header_only_targets=("hdr",))  # has left the target
    g.add_node("old/gone.cc", build_listed=True)  # no longer compiled by any target
    index = SimpleNamespace(
        targets={"hdr": SimpleNamespace(sources=(), public_headers=("lib/a.h",), private_headers=())}
    )
    ctx = ResolverContext(path_set=set(), stem_map={}, graph=None, repo_path=tmp_path)
    for _ in range(2):
        _stamp_cpp_build_listed(ctx, g, index)
    assert g.nodes["lib/a.h"]["cpp_header_only_targets"] == ("hdr",)
    assert "cpp_header_only_targets" not in g.nodes["lib/sub/b.h"]
    assert "build_listed" not in g.nodes["old/gone.cc"]


def test_patch_build_header_stamps_match_a_full_build(tmp_path: Path) -> None:
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    def write(rel: str, text: str) -> None:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)

    def parsed_builder() -> GraphBuilder:
        builder = GraphBuilder(repo_path=tmp_path)
        parser = ASTParser()
        for fi in FileTraverser(tmp_path).traverse():
            builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
        return builder

    def stamps(graph: nx.DiGraph) -> dict:
        return {n: d.get("cpp_header_only_targets") for n, d in graph.nodes(data=True)}

    cmake = (
        "add_library(hdr INTERFACE)\n"
        "target_sources(hdr INTERFACE include/hdr/core.h {extra})\n"
        "target_include_directories(hdr INTERFACE include)\n"
    )
    write("CMakeLists.txt", cmake.format(extra="include/hdr/detail/format.h"))
    write("include/hdr/core.h", "int core_fn();\n")
    write("include/hdr/detail/format.h", "int format_fn();\n")
    write("app/main.cc", '#include "hdr/core.h"\nint main() { return core_fn(); }\n')
    first = parsed_builder().build()
    snapshot = {"nodes": list(first.nodes(data=True)), "edges": list(first.edges(data=True))}

    # format.h leaves the library; the patch re-resolves only main.cc.
    write("CMakeLists.txt", cmake.format(extra=""))
    patched = parsed_builder()
    patched.seed_graph_snapshot(snapshot, scope=["app/main.cc"])
    patch_graph = patched.build(scope=["app/main.cc"], refresh_global=True)
    full_graph = parsed_builder().build()

    assert stamps(patch_graph) == stamps(full_graph)
    assert len(patch_graph.nodes["include/hdr/core.h"]["cpp_header_only_targets"]) == 1
    assert "cpp_header_only_targets" not in patch_graph.nodes["include/hdr/detail/format.h"]


def _index_repo(repo: Path, files: dict[str, str]):
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    builder = GraphBuilder(repo_path=repo)
    parser = ASTParser()
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    report = DeadCodeAnalyzer(graph, parsed_files=builder._parsed_files).analyze(
        {"min_confidence": 0.0}
    )
    return graph, report


def test_including_a_target_header_links_no_other_target_source(tmp_path: Path) -> None:
    graph, report = _index_repo(
        tmp_path,
        {
            "CMakeLists.txt": "add_library(engine STATIC src/world.cpp src/sound.cpp src/weather.cpp)\n"
            "target_include_directories(engine PUBLIC src)\n",
            "src/world.hpp": "struct World { int tick(); };\n",
            "src/world.cpp": '#include "world.hpp"\nint World::tick() { return 1; }\n',
            "src/sound.cpp": "int play() { return 2; }\n",
            "src/weather.cpp": "int rain() { return 3; }\n",
            "app/main.cpp": '#include "world.hpp"\nint main() { World w; return w.tick(); }\n',
        },
    )
    for importer in ("app/main.cpp", "src/world.cpp"):
        targets = {
            t for _, t, d in graph.out_edges(importer, data=True) if d.get("edge_type") == "imports"
        }
        assert "src/sound.cpp" not in targets and "src/weather.cpp" not in targets
    unreachable = {f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}
    # Listed in the target, so still reachable without the fake edges.
    assert not unreachable & {"src/sound.cpp", "src/weather.cpp"}


def test_header_only_library_across_directories_stays_reachable(tmp_path: Path) -> None:
    _, report = _index_repo(
        tmp_path,
        {
            "CMakeLists.txt": "add_library(hdr INTERFACE)\n"
            "target_sources(hdr INTERFACE include/hdr/core.h include/hdr/detail/format.h)\n"
            "target_include_directories(hdr INTERFACE include)\n",
            "include/hdr/core.h": "int core_fn();\n",
            "include/hdr/detail/format.h": "int format_fn();\n",
            "app/main.cc": '#include "hdr/core.h"\nint main() { return core_fn(); }\n',
        },
    )
    unreachable = {f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}
    assert "include/hdr/detail/format.h" not in unreachable
