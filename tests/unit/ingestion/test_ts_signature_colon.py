"""Tests for TypeScript/JavaScript signature building (stray colon fix)."""

from __future__ import annotations

import tree_sitter_typescript
from tree_sitter import Language, Parser

from repowise.core.ingestion.extractors.signatures import build_signature


def _sig(code: bytes, node_type: str) -> str:
    parser = Parser(Language(tree_sitter_typescript.language_typescript()))
    tree = parser.parse(code)

    def find(node):
        if node.type == node_type:
            return node
        for child in node.children:
            found = find(child)
            if found is not None:
                return found
        return None

    def_node = find(tree.root_node)
    assert def_node is not None
    name = ""
    for child in def_node.children:
        if child.type in ("identifier", "property_identifier"):
            name = child.text.decode()
            break
    return build_signature(node_type, name, "()", def_node, code.decode())


def test_ts_function_signature_no_doubled_colon() -> None:
    sig = _sig(
        b'function foo(a: number): string { return "x"; }',
        "function_declaration",
    )
    assert sig == "function foo() -> string"
    assert "-> :" not in sig


def test_ts_method_signature_no_doubled_colon() -> None:
    sig = _sig(
        b"class C { bar(): number { return 1 } }",
        "method_definition",
    )
    assert sig == "bar() -> number"
    assert "-> :" not in sig


def test_ts_function_no_return_type() -> None:
    sig = _sig(b"function baz() { return; }", "function_declaration")
    assert sig == "function baz()"
