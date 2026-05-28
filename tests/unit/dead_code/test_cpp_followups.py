"""Regression tests for the Phase 4 follow-up quick wins:

* broader ``tests/**`` glob coverage (nlohmann/json layouts under
  ``tests/abi/``, ``tests/src/``, ``tests/cmake_*/`` were previously
  uncovered),
* plural ``*_benchmarks.{cc,cpp}`` glob (abseil's ``randen_benchmarks.cc``
  shape),
* ``PYBIND11_EMBEDDED_MODULE`` recognised as an entry-point marker,
* compiler-intrinsic preprocessor macros (``__has_include`` redefined as
  a fallback) skipped from unused_export.
"""

from __future__ import annotations

import fnmatch

from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS


def _matches(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in _NEVER_FLAG_PATTERNS)


class TestTestsTreeBroadGlob:
    def test_nlohmann_abi_diag_off(self) -> None:
        assert _matches("tests/abi/diag/diag_off.cpp")

    def test_nlohmann_cmake_target_subproject(self) -> None:
        assert _matches("tests/cmake_target_include_directories/project/Bar.cpp")

    def test_nlohmann_unit_macro(self) -> None:
        assert _matches("tests/src/unit-udt_macro.cpp")

    def test_nested_tests_header(self) -> None:
        assert _matches("tests/fixtures/utils.h")

    def test_prefixed_tests_tree(self) -> None:
        assert _matches("packages/foo/tests/integration/runner.cpp")


class TestPluralBenchmarksGlob:
    def test_plural_repo_root(self) -> None:
        assert _matches("absl/random/internal/randen_benchmarks.cc")

    def test_plural_repo_root_cpp(self) -> None:
        assert _matches("perf/random_benchmarks.cpp")


class TestPybind11EmbeddedModuleMarker:
    def test_marker_is_recognised_by_warmup(self) -> None:
        from repowise.core.ingestion.graph_warmups import _CPP_ENTRY_MARKERS

        assert "PYBIND11_EMBEDDED_MODULE" in _CPP_ENTRY_MARKERS
        assert "PYBIND11_MODULE" in _CPP_ENTRY_MARKERS  # not regressed

    def test_synthesis_regex_matches_embedded_variant(self) -> None:
        from repowise.core.ingestion.extractors.synthetic_symbols.cpp_macros import (
            _PYBIND11_MODULE_RE,
        )

        src = "PYBIND11_EMBEDDED_MODULE(embed_test, m) { m.def(\"add\", &add); }"
        m = _PYBIND11_MODULE_RE.search(src)
        assert m is not None
        assert m.group(1) == "embed_test"

    def test_synthesis_regex_still_matches_plain_variant(self) -> None:
        from repowise.core.ingestion.extractors.synthetic_symbols.cpp_macros import (
            _PYBIND11_MODULE_RE,
        )

        m = _PYBIND11_MODULE_RE.search(
            "PYBIND11_MODULE(my_mod, m) { m.def(\"x\", &x); }"
        )
        assert m is not None
        assert m.group(1) == "my_mod"


class TestCompilerBuiltinMacroSkip:
    def test_has_include_in_builtins(self) -> None:
        from repowise.core.analysis.dead_code.analyzer import _CPP_BUILTIN_MACROS

        assert "__has_include" in _CPP_BUILTIN_MACROS
        assert "__has_attribute" in _CPP_BUILTIN_MACROS
        assert "__has_cpp_attribute" in _CPP_BUILTIN_MACROS
        # The well-known runtime intrinsics common in libc/libcxx code.
        assert "__builtin_expect" in _CPP_BUILTIN_MACROS
        assert "__FILE__" in _CPP_BUILTIN_MACROS
