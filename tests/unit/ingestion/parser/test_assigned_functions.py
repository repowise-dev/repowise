"""JS/TS functions assigned to a property, and ``var`` bindings of a function.

``obj.a = function a() {}``, ``exports.d = ...``, ``module.exports.x = ...``,
``Foo.prototype.e = ...`` and ``var h = function () {}`` each declare a
function a caller can reach, so each is a symbol named by its property or
binding, as ``const f = () => {}`` is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

LANGUAGES = ("javascript", "typescript")


def _symbols(parser: ASTParser, source: str, language: str = "javascript"):
    path = "app.ts" if language == "typescript" else "app.js"
    parsed = parser.parse_file(_make_file_info(path, language), source.encode("utf-8"))
    return parsed.symbols


def _rows(parser: ASTParser, source: str, language: str = "javascript"):
    """``(id without the path, kind, parent, start_line)`` per symbol."""
    return [
        (s.id.split("::", 1)[1], s.kind, s.parent_name, s.start_line)
        for s in _symbols(parser, source, language)
    ]


_FORMS = """\
var obj = {};
obj.a = function a() { return 1; };
obj.b = function () { return 2; };
obj.c = () => { return 3; };
exports.d = function d() { return 4; };
function Foo() {}
Foo.prototype.e = function e() { return 5; };
const f = function f() { return 6; };
const g = () => 7;
var h = function h() { return 8; };
var k = () => 9;
module.exports.m = x => x;
module.exports = obj;
"""


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_assigned_form_is_a_symbol(parser: ASTParser, language: str) -> None:
    assert _rows(parser, _FORMS, language) == [
        ("a", "function", None, 2),
        ("b", "function", None, 3),
        ("c", "function", None, 4),
        ("d", "function", None, 5),
        ("Foo", "function", None, 6),
        ("Foo::e", "method", "Foo", 7),
        ("f", "function", None, 8),
        ("g", "function", None, 9),
        ("h", "function", None, 10),
        ("k", "function", None, 11),
        ("m", "function", None, 12),
    ]


def test_typescript_annotated_forms(parser: ASTParser) -> None:
    source = (
        "const obj: any = {};\n"
        "obj.a = function a(): number { return 1; };\n"
        "obj.c = (): number => 3;\n"
        "export const g = (): number => 7;\n"
        "export var v = (x: number): number => x;\n"
    )
    assert _rows(parser, source, "typescript") == [
        ("obj", "variable", None, 1),
        ("obj::a", "method", "obj", 2),
        ("obj::c", "method", "obj", 3),
        ("g", "function", None, 4),
        ("v", "function", None, 5),
    ]


@pytest.mark.parametrize("language", LANGUAGES)
def test_owner_must_be_declared_in_the_file(parser: ASTParser, language: str) -> None:
    source = (
        "class K { m() {} }\n"
        "K.s = function () {};\n"
        "var Baz = function () {};\n"
        "Baz.prototype.q = () => 2;\n"
        "Array.prototype.x = function () {};\n"
        "a.b.c = () => 1;\n"
    )
    assert _rows(parser, source, language) == [
        ("K", "class", None, 1),
        ("K::m", "method", "K", 1),
        ("K::s", "method", "K", 2),
        ("Baz", "function", None, 3),
        ("Baz::q", "method", "Baz", 4),
        ("x", "function", None, 5),
        ("c", "function", None, 6),
    ]


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_reassigned_property_keeps_one_id(parser: ASTParser, language: str) -> None:
    source = (
        "obj.dup = function () { return 1; };\n"
        "obj.dup = function () { return 2; };\n"
        "class K { m() {} }\n"
        "K.prototype.m = function () {};\n"
        "exports.e = function () {};\n"
        "function e() {}\n"
        "x.y = function () {};\n"
        "z.y = () => 1;\n"
    )
    rows = _rows(parser, source, language)
    assert rows == [
        ("dup", "function", None, 1),
        ("K", "class", None, 3),
        ("K::m", "method", "K", 3),
        ("e", "function", None, 6),
        ("y", "function", None, 7),
    ]
    ids = [s.id for s in _symbols(parser, source, language)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("language", LANGUAGES)
def test_only_assignments_that_run_at_load(parser: ASTParser, language: str) -> None:
    source = (
        "function Foo() { this.inst = function () {}; self.own = function () {}; }\n"
        "app.get('/', function (req, res) { res.local = function () {}; });\n"
        "const make = () => { obj.inArrow = () => 1; };\n"
        "(function () { obj.inIife = function () {}; })();\n"
        "(function () { obj.inCall = function () {}; }).call(this);\n"
        "!function () { obj.bang = function () {}; }();\n"
        "if (typeof window !== 'undefined') { window.g = function () {}; }\n"
    )
    names = [name for name, *_ in _rows(parser, source, language)]
    assert names == ["Foo", "make", "inIife", "inCall", "bang", "g"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_shapes_that_name_no_property_are_not_symbols(parser: ASTParser, language: str) -> None:
    source = (
        "module.exports = function createApp() {};\n"
        "this.x = function () {};\n"
        "obj.y = handler;\n"
        "obj.z = require('./z');\n"
        "items[0].w = function () {};\n"
    )
    assert _rows(parser, source, language) == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_const_and_let_are_unchanged(parser: ASTParser, language: str) -> None:
    source = (
        "const f = function f() { return 6; };\n"
        "let g = () => 7;\n"
        "const h = x => x;\n"
        "const n = 1;\n"
        "function outer() { const inner = () => 1; }\n"
    )
    symbols = _symbols(parser, source, language)
    assert [(s.name, s.kind, s.signature) for s in symbols] == [
        ("f", "function", "f = function f() { return 6; }"),
        ("g", "function", "g()"),
        ("h", "function", "h(x)"),
        ("n", "variable", "n = 1"),
        ("outer", "function", "function outer()"),
    ]


def test_var_holding_a_value_is_still_not_indexed(parser: ASTParser) -> None:
    assert _rows(parser, "var n = 1;\nvar o = {};\nvar s = require('./s');\n") == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_signature_span_and_jsdoc(parser: ASTParser, language: str) -> None:
    source = (
        "/** Sends the body. */\n"
        "res.send = function send(body, status) {\n"
        "  return body;\n"
        "};\n"
        "res.one = x => x;\n"
    )
    send, one = _symbols(parser, source, language)
    assert (send.signature, send.start_line, send.end_line) == ("send(body, status)", 2, 4)
    assert send.docstring == "Sends the body."
    assert one.signature == "one(x)"


def test_calls_resolve_to_assigned_functions(tmp_path: Path) -> None:
    from repowise.core.ingestion import FileTraverser, GraphBuilder

    (tmp_path / "lib.js").write_text(
        "const o = {};\n"
        "o.f = function () { return 1; };\n"
        "function caller() { return o.f(); }\n"
        "function Foo() {}\n"
        "Foo.prototype.e = function () { return this.h(); };\n"
        "Foo.prototype.h = function () { return 5; };\n"
        "var h = function () { return k(); };\n"
        "var k = () => 2;\n"
        "exports.d = function d() { return 4; };\n",
        encoding="utf-8",
    )
    (tmp_path / "use.js").write_text(
        "const { d } = require('./lib');\nfunction main() { return d(); }\n",
        encoding="utf-8",
    )
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    calls = {
        (src, dst) for src, dst, data in graph.edges(data=True) if data.get("edge_type") == "calls"
    }
    assert {
        ("lib.js::caller", "lib.js::o::f"),
        ("lib.js::Foo::e", "lib.js::Foo::h"),
        ("lib.js::h", "lib.js::k"),
        ("use.js::main", "lib.js::d"),
    } <= calls
