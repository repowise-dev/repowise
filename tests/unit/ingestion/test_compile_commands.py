"""compile_commands.json reading for MSVC/clang-cl databases and the POSIX form."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile
from repowise.core.ingestion.resolvers.compile_commands import (
    entry_tokens,
    include_dirs_from_tokens,
    split_command,
    split_windows,
)

# ---------------------------------------------------------------------------
# Token level: platform independent
# ---------------------------------------------------------------------------


def test_windows_command_keeps_backslashes() -> None:
    tokens = split_command(r"clang++ -IC:\rage\src -c C:\rage\src\a.cpp")
    assert include_dirs_from_tokens(tokens) == [r"C:\rage\src"]


def test_split_windows_follows_argv_rules() -> None:
    cmd = r'cl.exe /I"C:\Program Files\SDK\include" /DNAME=\"x\" "a b.cpp" C:\dir\\ ""'
    assert split_windows(cmd) == [
        "cl.exe",
        r"/IC:\Program Files\SDK\include",
        '/DNAME="x"',
        "a b.cpp",
        "C:\\dir\\\\",
        "",
    ]


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["cl.exe", "/c", r"/IC:\a", "/I", r"C:\b"], [r"C:\a", r"C:\b"]),
        (["cl", "/c", r"-IC:\a", "-I", r"C:\b"], [r"C:\a", r"C:\b"]),
        (["cl.exe", "/external:I", r"C:\ext", r"/external:IC:\ext2"], [r"C:\ext", r"C:\ext2"]),
        (["clang-cl.exe", "-imsvc", r"C:\sdk", r"-external:IC:\e"], [r"C:\sdk", r"C:\e"]),
        (
            ["clang-cl", "/clang:-Iclang_dir", "/Imsvc_dir", "/c", "a.cpp"],
            ["clang_dir", "msvc_dir"],
        ),
        (
            [
                "clang-cl",
                "/clang:-isystem",
                "/clang:sys_dir",
                "/clang:-idirafter/after",
                "/c",
                "a.cpp",
            ],
            ["sys_dir", "/after"],
        ),
        # behind a compiler cache wrapper, and an unnamed driver with /c
        (["sccache.exe", r"C:\VS\cl.exe", r"/IC:\a"], [r"C:\a"]),
        ([r"C:\tools\wrapper.exe", "/c", r"/IC:\a"], [r"C:\a"]),
    ],
)
def test_msvc_include_options(tokens: list[str], expected: list[str]) -> None:
    assert include_dirs_from_tokens(tokens) == expected


def test_slash_paths_are_not_options_on_a_unix_command() -> None:
    tokens = ["/usr/bin/c++", "-I/repo/inc", "/clang:-I/nope", "-c", "/Include/x.cpp"]
    assert include_dirs_from_tokens(tokens) == ["/repo/inc"]


def test_idirafter_separate_and_glued() -> None:
    tokens = ["c++", "-idirafter", "after_dir", "-idirafter/glued", "-c", "a.cpp"]
    assert include_dirs_from_tokens(tokens) == ["after_dir", "/glued"]


def test_typical_cmake_linux_entry_is_unchanged() -> None:
    entry = {
        "directory": "/home/dev/proj/build",
        "command": (
            '/usr/bin/c++ -DVERSION=\\"1.0\\" -I/home/dev/proj/include '
            "-I ../src -isystem /home/dev/proj/third_party -isystem/opt/x "
            "-iquote quoted -O2 -std=c++17 -o CMakeFiles/a.dir/a.cpp.o "
            "-c /home/dev/proj/src/a.cpp"
        ),
        "file": "/home/dev/proj/src/a.cpp",
    }
    tokens = entry_tokens(entry, Path(entry["directory"]))
    assert '-DVERSION="1.0"' in tokens
    assert include_dirs_from_tokens(tokens) == [
        "/home/dev/proj/include",
        "../src",
        "/home/dev/proj/third_party",
        "/opt/x",
        "quoted",
    ]


def test_response_files_expand_relative_to_directory(tmp_path: Path) -> None:
    (tmp_path / "inner.rsp").write_text('/I"inner dir"', encoding="utf-8")
    # MSBuild writes response files as UTF-16 with a BOM.
    (tmp_path / "outer.rsp").write_bytes('/I"outer" @inner.rsp'.encode("utf-16"))
    entry = {"directory": str(tmp_path), "command": "cl.exe /c @outer.rsp @missing.rsp a.cpp"}
    tokens = entry_tokens(entry, tmp_path)
    assert include_dirs_from_tokens(tokens) == ["outer", "inner dir"]


def test_response_file_cycle_terminates(tmp_path: Path) -> None:
    (tmp_path / "a.rsp").write_text("/Ia @b.rsp", encoding="utf-8")
    (tmp_path / "b.rsp").write_text("/Ib @a.rsp", encoding="utf-8")
    entry = {"arguments": ["cl.exe", "/c", "@a.rsp"]}
    assert include_dirs_from_tokens(entry_tokens(entry, tmp_path)) == ["a", "b"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # a drive-letter-looking value inside a define stays a Unix command
        ('g++ -DFMT="a:\\n" -I/my\\ dir -I/inc x.cpp', ["/my dir", "/inc"]),
        # a bare /c path token does not turn /Include paths into options
        ("g++ -I/a /c /Include/x.cpp", ["/a"]),
    ],
)
def test_unix_commands_keep_posix_reading(command: str, expected: list[str]) -> None:
    assert include_dirs_from_tokens(entry_tokens({"command": command}, Path("."))) == expected


def test_wrapped_cl_command_keeps_backslashes() -> None:
    tokens = split_command(r"ccache cl /c /Iinc\sub x.cpp")
    assert include_dirs_from_tokens(tokens) == [r"inc\sub"]


def test_utf16_response_file_without_bom(tmp_path: Path) -> None:
    (tmp_path / "u.rsp").write_bytes(r"/IC:\u".encode("utf-16-le"))
    tokens = entry_tokens({"arguments": ["cl.exe", "/c", "@u.rsp"]}, tmp_path)
    assert include_dirs_from_tokens(tokens) == [r"C:\u"]


def test_bad_quoting_gives_no_tokens() -> None:
    assert entry_tokens({"command": "g++ -I'unterminated"}, Path(".")) == []


# ---------------------------------------------------------------------------
# End to end: two include roots, with same-named decoys elsewhere in the repo
# ---------------------------------------------------------------------------


def _cpp(path: str, includes: tuple[str, ...] = ()) -> ParsedFile:
    imports = [
        Import(
            raw_statement=f'#include "{h}"',
            module_path=h,
            imported_names=[],
            is_relative=False,
            resolved_file=None,
        )
        for h in includes
    ]
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="cpp",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ParsedFile(
        file_info=info,
        symbols=[],
        imports=imports,
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )


_HEADERS = (
    "engine/core/inc/vecmath.h",
    "engine/render/inc/api.h",
    # decoys a stem or importer-relative fallback could pick instead
    "aaa/vecmath.h",
    "aaa/api.h",
)


def _repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    for rel in (*_HEADERS, "game/src/main.cpp"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    (tmp_path / "build").mkdir()
    return (
        tmp_path / "engine/core/inc",
        tmp_path / "engine/render/inc",
        tmp_path / "game/src/main.cpp",
    )


def _build(tmp_path: Path, db_path: Path, entries: list[dict]):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text(json.dumps(entries), encoding="utf-8")
    b = GraphBuilder(repo_path=tmp_path)
    for rel in _HEADERS:
        b.add_file(_cpp(rel))
    b.add_file(_cpp("game/src/main.cpp", ("vecmath.h", "api.h")))
    b.build()
    return b.graph()


def _assert_resolved(graph) -> None:
    assert graph.has_edge("game/src/main.cpp", "engine/core/inc/vecmath.h")
    assert graph.has_edge("game/src/main.cpp", "engine/render/inc/api.h")
    assert not graph.has_edge("game/src/main.cpp", "aaa/vecmath.h")
    assert not graph.has_edge("game/src/main.cpp", "aaa/api.h")


def _entries(kind: str, core: Path, render: Path, main: Path, build: Path) -> list[dict]:
    if kind == "msvc_command_quoted":
        cmd = f'cl.exe /nologo /c /I"{core}" /I "{render}" /Fo"x.obj" "{main}"'
        return [{"directory": str(build), "command": cmd, "file": str(main)}]
    if kind == "clang_cl_arguments":
        args = ["clang-cl.exe", "/c", f"/I{core}", "-imsvc", str(render), str(main)]
        return [{"directory": str(build), "arguments": args, "file": str(main)}]
    if kind == "external_include":
        args = ["cl.exe", "/c", f"/external:I{core}", "-external:I", str(render), str(main)]
        return [{"directory": str(build), "arguments": args, "file": str(main)}]
    if kind == "response_file":
        (build / "main.rsp").write_text(
            '/I"../engine/core/inc"\r\n/I"../engine/render/inc"\r\n', encoding="utf-8"
        )
        cmd = f'"{build / "cl.exe"}" /c @main.rsp ../game/src/main.cpp'
        return [{"directory": str(build), "command": cmd, "file": "../game/src/main.cpp"}]
    if kind == "relative_file_and_dirs":
        args = ["cl", "/c", "/I../engine/core/inc", "/I", "../engine/render/inc", "x.cpp"]
        return [{"directory": str(build), "arguments": args, "file": "../game/src/main.cpp"}]
    if kind == "relative_posix":
        args = ["c++", "-I../engine/core/inc", "-I", "../engine/render/inc", "-c", "x.cpp"]
        return [{"directory": str(build), "arguments": args, "file": "../game/src/main.cpp"}]
    if kind == "windows_command_string":
        cmd = f"clang++ -I{core} -I {render} -c {main}"
        return [{"directory": str(build), "command": cmd, "file": str(main)}]
    if kind == "idirafter":
        args = ["c++", f"-idirafter{core}", "-idirafter", str(render), "-c", str(main)]
        return [{"directory": str(build), "arguments": args, "file": str(main)}]
    if kind == "clang_cl_passthrough":
        args = ["clang-cl.exe", "/c", f"/clang:-I{core}", f"/I{render}", str(main)]
        return [{"directory": str(build), "arguments": args, "file": str(main)}]
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    [
        "msvc_command_quoted",
        "clang_cl_arguments",
        "external_include",
        "response_file",
        "relative_file_and_dirs",
        "relative_posix",
        "windows_command_string",
        "idirafter",
        "clang_cl_passthrough",
    ],
)
def test_database_forms_resolve_both_include_roots(tmp_path: Path, kind: str) -> None:
    core, render, main = _repo(tmp_path)
    build = tmp_path / "build"
    graph = _build(
        tmp_path, tmp_path / "compile_commands.json", _entries(kind, core, render, main, build)
    )
    _assert_resolved(graph)


def test_configured_database_path(tmp_path: Path) -> None:
    core, render, main = _repo(tmp_path)
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "compile_commands: out/vs2022\n", encoding="utf-8"
    )
    entries = _entries("clang_cl_arguments", core, render, main, tmp_path / "build")
    graph = _build(tmp_path, tmp_path / "out" / "vs2022" / "compile_commands.json", entries)
    _assert_resolved(graph)


def test_without_database_decoys_are_ambiguous(tmp_path: Path) -> None:
    """The fixture discriminates: with no database the right headers are not all found."""
    _repo(tmp_path)
    b = GraphBuilder(repo_path=tmp_path)
    for rel in _HEADERS:
        b.add_file(_cpp(rel))
    b.add_file(_cpp("game/src/main.cpp", ("vecmath.h", "api.h")))
    b.build()
    g = b.graph()
    assert not (
        g.has_edge("game/src/main.cpp", "engine/core/inc/vecmath.h")
        and g.has_edge("game/src/main.cpp", "engine/render/inc/api.h")
        and not g.has_edge("game/src/main.cpp", "aaa/vecmath.h")
        and not g.has_edge("game/src/main.cpp", "aaa/api.h")
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_database_path_casing_differs_from_the_index(tmp_path: Path) -> None:
    core, render, main = _repo(tmp_path)
    upper = Path(str(main).replace("game", "GAME").replace("main.cpp", "Main.cpp"))
    args = ["cl.exe", "/c", f"/I{core}", f"/I{render}", str(upper)]
    entries = [{"directory": str(tmp_path / "build"), "arguments": args, "file": str(upper)}]
    _assert_resolved(_build(tmp_path, tmp_path / "compile_commands.json", entries))
