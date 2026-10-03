"""JSDoc reaches the declaration it is written above, through export wrappers.

``/** doc */ export function f() {}`` puts the comment beside the
export_statement, not beside the function, so reading only the node's own
previous sibling left most exported TS/JS symbols with no docstring. The
comment must touch the declaration: a file header separated by a blank line
documents nothing.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info

# (shape id, source, symbol name, expected docstring or None)
_COMMON_SHAPES = [
    ("plain", "/** Adds. */\nfunction add(a, b) { return a + b; }\n", "add", "Adds."),
    ("exported", "/** Adds. */\nexport function add(a, b) { return a + b; }\n", "add", "Adds."),
    (
        "default_export",
        "/** Main entry. */\nexport default function main() {}\n",
        "main",
        "Main entry.",
    ),
    (
        "const_arrow",
        "/**\n * Doubles a value.\n */\nexport const double = (x) => x * 2;\n",
        "double",
        "Doubles a value.",
    ),
    ("const_value", "/** Slug shape. */\nexport const SLUG = /^[a-z]+$/;\n", "SLUG", "Slug shape."),
    ("class", "/** A shape. */\nexport class Shape {}\n", "Shape", "A shape."),
    (
        "method",
        "export class Shape {\n  /** The area. */\n  area() { return 0; }\n}\n",
        "area",
        "The area.",
    ),
    (
        "file_header_negative",
        "/**\n * @file Geometry helpers.\n */\n\nexport function area() {}\n",
        "area",
        None,
    ),
    (
        "blank_line_negative",
        "/** Stale note. */\n\nexport const limit = 10;\n",
        "limit",
        None,
    ),
    (
        "second_declarator_negative",
        "/** Only the first. */\nexport const first = 1, second = 2;\n",
        "second",
        None,
    ),
    (
        "line_comment_negative",
        "// Not JSDoc.\nexport function area() {}\n",
        "area",
        None,
    ),
]

_TS_ONLY_SHAPES = [
    (
        "overloads",
        "export function parse(v: string): number;\nexport function parse(v: number): number;\n"
        "/** Parses a value. */\nexport function parse(v: string | number): number { return 0; }\n",
        "parse",
        "Parses a value.",
    ),
    (
        "decorated_class",
        "/** A component. */\n@Component({})\nclass Widget {}\n",
        "Widget",
        "A component.",
    ),
    (
        "decorated_export",
        "/** A component. */\n@Component({})\nexport class Widget {}\n",
        "Widget",
        "A component.",
    ),
    (
        "decorated_method",
        "export class Api {\n  /** Lists items. */\n  @Get()\n  list(): void {}\n}\n",
        "list",
        "Lists items.",
    ),
    (
        "declare",
        "/** Internal shape. */\nexport declare type Shape = { a: number };\n",
        "Shape",
        "Internal shape.",
    ),
    (
        "private_method",
        "export class Api {\n  /** Resets state. */\n  private reset(hard: boolean): void {}\n}\n",
        "reset",
        "Resets state.",
    ),
]

_CASES = [
    pytest.param(path, lang, src, name, doc, id=f"{path}-{shape}")
    for path, lang, shapes in (
        ("mod.ts", "typescript", _COMMON_SHAPES + _TS_ONLY_SHAPES),
        ("mod.tsx", "typescript", _COMMON_SHAPES + _TS_ONLY_SHAPES),
        ("mod.js", "javascript", _COMMON_SHAPES),
    )
    for shape, src, name, doc in shapes
]


@pytest.mark.parametrize(("path", "language", "src", "name", "expected"), _CASES)
def test_jsdoc_attachment(
    parser: ASTParser, path: str, language: str, src: str, name: str, expected: str | None
) -> None:
    parsed = parser.parse_file(_make_file_info(path, language), src.encode())
    matches = [s for s in parsed.symbols if s.name == name and not s.is_declaration]
    assert len(matches) == 1, [s.id for s in parsed.symbols]
    assert matches[0].docstring == expected
