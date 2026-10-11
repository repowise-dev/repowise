"""Dart import binding extraction.

Handles the three shapes that bind names into the importing library:

- ``import 'x.dart' as prefix;`` — a module-alias binding; calls resolve
  through ``prefix.foo()``.
- ``import 'x.dart' show A, B;`` — one named binding per shown name.
- ``import 'x.dart' hide A;`` — narrows the namespace but binds nothing
  nameable, so it contributes no named bindings.

A Dart import without a ``show`` combinator brings the target's WHOLE
namespace into scope (there is no per-name import form), so
``imported_names`` is the wildcard ``["*"]`` in every non-``show`` shape —
otherwise the unused-export pass reads every plainly-imported symbol as
dead. ``export`` re-exports and ``part``/``part of`` (same-library splits)
are wildcards for the same reason.
"""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_dart_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    if stmt_node.type in ("part_directive", "part_of_directive"):
        return ["*"], []
    if stmt_node.type not in ("import_specification", "library_export"):
        return [], []

    shown: list[str] = []
    bindings: list[NamedBinding] = []
    children = stmt_node.children
    for i, child in enumerate(children):
        if child.type == "identifier" and i > 0 and children[i - 1].type == "as":
            prefix = node_text(child, src)
            if prefix:
                bindings.append(
                    NamedBinding(
                        local_name=prefix,
                        exported_name=None,
                        source_file=None,
                        is_module_alias=True,
                    )
                )
        elif child.type == "combinator":
            kids = child.children
            if not kids or kids[0].type != "show":
                continue  # ``hide`` still exposes the rest of the namespace
            for sub in kids:
                if sub.type == "identifier":
                    name = node_text(sub, src)
                    if name:
                        shown.append(name)
                        bindings.append(
                            NamedBinding(
                                local_name=name,
                                exported_name=name,
                                source_file=None,
                            )
                        )
    # ``show`` narrows the namespace to the listed names; every other shape
    # (plain, ``as`` prefix, ``hide``) is a whole-namespace import.
    names = shown if shown else ["*"]
    return names, bindings
