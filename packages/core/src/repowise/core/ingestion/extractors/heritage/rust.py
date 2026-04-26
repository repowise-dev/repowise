"""Rust heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_rust_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Rust: impl Trait for Type, trait Foo: Bar + Baz, #[derive(Trait)]."""
    if def_node.type == "impl_item":
        trait_node = def_node.child_by_field_name("trait")
        type_node = def_node.child_by_field_name("type")
        if trait_node and type_node:
            trait_name = node_text(trait_node, src).strip().rsplit("::", 1)[-1]
            type_name = node_text(type_node, src).strip()
            if trait_name and type_name:
                out.append(
                    HeritageRelation(
                        child_name=type_name,
                        parent_name=trait_name,
                        kind="trait_impl",
                        line=line,
                    )
                )

    elif def_node.type == "trait_item":
        bounds = def_node.child_by_field_name("bounds")
        if bounds:
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

    elif def_node.type in ("struct_item", "enum_item"):
        # Walk preceding siblings for #[derive(Trait1, Trait2)]
        prev = def_node.prev_named_sibling
        while prev is not None and prev.type == "attribute_item":
            attr_text = node_text(prev, src).strip()
            if "derive(" in attr_text:
                for child in prev.children:
                    if child.type == "attribute":
                        for sub in child.children:
                            if sub.type == "token_tree":
                                for tok in sub.children:
                                    if tok.type == "identifier":
                                        trait_name = node_text(tok, src).strip()
                                        if trait_name:
                                            out.append(
                                                HeritageRelation(
                                                    child_name=name,
                                                    parent_name=trait_name,
                                                    kind="derive",
                                                    line=line,
                                                )
                                            )
            prev = prev.prev_named_sibling
