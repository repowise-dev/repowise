"""Rust heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
from ..helpers import node_text


def _impl_relation(def_node: Node, line: int, src: str, out: list[HeritageRelation]) -> None:
    """``impl Trait for Type`` -> ``Type`` trait_impl ``Trait``."""
    trait_node = def_node.child_by_field_name("trait")
    type_node = def_node.child_by_field_name("type")
    if not (trait_node and type_node):
        return
    trait_name = bare_type_name(node_text(trait_node, src))
    type_name = bare_type_name(node_text(type_node, src))
    if trait_name and type_name:
        out.append(
            HeritageRelation(
                child_name=type_name,
                parent_name=trait_name,
                kind="trait_impl",
                line=line,
            )
        )


def _trait_supertraits(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``trait Foo: Bar + Baz`` -> ``Foo`` extends ``Bar``, ``Baz``.

    A supertrait may also be written ``trait Foo where Self: Bar``, which the
    ``bounds`` field does not carry, so the where clause is read too.
    """
    _emit_bounds(def_node.child_by_field_name("bounds"), name, line, src, out)

    for child in def_node.children:
        if child.type != "where_clause":
            continue
        for predicate in child.children:
            if predicate.type != "where_predicate":
                continue
            # Only `Self`. Every other subject is a type parameter, and a bound
            # on one says nothing about the trait that declares it.
            left = predicate.child_by_field_name("left")
            if left is None or node_text(left, src).strip() != "Self":
                continue
            _emit_bounds(predicate.child_by_field_name("bounds"), name, line, src, out)


def _emit_bounds(
    bounds: Node | None, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Emit one ``extends`` per trait named in a bound list."""
    if not bounds:
        return
    for child in bounds.children:
        if child.type in ("+", ":"):
            continue
        parent = bare_type_name(node_text(child, src))
        if parent:
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=parent,
                    kind="extends",
                    line=line,
                )
            )


# A `where T: Debug` bound elsewhere constrains a type PARAMETER, and the
# relation can only be recorded against the enclosing item, which is often a
# function. No edge drawn from that is true in any edge type, so those bounds
# are not extracted. Recording one needs a node for the parameter itself,
# which the graph has no concept of.


def _derive_traits(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``#[derive(Trait1, Trait2)]`` on the preceding attribute siblings."""
    prev = def_node.prev_named_sibling
    while prev is not None and prev.type == "attribute_item":
        if "derive(" in node_text(prev, src):
            for trait_name in _derive_idents(prev, src):
                out.append(
                    HeritageRelation(
                        child_name=name,
                        parent_name=trait_name,
                        kind="derive",
                        line=line,
                    )
                )
        prev = prev.prev_named_sibling


def _derive_idents(attr_node: Node, src: str) -> list[str]:
    """Yield the trait identifiers inside a ``#[derive(...)]`` attribute."""
    idents: list[str] = []
    for child in attr_node.children:
        if child.type != "attribute":
            continue
        for sub in child.children:
            if sub.type != "token_tree":
                continue
            for tok in sub.children:
                if tok.type == "identifier":
                    trait_name = node_text(tok, src).strip()
                    if trait_name:
                        idents.append(trait_name)
    return idents


def _extract_rust_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Rust: impl Trait for Type, trait Foo: Bar + Baz, #[derive(Trait)]."""
    if def_node.type == "impl_item":
        _impl_relation(def_node, line, src, out)
    elif def_node.type == "trait_item":
        _trait_supertraits(def_node, name, line, src, out)

    if def_node.type in ("struct_item", "enum_item"):
        _derive_traits(def_node, name, line, src, out)
