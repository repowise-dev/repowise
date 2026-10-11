"""TypeScript class members keep their parameters, and regex / ternary module
constants are symbols.

A second ``method_definition`` pattern that captured only the accessibility
modifier used to win the parser's per-(line, name) dedup for every
``private`` / ``protected`` / ``public`` method, so those methods lost their
parameter list. Module constants initialised with a regex literal or a
ternary matched no value alternative and minted no symbol at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "lang_samples" / "typescript"


def _symbols(parser: ASTParser, fixture: str) -> dict[str, object]:
    path = FIXTURES / fixture
    parsed = parser.parse_file(_make_file_info(fixture, "typescript"), path.read_bytes())
    # Overload signatures may surface as declarations; the implementation is
    # the one member row per name that carries the body.
    return {s.name: s for s in parsed.symbols if not s.is_declaration}


@pytest.mark.parametrize(
    ("name", "signature", "visibility"),
    [
        ("open", 'open(path: string, mode = "r") -> number', "public"),
        ("close", "close(fd: number) -> void", "private"),
        ("flush", "flush(fd: number, ...chunks: Uint8Array[]) -> void", "protected"),
        ("#reset", "#reset(hard?: boolean)", "private"),
        ("create", "create(options: object) -> Service", "public"),
        ("fetch", "fetch(url: string) -> Promise<string>", "public"),
        (
            "retry",
            "retry(fn: () => Promise<T>, attempts: number) -> Promise<T>",
            "private",
        ),
        ("toString", "toString() -> string", "public"),
        ("convert", "convert(value: string | number) -> string | number", "public"),
        ("constructor", "constructor(private readonly root: string)", "public"),
        ("cache", "cache(id: string, entity: T) -> void", "protected"),
    ],
)
def test_member_signature_and_visibility(
    parser: ASTParser, name: str, signature: str, visibility: str
) -> None:
    sym = _symbols(parser, "ts_class_modifiers.ts")[name]
    assert sym.kind == "method"
    assert sym.signature == signature
    assert sym.visibility == visibility


def test_async_member_flags(parser: ASTParser) -> None:
    syms = _symbols(parser, "ts_class_modifiers.ts")
    assert syms["fetch"].is_async
    assert syms["retry"].is_async
    assert not syms["create"].is_async


def test_members_parented_to_their_class(parser: ASTParser) -> None:
    syms = _symbols(parser, "ts_class_modifiers.ts")
    assert syms["close"].parent_name == "Service"
    assert syms["cache"].parent_name == "Repository"
    assert syms["Repository"].kind == "class"


def test_one_row_per_accessibility_modified_method(parser: ASTParser) -> None:
    parsed = parser.parse_file(
        _make_file_info("ts_class_modifiers.ts", "typescript"),
        (FIXTURES / "ts_class_modifiers.ts").read_bytes(),
    )
    ids = [s.id for s in parsed.symbols if s.name in ("open", "close", "flush", "retry")]
    assert len(ids) == len(set(ids)) == 4


def test_regex_and_ternary_module_constants(parser: ASTParser) -> None:
    syms = _symbols(parser, "ts_module_consts.ts")
    assert syms["SLUG_PATTERN"].kind == "constant"
    assert syms["SLUG_PATTERN"].visibility == "public"
    assert syms["whitespace"].kind == "variable"
    assert syms["whitespace"].visibility == "private"
    assert syms["mode"].visibility == "public"
    assert syms["retries"].visibility == "private"
    assert syms["limit"].kind == "variable"


def test_function_local_regex_and_ternary_stay_unindexed(parser: ASTParser) -> None:
    syms = _symbols(parser, "ts_module_consts.ts")
    assert "helper" in syms
    assert "inner" not in syms
    assert "pick" not in syms


def test_javascript_private_method_and_regex_constant(parser: ASTParser) -> None:
    src = b"export const RE = /a+/g;\nconst flag = a ? 1 : 2;\nclass C {\n  #step(n) {}\n}\n"
    parsed = parser.parse_file(_make_file_info("mod.js", "javascript"), src)
    syms = {s.name: s for s in parsed.symbols}
    assert syms["RE"].kind == "constant"
    assert "flag" in syms
    assert syms["#step"].signature == "#step(n)"
    assert syms["#step"].parent_name == "C"
