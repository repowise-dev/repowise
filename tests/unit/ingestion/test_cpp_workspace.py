"""Tests for the C/C++ workspace index + include resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.cpp import (
    resolve_cpp_import,
    resolve_cpp_import_all,
)
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


def test_resolve_all_fans_out_to_target_siblings(tmp_path):
    """A ``#include`` of a public header fans out to every TU sharing
    the owning target so the header's defining files aren't orphaned."""
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

    targets = resolve_cpp_import_all("coffee/brew.h", "src/brew.cc", ctx)
    assert targets[0] == "include/coffee/brew.h"
    # Sibling TU should be included in the fan-out
    assert "src/grind.cc" in targets


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


def test_header_only_target_fans_out_to_other_headers(tmp_path):
    # fmt-like header-only library: public headers, zero sources. One
    # included header must pull the target's other headers along,
    # otherwise the rest of the library is orphaned.
    _write(tmp_path, "CMakeLists.txt", """
        add_library(fmtish INTERFACE)
        target_sources(fmtish INTERFACE include/fmtish/core.h include/fmtish/format.h include/fmtish/ranges.h)
        target_include_directories(fmtish INTERFACE include)
    """)
    _write(tmp_path, "include/fmtish/core.h", "// core\n")
    _write(tmp_path, "include/fmtish/format.h", "// format\n")
    _write(tmp_path, "include/fmtish/ranges.h", "// ranges\n")
    _write(tmp_path, "app/main.cc", '#include "fmtish/core.h"\n')
    paths = [
        "include/fmtish/core.h", "include/fmtish/format.h",
        "include/fmtish/ranges.h", "app/main.cc", "CMakeLists.txt",
    ]
    ctx = _make_ctx(tmp_path, paths)
    targets = resolve_cpp_import_all("fmtish/core.h", "app/main.cc", ctx)
    assert targets[0] == "include/fmtish/core.h"
    assert "include/fmtish/format.h" in targets
    assert "include/fmtish/ranges.h" in targets


def test_source_target_fanout_unchanged_by_header_pool(tmp_path):
    # A target WITH sources keeps the TU fan-out (no header pool mixing).
    _write(tmp_path, "CMakeLists.txt", """
        add_library(coffee STATIC src/brew.cc src/grind.cc)
        target_sources(coffee PUBLIC include/coffee/brew.h)
        target_include_directories(coffee PUBLIC include)
    """)
    for rel in ("include/coffee/brew.h", "src/brew.cc", "src/grind.cc"):
        _write(tmp_path, rel, "// x\n")
    paths = ["include/coffee/brew.h", "src/brew.cc", "src/grind.cc", "CMakeLists.txt"]
    ctx = _make_ctx(tmp_path, paths)
    targets = resolve_cpp_import_all("coffee/brew.h", "src/brew.cc", ctx)
    assert targets[0] == "include/coffee/brew.h"
    assert "src/grind.cc" in targets
