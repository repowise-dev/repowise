"""Direct tests for the C++ template-parameter helpers behind out-of-line member parents."""

from __future__ import annotations

import tree_sitter_cpp
from tree_sitter import Language, Node, Parser

from repowise.core.ingestion.parser_helpers import (
    _spells_enclosing_parameters,
    _template_parameter_names,
)

_PARSER = Parser(Language(tree_sitter_cpp.language()))


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _parameters(source: str) -> tuple[Node, str]:
    src = source
    root = _PARSER.parse(src.encode()).root_node
    decl = next(n for n in _walk(root) if n.type == "template_declaration")
    return decl.child_by_field_name("parameters"), src


class TestTemplateParameterNames:
    def test_unnamed_typename_is_none(self) -> None:
        params, src = _parameters("template <typename> struct S {};")
        assert _template_parameter_names(params, src) == [None]

    def test_default_argument_keeps_the_name(self) -> None:
        params, src = _parameters("template <class T = int> struct S {};")
        assert _template_parameter_names(params, src) == ["T"]

    def test_non_type_pointer_declarator_strips_the_star(self) -> None:
        params, src = _parameters("template <int *P> struct S {};")
        assert _template_parameter_names(params, src) == ["P"]

    def test_mixed_list_and_pack(self) -> None:
        params, src = _parameters("template <typename T, int N, typename... Ts> struct S {};")
        assert _template_parameter_names(params, src) == ["T", "N", "Ts"]


class TestSpellsEnclosingParameters:
    def test_no_enclosing_template_declaration_is_false(self) -> None:
        src = "void Box<T>::f() {}"
        root = _PARSER.parse(src.encode()).root_node
        scope = next(n for n in _walk(root) if n.type == "template_type")
        arguments = scope.child_by_field_name("arguments")
        assert not any(a.type == "template_declaration" for a in _ancestors(scope))
        assert _spells_enclosing_parameters(scope, arguments, src) is False


def _ancestors(node: Node):
    node = node.parent
    while node is not None:
        yield node
        node = node.parent
