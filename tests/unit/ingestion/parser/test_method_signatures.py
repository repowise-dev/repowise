from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


@pytest.mark.parametrize(
    ("path", "language", "source", "name", "expected"),
    [
        (
            "Example.java",
            "java",
            b"class Example { V get(K key) { return null; } }",
            "get",
            "get(K key) -> V",
        ),
        (
            "Example.cs",
            "csharp",
            b"class Example { V Get(K key) { return default; } }",
            "Get",
            "Get(K key) -> V",
        ),
        (
            "example.cpp",
            "cpp",
            b"Widget make(int key) { return Widget(); }",
            "make",
            "make(int key) -> Widget",
        ),
        (
            "example.go",
            "go",
            b'package example\ntype Example struct{}\nfunc (e *Example) Get(key string) (string, error) { return "", nil }',
            "Get",
            "func (e *Example) Get(key string) -> (string, error)",
        ),
    ],
)
def test_method_signature_uses_language_syntax(
    parser: ASTParser, path: str, language: str, source: bytes, name: str, expected: str
) -> None:
    result = parser.parse_file(_make_file_info(path, language), source)
    method = next(symbol for symbol in result.symbols if symbol.name == name)
    assert method.signature == expected
