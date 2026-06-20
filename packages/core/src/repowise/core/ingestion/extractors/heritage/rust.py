"""Rust heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text

# Auto-derived/auto-implemented marker traits carry no useful heritage signal.
_SKIP_BOUNDS = ("Sized", "Send", "Sync", "Unpin")


def _strip_generics(text: str) -> str:
    """``Sides<T>`` -> ``Sides``; also strips a leading path (``a::B`` -> ``B``)."""
    name = text.strip().rsplit("::", 1)[-1]
    if "<" in name:
        name = name[: name.index("<")]
    return name


def _impl_relation(def_node: Node, line: int, src: str, out: list[HeritageRelation]) -> None:
    """``impl Trait for Type`` -> ``Type`` trait_impl ``Trait``."""
    trait_node = def_node.child_by_field_name("trait")
    type_node = def_node.child_by_field_name("type")
    if not (trait_node and type_node):
        return
    trait_name = _strip_generics(node_text(trait_node, src))
    type_name = _strip_generics(node_text(type_node, src))
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
    """``trait Foo: Bar + Baz`` -> ``Foo`` extends ``Bar``, ``Baz``."""
    bounds = def_node.child_by_field_name("bounds")
    if not bounds:
        return
    for child in bounds.children:
        if child.type in ("+", ":"):
            continue
        parent = node_text(child, src).strip().rsplit("::", 1)[-1]
        if parent:
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=parent,
                    kind="extends",
                    line=line,
                )
            )


def _where_clause_bounds(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``impl<T> Foo for T where T: Debug + Clone`` -> Foo trait_bound Debug, Clone."""
    for child in def_node.children:
        if child.type != "where_clause":
            continue
        for predicate in child.children:
            if predicate.type != "where_predicate":
                continue
            bounds = predicate.child_by_field_name("bounds")
            if not bounds:
                continue
            for bound_child in bounds.children:
                if bound_child.type in ("+", ":"):
                    continue
                bound_name = _strip_generics(node_text(bound_child, src))
                if bound_name and bound_name not in _SKIP_BOUNDS:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bound_name,
                            kind="trait_bound",
                            line=line,
                        )
                    )


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

    # Trait bounds from where clauses can appear on any item type.
    if def_node.type in (
        "impl_item",
        "function_item",
        "struct_item",
        "enum_item",
        "trait_item",
    ):
        _where_clause_bounds(def_node, name, line, src, out)

    if def_node.type in ("struct_item", "enum_item"):
        _derive_traits(def_node, name, line, src, out)
