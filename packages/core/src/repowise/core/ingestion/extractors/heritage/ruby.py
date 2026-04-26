"""Ruby heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text

_MIXIN_METHODS = {"include", "extend", "prepend"}


def _extract_ruby_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Ruby: class Foo < Bar; include Mod; extend Mod; prepend Mod."""
    superclass = def_node.child_by_field_name("superclass")
    if superclass:
        parent = node_text(superclass, src).strip()
        parent = parent.removeprefix("<").strip()
        bare = parent.split("::")[-1]
        if bare:
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=bare,
                    kind="extends",
                    line=line,
                )
            )

    for child in def_node.children:
        if child.type == "body_statement":
            for stmt in child.children:
                if stmt.type != "call":
                    continue
                method_node = stmt.child_by_field_name("method")
                if method_node is None:
                    for sc in stmt.children:
                        if sc.type == "identifier":
                            method_node = sc
                            break
                if method_node is None:
                    continue
                method_name = node_text(method_node, src).strip()
                if method_name not in _MIXIN_METHODS:
                    continue
                args = stmt.child_by_field_name("arguments")
                if args is None:
                    for sc in stmt.children:
                        if sc.type == "argument_list":
                            args = sc
                            break
                if args is None:
                    continue
                for arg in args.children:
                    if arg.type == "constant":
                        mixin_name = node_text(arg, src).strip().split("::")[-1]
                        if mixin_name:
                            out.append(
                                HeritageRelation(
                                    child_name=name,
                                    parent_name=mixin_name,
                                    kind="mixin",
                                    line=stmt.start_point[0] + 1,
                                )
                            )
