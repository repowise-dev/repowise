"""Tests for the C/C++ workspace index + include resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion.graph._import_targets import resolve_import_targets
from repowise.core.ingestion.models import Import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.cpp import resolve_cpp_import
from repowise.core.ingestion.resolvers.cpp_workspace import (
    build_cpp_workspace_index,
    is_stdlib_include,
)


def _write(tmp_path: Path, rel: str, text: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _make_ctx(tmp_path: Path, paths: list[str]) -> ResolverContext:
    return ResolverContext(
        path_set=set(paths),
        stem_map={Path(p).stem.lower(): [p] for p in paths},
        graph=nx.DiGraph(),
        repo_path=tmp_path,
    )


def test_public_header_layout_includes_lib_dir(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        add_library(coffee STATIC src/brew.cc)
        target_sources(coffee PUBLIC include/coffee/brew.h)
        target_include_directories(coffee PUBLIC include)
    """)
    _write(tmp_path, "include/coffee/brew.h", "// header\n")
    _write(tmp_path, "src/brew.cc", "// impl\n")
    paths = ["include/coffee/brew.h", "src/brew.cc", "CMakeLists.txt"]

    ctx = _make_ctx(tmp_path, paths)
    index = build_cpp_workspace_index(ctx)
    assert "include/coffee/brew.h" in index.public_header_includes.values()
    # Both literal and include-dir-stripped keys map to the file
    assert index.public_header_includes.get("coffee/brew.h") == "include/coffee/brew.h"


