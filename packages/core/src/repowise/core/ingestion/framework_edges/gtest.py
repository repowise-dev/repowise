"""GoogleTest / Catch2 / Boost.Test / doctest / Google Benchmark / libFuzzer.

C++ test- and benchmark-framework recognition. The graph never carries a
static caller edge from the runtime test runner to a ``TEST(Suite, Name)``
body, so without these edges the fixture classes referenced by ``TEST_F``
and ``TEST_CASE_METHOD`` (and Boost ``BOOST_FIXTURE_TEST_CASE``) read as
``unused_export``.

The handler emits two things:

* a framework edge from the test TU to the **fixture class's** defining
  file — so a public ``class BrewFixture : public ::testing::Test {…}``
  declared in one header and used as ``TEST_F(BrewFixture, …)`` from
  another TU stays reachable;
* an ``is_entry_point=True`` stamp on the test TU's file node (the warmup
  already marks ``LLVMFuzzerTestOneInput`` carriers and the registration-
  macro TUs, but ``TEST(…)``-only and ``BENCHMARK(…)``-only TUs are not in
  that token list — emitting the flag here keeps them out of
  ``unreachable_file``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_class_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_CPP_LANGS: tuple[str, ...] = ("cpp", "c")

# Tokens whose presence in any TU means a C++ test framework is in use.
# Cheap reject path before regex work.
_TEST_FRAMEWORK_TOKENS: tuple[str, ...] = (
    "gtest/gtest.h",
    "gmock/gmock.h",
    "catch2/catch",
    "catch.hpp",
    "doctest/doctest.h",
    "doctest.h",
    "boost/test/unit_test.hpp",
    "boost/test/included",
    "benchmark/benchmark.h",
    "TEST(",
    "TEST_F(",
    "TEST_P(",
    "TYPED_TEST(",
    "TEST_CASE(",
    "TEST_CASE_METHOD(",
    "SCENARIO(",
    "DOCTEST_TEST_CASE",
    "BOOST_AUTO_TEST_CASE",
    "BOOST_FIXTURE_TEST_CASE",
    "BENCHMARK(",
    "BENCHMARK_F(",
    "BENCHMARK_MAIN",
    "LLVMFuzzerTestOneInput",
)

# ``TEST_F(FixtureName, TestName)`` / ``TEST_P(FixtureName, …)`` /
# ``TYPED_TEST(FixtureName, …)`` — only the first identifier matters for
# fixture rescue.
_GTEST_FIXTURE_RE = re.compile(
    r"\b(?:TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*([A-Z_]\w*)\s*,"
)

# Boost.Test: ``BOOST_FIXTURE_TEST_CASE(TestName, FixtureName)`` —
# fixture is the *second* arg.
_BOOST_FIXTURE_RE = re.compile(
    r"\bBOOST_FIXTURE_TEST_(?:CASE|SUITE)\s*\(\s*\w+\s*,\s*([A-Z_]\w*)"
)

# Catch2 / doctest: ``TEST_CASE_METHOD(FixtureName, "name")``.
_CATCH_FIXTURE_RE = re.compile(
    r"\bTEST_CASE_METHOD\s*\(\s*([A-Z_]\w*)\s*,"
)


def _file_uses_test_framework(text: str) -> bool:
    return any(tok in text for tok in _TEST_FRAMEWORK_TOKENS)


def _add_gtest_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    """Emit fixture type_use edges + stamp test TUs as entry points."""
    count = 0
    class_to_file = _build_class_to_file(parsed_files, _CPP_LANGS)

    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in _CPP_LANGS:
            continue
        text = read_text(parsed)
        if not text or not _file_uses_test_framework(text):
            continue

        # Mark the TU as an entry point — the test runner discovers TEST
        # bodies via static-init registration, so no static caller edge
        # exists.
        node = graph.nodes.get(path)
        if node is not None:
            node["is_entry_point"] = True

        seen: set[str] = set()
        for rx in (_GTEST_FIXTURE_RE, _BOOST_FIXTURE_RE, _CATCH_FIXTURE_RE):
            for m in rx.finditer(text):
                fixture = m.group(1)
                if fixture in seen:
                    continue
                seen.add(fixture)
                target = class_to_file.get(fixture)
                if target and target != path and target in path_set:
                    if _add_edge_if_new(graph, path, target):
                        count += 1

    return count


class _CppTestFrameworkHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        for parsed in dctx.parsed_files.values():
            if parsed.file_info.language not in _CPP_LANGS:
                continue
            for imp in parsed.imports:
                mod = imp.module_path
                if (
                    "gtest" in mod
                    or "catch" in mod
                    or "doctest" in mod
                    or "boost/test" in mod
                    or "benchmark/benchmark" in mod
                ):
                    return True
        # No include detection — fall back to source-text token scan in
        # ``add_edges`` itself. Detect returns True if any cpp/c file
        # exists; the per-file cheap-reject still skips non-tests.
        return any(
            parsed.file_info.language in _CPP_LANGS
            for parsed in dctx.parsed_files.values()
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_gtest_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_CppTestFrameworkHandler()]
