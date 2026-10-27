"""Names bound from a dynamic ``import()`` resolve calls like a static import.

``f`` is declared in two modules so a call can only reach ``m``'s through the
import binding, never through a repo-wide unique-name fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_EXTENSIONS = ("ts", "js", "tsx")

_BOUND = {
    "then_shorthand": 'export function run() { import("./m.js").then(({ f }) => f()); }\n',
    "then_renamed": 'export function run() { import("./m.js").then(({ f: g }) => g()); }\n',
    "then_function": (
        'export function run() { import("./m.js").then(function ({ f }) { f(); }); }\n'
    ),
    "then_namespace": 'export function run() { import("./m.js").then((m) => m.f()); }\n',
    "await_shorthand": (
        'export async function run() { const { f } = await import("./m.js"); f(); }\n'
    ),
    "await_renamed": (
        'export async function run() {\n  const { f: g } = await import("./m.js");\n  g();\n}\n'
    ),
    "await_namespace": (
        'export async function run() { const m = await import("./m.js"); m.f(); }\n'
    ),
    "non_ascii_earlier_on_the_line": (
        'export async function run() { const s = "é"; const { f } = await import("./m.js"); f(); }\n'
    ),
    "module_level_await": (
        'const { f: g } = await import("./m.js");\nexport function run() { g(); }\n'
    ),
    "require_in_body": (
        'export function run() { const { f: g } = require("./m.js"); g(); }\n'
    ),
}

_UNBOUND = {
    "package_specifier": (
        'export async function run() { const { f } = await import("pkg"); f(); }\n'
    ),
    "variable_specifier": (
        'export async function run(p) { const { f } = await import(p); f(); }\n'
    ),
    "not_exported": (
        'export async function run() { const { nope } = await import("./m.js"); nope(); }\n'
    ),
    "shadowed_by_callback_parameter": (
        'export async function run() {\n  const { f } = await import("./m.js");\n'
        "  [1].map((f) => f());\n}\n"
    ),
    "shadowed_on_the_import_line_by_a_parameter": (
        'export async function run() { const { f } = await import("./m.js");'
        " [1].map((f) => f()); }\n"
    ),
    "shadowed_on_the_import_line_by_a_nested_local": (
        'export async function run() { const { f } = await import("./m.js");'
        " function g() { const f = 1; f(); } }\n"
    ),
}


def _calls(
    tmp_path: Path, ext: str, source: str, m_source: str = "export function f() { return 1; }\n"
) -> set[tuple[str, str]]:
    files = {
        f"m.{ext}": m_source,
        f"n.{ext}": "export function f() { return 2; }\n",
        f"a.{ext}": source,
    }
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(tmp_path).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    graph = builder.graph()
    return {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"}


@pytest.mark.parametrize("ext", _EXTENSIONS)
@pytest.mark.parametrize("shape", sorted(_BOUND))
def test_bound_name_calls_the_imported_function(tmp_path: Path, ext: str, shape: str) -> None:
    calls = _calls(tmp_path, ext, _BOUND[shape])
    assert (f"a.{ext}::run", f"m.{ext}::f") in calls
    assert (f"a.{ext}::run", f"n.{ext}::f") not in calls


@pytest.mark.parametrize("ext", _EXTENSIONS)
@pytest.mark.parametrize("shape", sorted(_UNBOUND))
def test_unbound_name_gets_no_edge(tmp_path: Path, ext: str, shape: str) -> None:
    calls = _calls(tmp_path, ext, _UNBOUND[shape])
    assert not {edge for edge in calls if edge[0].startswith(f"a.{ext}::")}


def test_each_dynamic_import_of_one_module_keeps_its_own_names(tmp_path: Path) -> None:
    calls = _calls(
        tmp_path,
        "ts",
        'export async function run() { const { f } = await import("./m.js"); f(); }\n'
        'export async function other() { const { h } = await import("./m.js"); h(); }\n',
        m_source="export function f() { return 1; }\nexport function h() { return 3; }\n",
    )
    assert ("a.ts::run", "m.ts::f") in calls
    assert ("a.ts::other", "m.ts::h") in calls
