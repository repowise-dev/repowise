"""Tests for the CMake reader."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.external_systems import cmake


def _write(tmp_path: Path, rel: str, text: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_add_executable_with_inline_sources(tmp_path):
    cml = _write(tmp_path, "CMakeLists.txt", """
        cmake_minimum_required(VERSION 3.10)
        project(demo)
        add_executable(demo_app main.cc helper.cc helper.h)
    """)
    cm = cmake.parse_cmake_lists(cml, repo_root=tmp_path)
    assert cm.path == "CMakeLists.txt"
    assert len(cm.targets) == 1
    t = cm.targets[0]
    assert t.name == "demo_app"
    assert t.kind == "executable"
    assert "main.cc" in t.sources and "helper.cc" in t.sources
    assert "helper.h" in t.private_headers


def test_add_library_with_target_sources_public(tmp_path):
    _write(tmp_path, "lib/CMakeLists.txt", """
        add_library(libfoo STATIC)
        target_sources(libfoo PRIVATE foo.cc)
        target_sources(libfoo PUBLIC include/foo/foo.h)
        target_include_directories(libfoo PUBLIC include)
    """)
    cm = cmake.parse_cmake_lists(tmp_path / "lib/CMakeLists.txt", repo_root=tmp_path)
    t = cm.targets[0]
    assert t.kind == "library_static"
    assert "lib/foo.cc" in t.sources
    assert "lib/include/foo/foo.h" in t.public_headers
    assert "lib/include" in t.include_dirs


def test_add_subdirectory_reactor(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        add_subdirectory(libfoo)
        add_subdirectory(app)
    """)
    _write(tmp_path, "libfoo/CMakeLists.txt", "add_library(foo SHARED foo.cc)")
    _write(tmp_path, "app/CMakeLists.txt", "add_executable(my_app main.cc)")
    files = cmake.discover_cmake_reactor(tmp_path)
    by_path = {f.path: f for f in files}
    assert "CMakeLists.txt" in by_path
    assert "libfoo/CMakeLists.txt" in by_path
    assert "app/CMakeLists.txt" in by_path
    libfoo = by_path["libfoo/CMakeLists.txt"]
    assert libfoo.targets[0].kind == "library_shared"
    app = by_path["app/CMakeLists.txt"]
    assert app.targets[0].kind == "executable"


def test_if_block_marks_conditional_sources(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        add_library(plat STATIC stub.cc)
        if(WIN32)
          target_sources(plat PRIVATE env_windows.cc)
        endif()
        if(UNIX)
          target_sources(plat PRIVATE env_posix.cc)
        endif()
    """)
    cm = cmake.parse_cmake_lists(tmp_path / "CMakeLists.txt", repo_root=tmp_path)
    t = cm.targets[0]
    assert "env_windows.cc" in t.conditional_sources
    assert "env_posix.cc" in t.conditional_sources
    assert "stub.cc" not in t.conditional_sources


def test_find_package_emits_external_record(tmp_path):
    cml = _write(tmp_path, "CMakeLists.txt", """
        find_package(Boost 1.74 REQUIRED)
        find_package(OpenSSL REQUIRED)
    """)
    records = cmake.parse(cml, tmp_path)
    names = {r.name for r in records}
    assert names == {"Boost", "OpenSSL"}
    boost = next(r for r in records if r.name == "Boost")
    assert boost.version == "1.74"
    assert boost.ecosystem == "cmake"


def test_set_expansion(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        set(LIB_SRC a.cc b.cc)
        add_library(thing STATIC ${LIB_SRC})
    """)
    cm = cmake.parse_cmake_lists(tmp_path / "CMakeLists.txt", repo_root=tmp_path)
    t = cm.targets[0]
    assert "a.cc" in t.sources and "b.cc" in t.sources


def test_target_compile_definitions(tmp_path):
    _write(tmp_path, "CMakeLists.txt", """
        add_library(thing STATIC a.cc)
        target_compile_definitions(thing PRIVATE -DTHING_EXPORT_BUILD THING_API)
    """)
    cm = cmake.parse_cmake_lists(tmp_path / "CMakeLists.txt", repo_root=tmp_path)
    t = cm.targets[0]
    assert "THING_EXPORT_BUILD" in t.compile_defines
    assert "THING_API" in t.compile_defines


def test_malformed_does_not_raise(tmp_path):
    p = tmp_path / "CMakeLists.txt"
    p.write_text("add_executable(", encoding="utf-8")
    cm = cmake.parse_cmake_lists(p, repo_root=tmp_path)
    # Trailing-open paren is tolerated; either zero targets or the args are
    # captured but we don't crash.
    assert isinstance(cm.targets, list)
