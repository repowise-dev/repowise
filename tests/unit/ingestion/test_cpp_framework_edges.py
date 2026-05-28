"""Unit tests for the GoogleTest / Catch2 / Boost.Test / doctest / Google
Benchmark / libFuzzer framework_edges handler."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str, language: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for src in list(repo.rglob("*.cc")) + list(repo.rglob("*.cpp")) + list(repo.rglob("*.h")) + list(repo.rglob("*.hpp")):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(src.resolve()), "cpp")
        out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem = Path(p).stem.lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


def _graph_with_nodes(parsed: dict[str, ParsedFile]) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in parsed:
        g.add_node(p)
    return g


class TestGoogleTestFixtureEdges:
    def test_test_f_emits_fixture_edge(self, tmp_path: Path) -> None:
        (tmp_path / "fixture.h").write_text(
            "#pragma once\n"
            "#include <gtest/gtest.h>\n"
            "class BrewFixture : public ::testing::Test {};\n"
        )
        (tmp_path / "brew_test.cc").write_text(
            "#include <gtest/gtest.h>\n"
            '#include "fixture.h"\n'
            "TEST_F(BrewFixture, BrewsCoffee) { EXPECT_TRUE(true); }\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["gtest"])
        assert graph.has_edge("brew_test.cc", "fixture.h")
        assert graph.nodes["brew_test.cc"].get("is_entry_point") is True

    def test_typed_test_fixture_edge(self, tmp_path: Path) -> None:
        (tmp_path / "ty_fix.h").write_text(
            "#pragma once\n#include <gtest/gtest.h>\n"
            "template <class T> class MyTyped : public ::testing::Test {};\n"
        )
        (tmp_path / "typed_test.cc").write_text(
            "#include <gtest/gtest.h>\n"
            '#include "ty_fix.h"\n'
            "TYPED_TEST(MyTyped, RunsIt) {}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["gtest"])
        assert graph.has_edge("typed_test.cc", "ty_fix.h")

    def test_plain_test_marks_entry_no_fixture_edge(self, tmp_path: Path) -> None:
        (tmp_path / "plain_test.cc").write_text(
            "#include <gtest/gtest.h>\n"
            "TEST(Suite, ExampleCase) { EXPECT_EQ(1, 1); }\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["gtest"])
        assert graph.nodes["plain_test.cc"].get("is_entry_point") is True


class TestBoostAndCatch2Fixtures:
    def test_boost_fixture_test_case(self, tmp_path: Path) -> None:
        (tmp_path / "btx.h").write_text(
            "#pragma once\n#include <boost/test/unit_test.hpp>\n"
            "struct DbFixture { int x = 0; };\n"
        )
        (tmp_path / "bt_test.cc").write_text(
            "#include <boost/test/unit_test.hpp>\n"
            '#include "btx.h"\n'
            "BOOST_FIXTURE_TEST_CASE(MyCase, DbFixture) { BOOST_CHECK(x == 0); }\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["boost"])
        assert graph.has_edge("bt_test.cc", "btx.h")

    def test_catch2_test_case_method(self, tmp_path: Path) -> None:
        (tmp_path / "cfix.h").write_text(
            "#pragma once\n#include <catch2/catch_test_macros.hpp>\n"
            "class Cooked { public: int y = 0; };\n"
        )
        (tmp_path / "c_test.cc").write_text(
            "#include <catch2/catch_test_macros.hpp>\n"
            '#include "cfix.h"\n'
            'TEST_CASE_METHOD(Cooked, "warms up") { REQUIRE(y == 0); }\n'
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["catch2"])
        assert graph.has_edge("c_test.cc", "cfix.h")


class TestBenchmarkAndFuzzer:
    def test_benchmark_main_marks_entry(self, tmp_path: Path) -> None:
        (tmp_path / "bench.cc").write_text(
            "#include <benchmark/benchmark.h>\n"
            "static void BM_X(benchmark::State& s) { for (auto _ : s); }\n"
            "BENCHMARK(BM_X);\n"
            "BENCHMARK_MAIN();\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["benchmark"])
        assert graph.nodes["bench.cc"].get("is_entry_point") is True

    def test_libfuzzer_entry(self, tmp_path: Path) -> None:
        (tmp_path / "fuzz.cc").write_text(
            "#include <cstdint>\n"
            'extern "C" int LLVMFuzzerTestOneInput(const uint8_t* d, size_t s) { return 0; }\n'
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx)
        assert graph.nodes["fuzz.cc"].get("is_entry_point") is True


class TestNonTestFilesUnaffected:
    def test_plain_cpp_not_marked(self, tmp_path: Path) -> None:
        (tmp_path / "lib.cc").write_text(
            "int add(int a, int b) { return a + b; }\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx)
        assert graph.nodes["lib.cc"].get("is_entry_point") is None