def test_resolve_public_header_via_workspace(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        add_library(coffee STATIC src/brew.cc)
        target_sources(coffee PUBLIC include/coffee/brew.h)
        target_include_directories(coffee PUBLIC include)
    """)
    _write(tmp_path, "include/coffee/brew.h", "// header\n")
    _write(tmp_path, "src/brew.cc", '#include "coffee/brew.h"\n')
    paths = ["include/coffee/brew.h", "src/brew.cc", "CMakeLists.txt"]
    ctx = _make_ctx(tmp_path, paths)

    target = resolve_cpp_import("coffee/brew.h", "src/brew.cc", ctx)
    assert target == "include/coffee/brew.h"


def test_resolve_importer_relative_still_works(tmp_path):
    _write(tmp_path, "src/util.h", "")
    _write(tmp_path, "src/util.cc", '#include "util.h"\n')
    paths = ["src/util.h", "src/util.cc"]
    ctx = _make_ctx(tmp_path, paths)
    target = resolve_cpp_import("util.h", "src/util.cc", ctx)
    assert target == "src/util.h"


def test_stdlib_includes_are_dropped(tmp_path):
    paths = ["main.cc"]
    ctx = _make_ctx(tmp_path, paths)
    assert is_stdlib_include("vector")
    assert is_stdlib_include("stdio.h")
    assert resolve_cpp_import("vector", "main.cc", ctx) is None
    assert resolve_cpp_import("stdio.h", "main.cc", ctx) is None


def test_include_resolves_to_the_header_alone(tmp_path):
    """An include of a target's header is one edge, never one per TU of the target."""
    _write(tmp_path, "CMakeLists.txt", """
        add_library(coffee STATIC src/brew.cc src/grind.cc)
        target_sources(coffee PUBLIC include/coffee/brew.h)
        target_include_directories(coffee PUBLIC include)
    """)
    _write(tmp_path, "include/coffee/brew.h", "")
    _write(tmp_path, "src/brew.cc", '#include "coffee/brew.h"\n')
    _write(tmp_path, "src/grind.cc", "")
    paths = [
        "include/coffee/brew.h", "src/brew.cc", "src/grind.cc", "CMakeLists.txt",
    ]
    ctx = _make_ctx(tmp_path, paths)

    imp = Import(
        raw_statement='#include "coffee/brew.h"',
        module_path="coffee/brew.h",
        imported_names=["brew"],
        is_relative=False,
        resolved_file=None,
    )
    assert resolve_import_targets(imp, "src/brew.cc", "cpp", ctx) == ("include/coffee/brew.h",)


def test_stem_fallback_prefers_the_named_header_over_its_source(tmp_path):
    # No build file and no include root: only the stem map can answer, and it
    # ranks the .cpp beside the .hpp first.
    paths = ["engine/data/Clump.cpp", "engine/data/Clump.hpp", "engine/loaders/Loader.cpp"]
    ctx = _make_ctx(tmp_path, paths)
    ctx.stem_map["clump"] = ["engine/data/Clump.cpp", "engine/data/Clump.hpp"]
    resolved = resolve_cpp_import("data/Clump.hpp", "engine/loaders/Loader.cpp", ctx)
    assert resolved == "engine/data/Clump.hpp"


def test_stem_fallback_never_lands_a_header_include_on_a_source(tmp_path):
    paths = ["engine/data/Clump.cpp", "engine/loaders/Loader.cpp", "tools/clump.py"]
    ctx = _make_ctx(tmp_path, paths)
    ctx.stem_map["clump"] = ["engine/data/Clump.cpp", "tools/clump.py"]
    assert resolve_cpp_import("data/Clump.hpp", "engine/loaders/Loader.cpp", ctx) is None
    # Still wrapped in angle brackets: the same file name, the same refusal.
    assert resolve_cpp_import("<data/Clump.hpp>", "engine/loaders/Loader.cpp", ctx) is None


def test_stem_fallback_takes_an_extensionless_include_only_by_exact_name(tmp_path):
    # ``<format>`` is a standard header, not the repo's vendored format.cpp.
    paths = ["third/format.cpp", "third/format.h", "qt/QString", "app/main.cpp"]
    ctx = _make_ctx(tmp_path, paths)
    ctx.stem_map["format"] = ["third/format.cpp", "third/format.h"]
    assert resolve_cpp_import("format", "app/main.cpp", ctx) is None
    assert resolve_cpp_import("QString", "app/main.cpp", ctx) == "qt/QString"


def test_project_export_macro_discovery(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        add_library(coffee STATIC src/brew.cc)
        target_sources(coffee PUBLIC include/coffee/api.h)
        target_include_directories(coffee PUBLIC include)
    """)
    _write(tmp_path, "include/coffee/api.h", """
#if defined(_WIN32)
#define COFFEE_EXPORT __declspec(dllexport)
#else
#define COFFEE_EXPORT __attribute__((visibility("default")))
#endif

class COFFEE_EXPORT Brewer { };
""")
    _write(tmp_path, "src/brew.cc", "")
    paths = ["include/coffee/api.h", "src/brew.cc", "CMakeLists.txt"]
    ctx = _make_ctx(tmp_path, paths)

    index = build_cpp_workspace_index(ctx)
    assert "COFFEE_EXPORT" in index.project_export_macros


def _ffi_plugin_fixture(tmp_path, extra_header: str = ""):
    _write(tmp_path, "src/health_connect/CMakeLists.txt", """
        cmake_minimum_required(VERSION 3.10)
        project(health_connect VERSION 0.0.1 LANGUAGES C)
        add_library(health_connect SHARED
          "./health_connect.c"
        )
    """)
    _write(tmp_path, "src/health_connect/dartjni.h", """
#pragma once
#if _WIN32
#define FFI_PLUGIN_EXPORT __declspec(dllexport)
#else
#define FFI_PLUGIN_EXPORT
#endif
FFI_PLUGIN_EXPORT int SpawnJvm(int args);
""" + extra_header)
    _write(tmp_path, "src/health_connect/health_connect.c", """
#include "dartjni.h"
FFI_PLUGIN_EXPORT
int SpawnJvm(int args) { return args + 1; }
""")
    paths = [
        "src/health_connect/CMakeLists.txt",
        "src/health_connect/dartjni.h",
        "src/health_connect/health_connect.c",
    ]
    return _make_ctx(tmp_path, paths)


def test_export_macro_found_in_header_beside_sources(tmp_path):
    # Flutter FFI plugin layout: no public headers declared, no include/.
    index = build_cpp_workspace_index(_ffi_plugin_fixture(tmp_path))
    assert "FFI_PLUGIN_EXPORT" in index.project_export_macros


@pytest.mark.parametrize("header", ["api.hh", "api.h++", "api.hpp", "api.hxx"])
def test_export_macro_found_in_other_header_extensions(tmp_path, header):
    _write(tmp_path, "CMakeLists.txt", "add_library(coffee SHARED src/brew.cc)\n")
    _write(tmp_path, f"src/{header}", "#define COFFEE_EXPORT __declspec(dllexport)\n")
    _write(tmp_path, "src/brew.cc", "")
    paths = [f"src/{header}", "src/brew.cc", "CMakeLists.txt"]
    index = build_cpp_workspace_index(_make_ctx(tmp_path, paths))
    assert "COFFEE_EXPORT" in index.project_export_macros


def test_non_export_macro_beside_sources_is_filtered(tmp_path):
    ctx = _ffi_plugin_fixture(tmp_path, "#define BUFFER_SIZE 4096\n")
    index = build_cpp_workspace_index(ctx)
    assert "BUFFER_SIZE" not in index.project_export_macros
    assert "FFI_PLUGIN_EXPORT" in index.project_export_macros


def test_include_dir_fallback_unchanged_by_source_dir_scan(tmp_path):
    # include/ still wins; a header beside the sources is not scanned.
    _write(tmp_path, "CMakeLists.txt", "add_library(coffee STATIC src/brew.cc)\n")
    _write(tmp_path, "include/coffee/api.h",
           "#define COFFEE_EXPORT __declspec(dllexport)\n")
    _write(tmp_path, "src/brew.cc", "")
    _write(tmp_path, "src/local.h", "#define LOCAL_EXPORT __declspec(dllexport)\n")
    paths = ["include/coffee/api.h", "src/brew.cc", "src/local.h", "CMakeLists.txt"]
    index = build_cpp_workspace_index(_make_ctx(tmp_path, paths))
    assert "COFFEE_EXPORT" in index.project_export_macros
    assert "LOCAL_EXPORT" not in index.project_export_macros


def test_no_workspace_falls_back_to_stem(tmp_path):
    _write(tmp_path, "src/util.h", "")
    paths = ["src/util.h", "src/main.cc"]
    ctx = _make_ctx(tmp_path, paths)
    # No CMakeLists, no compile_commands — must still find util.h via stem
    target = resolve_cpp_import("util.h", "src/main.cc", ctx)
    assert target == "src/util.h"


def test_siblings_in_targets(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        add_library(thing STATIC a.cc b.cc c.cc)
        target_sources(thing PUBLIC a.h)
    """)
    for f in ("a.cc", "b.cc", "c.cc", "a.h"):
        _write(tmp_path, f, "")
    paths = ["a.cc", "b.cc", "c.cc", "a.h", "CMakeLists.txt"]
    ctx = _make_ctx(tmp_path, paths)
    index = build_cpp_workspace_index(ctx)
    siblings = set(index.siblings_in_targets("a.cc"))
    assert siblings == {"b.cc", "c.cc"}
