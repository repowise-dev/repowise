"""C# import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_csharp_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from C# using directives.

    Recognises:
        using Foo;                          -> NamedBinding(local="Foo", exported="Foo")
        using Foo.Bar.Baz;                  -> NamedBinding(local="Baz", exported="Foo.Bar.Baz")
        using Alias = Foo.Bar.Type;         -> NamedBinding(local="Alias", ..., is_module_alias=True)
        global using Foo;                   -> NamedBinding(..., is_global=True)
        using static Foo.Bar;               -> NamedBinding(..., is_static_import=True)
        global using static Foo.Bar;        -> both flags True
    """
    alias: str | None = None
    namespace = ""
    is_global = False
    is_static = False
    saw_equals = False
    pending_identifier: str | None = None

    for child in stmt_node.children:
        if not child.is_named:
            tok = node_text(child, src)
            if tok == "global":
                is_global = True
                continue
            if tok == "static":
                is_static = True
                continue
            if tok == "=":
                saw_equals = True
                if pending_identifier is not None:
                    alias = pending_identifier
                    pending_identifier = None
                continue
            continue
        if child.type == "name_equals":
            for sub in child.children:
                if sub.type == "identifier":
                    alias = node_text(sub, src)
            continue
        if child.type == "identifier":
            text = node_text(child, src)
            if saw_equals:
                namespace = text
            else:
                pending_identifier = text
            continue
        if child.type == "qualified_name":
            namespace = node_text(child, src)

    if pending_identifier is not None and not namespace:
        namespace = pending_identifier

    if not namespace:
        return [], []
    local = alias if alias else namespace.rsplit(".", 1)[-1]
    return [local], [
        NamedBinding(
            local_name=local,
            exported_name=namespace,
            source_file=None,
            is_module_alias=alias is not None,
            is_global=is_global,
            is_static_import=is_static,
        )
    ]
