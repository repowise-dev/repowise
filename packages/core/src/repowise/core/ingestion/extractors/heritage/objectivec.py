"""Objective-C heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
from ..helpers import node_text


def _extract_objectivec_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Objective-C: ``@interface Foo : NSObject <Delegate, NSCopying>``.

    The superclass is the ``superclass`` field and the adopted protocols are
    the ``type_name`` children of the ``parameterized_arguments`` list that
    follows it, which is the C grammar's generic-argument node reused for the
    ``<...>`` conformance syntax.

    Only a plain ``@interface`` carries heritage: a class extension has no
    superclass and no conformance list in this position, and the
    ``@implementation`` repeats neither.

    Stated ceiling: a category that adopts a protocol
    (``@interface Foo (Extras) <Bar>``) emits nothing either. The early return
    below drops the whole node, and a category's conformance belongs to the
    class rather than to the category symbol, which is a separate question.
    """
    if def_node.child_by_field_name("category") is not None:
        return

    superclass = def_node.child_by_field_name("superclass")
    if superclass is not None:
        parent = bare_type_name(node_text(superclass, src))
        if parent and parent != name:
            out.append(
                HeritageRelation(
                    child_name=name, parent_name=parent, kind="extends", line=line
                )
            )

    for child in def_node.named_children:
        if child.type != "parameterized_arguments":
            continue
        for protocol_node in child.named_children:
            if protocol_node.type != "type_name":
                continue
            protocol = bare_type_name(node_text(protocol_node, src))
            if protocol and protocol != name:
                out.append(
                    HeritageRelation(
                        child_name=name, parent_name=protocol, kind="implements", line=line
                    )
                )
