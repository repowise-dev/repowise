"""GDScript heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
from ..helpers import node_text


def _parent_from_extends(extends_node: Node, src: str) -> str | None:
    """Return the parent type named by an ``extends_statement``.

    The grammar is ``"extends" choice($.string, $.type)`` with no field
    names, so the payload is located by child *type*:

    - ``(type)``   -> ``extends Node2D`` / ``extends Foo.Bar``. The text is
      the parent name, matched against symbol names by HeritageResolver.
    - ``(string)`` -> ``extends "res://base.gd"``. A *path*, not a name, so
      it is deliberately NOT emitted as a heritage relation: HeritageResolver
      matches ``parent_name`` against symbol names only, and a path never
      matches one. The same statement is already captured as an import by
      queries/gdscript.scm and resolved to a real file by resolvers/gdscript.py,
      which is where that dependency belongs.
    """
    for child in extends_node.named_children:
        if child.type == "type":
            text = node_text(child, src).strip()
            # `type` is choice(attribute, identifier, subscript), so a
            # qualified parent arrives dotted (`extends Inventory.BaseSlot`).
            # HeritageResolver matches bare symbol names, and the inner class
            # is indexed as `BaseSlot`, so the shared helper takes the last
            # segment, the same answer the C++ and C# extractors read from it.
            return bare_type_name(text) or None
    return None


def _extract_gdscript_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """GDScript: ``extends Node2D`` / ``class_name Foo extends Bar`` /
    ``class Inner extends Baz:``.

    Two node types reach this function (see ``heritage_node_types`` in
    languages/specs/gdscript.py):

    ``class_definition`` -- an inner class. Usually carries its own
    ``extends`` field from the header form ``class Inner extends Baz:``,
    but the grammar also admits ``extends`` as a statement inside the
    class body, so that is checked as a fallback.

    ``class_name_statement`` -- the script-level class. The grammar gives it
    an optional ``extends`` field, which is populated only for the one-line
    form ``class_name Foo extends Bar``. Both of these are equally idiomatic
    and far more common::

        extends Node          class_name Player
        class_name Player     extends Node

    and in both the ``extends_statement`` is a *sibling* under ``source``,
    not a child. So when the field is absent, scan the file's top level for
    a standalone ``extends_statement``. A script may legally have only one,
    so the first hit is the answer.
    """
    extends_node = def_node.child_by_field_name("extends")

    if extends_node is None:
        # Where to look for a standalone `extends` depends on the node:
        # a class_name_statement's is a sibling under `source`, an inner
        # class's is a statement inside its own class_body (node-types.json
        # lists extends_statement among class_body's children, so the
        # header form is not the only way to spell it).
        scope = (
            def_node.parent
            if def_node.type == "class_name_statement"
            else def_node.child_by_field_name("body")
        )
        if scope is not None:
            for child in scope.named_children:
                if child.type == "extends_statement":
                    extends_node = child
                    break

    if extends_node is None:
        return

    parent_name = _parent_from_extends(extends_node, src)
    if not parent_name or parent_name == name:
        return

    out.append(
        HeritageRelation(
            child_name=name,
            parent_name=parent_name,
            kind="extends",
            line=line,
        )
    )
