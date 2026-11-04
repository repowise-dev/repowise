"""Generic type arguments do not name a call or its receiver."""

from __future__ import annotations

import pytest
from tree_sitter import Parser

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.ast_utils import _callee_names
from repowise.core.ingestion.parser import _get_language


def _read_callee(source: bytes, language: str, call_type: str):
    grammar = _get_language(language)
    if grammar is None:
        pytest.skip(f"tree-sitter grammar missing for {language}")
    tree = Parser(grammar).parse(source)
    assert not tree.root_node.has_error
    stack = [tree.root_node]
    calls = []
    while stack:
        node = stack.pop()
        if node.type == call_type:
            calls.append(node)
        stack.extend(node.named_children)
    assert len(calls) == 1
    return _callee_names(calls[0])


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("Substitute.For<IFoo>()", ("for", {"substitute"})),
        ("Mock.Of<IBar>()", ("of", {"mock"})),
        ("Assert.Equal<IFoo>(a, b)", ("equal", {"assert"})),
        (
            "Assert.Equal<System.Collections.Generic.List<IFoo>>(a, b)",
            ("equal", {"assert"}),
        ),
        ("Factory<IFoo>.Create<IBar>()", ("create", {"factory"})),
        ("Equal<IFoo>(a, b)", ("equal", set())),
        ("Helper.Make<Assert>(a)", ("make", {"helper"})),
        ("Assert.Equal(a, b)", ("equal", {"assert"})),
    ],
)
def test_csharp_callee_names(expression: str, expected: tuple[str, set[str]]) -> None:
    source = f"class Tests {{ void Test() {{ {expression}; }} }}".encode()
    assert _read_callee(source, "csharp", "invocation_expression") == expected


@pytest.mark.parametrize(
    ("language", "source", "call_type", "expected"),
    [
        (
            "typescript",
            b"function test() { mock.fn<IFoo>(); }",
            "call_expression",
            ("fn", {"mock"}),
        ),
        (
            "java",
            b"class Tests { void test() { Collections.<IFoo>emptyList(); } }",
            "method_invocation",
            ("emptylist", {"collections"}),
        ),
        (
            "rust",
            b"fn test() { factory.create::<IBar>(); }",
            "call_expression",
            ("create", {"factory"}),
        ),
    ],
)
def test_other_generic_callees_unchanged(
    language: str, source: bytes, call_type: str, expected: tuple[str, set[str]]
) -> None:
    assert _read_callee(source, language, call_type) == expected


@pytest.mark.parametrize(
    ("expression", "called", "assertions"),
    [
        ("Assert.Equal<IFoo>(a, b)", "equal", 1),
        ("Assert.Equal<System.Collections.Generic.List<IFoo>>(a, b)", "equal", 1),
        ("Substitute.For<IFoo>()", "for", 0),
        ("Mock.Of<IBar>()", "of", 0),
        ("Helper.Make<IFoo>(a)", "make", 0),
    ],
)
def test_csharp_generic_calls_in_walker(expression: str, called: str, assertions: int) -> None:
    if _get_language("csharp") is None:
        pytest.skip("tree-sitter C# grammar missing")
    source = f"class Tests {{ void Test() {{ {expression}; }} }}".encode()
    result = walk_file("tests/Tests.cs", "csharp", source)
    assert len(result.functions) == 1
    function = result.functions[0]
    assert function.called_names == frozenset({called})
    assert function.assertion_count == assertions
