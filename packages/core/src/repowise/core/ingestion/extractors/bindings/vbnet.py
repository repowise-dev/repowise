"""VB.NET import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_vbnet_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from VB.NET ``Imports`` statements.

    Recognises:
        Imports Foo.Bar.Baz         -> NamedBinding(local="Baz", exported="Foo.Bar.Baz")
        Imports Alias = Foo.Bar     -> NamedBinding(local="Alias", ..., is_module_alias=True)

    The v0.1.0 grammar fails to parse the ``Alias =`` prefix of an aliased
    import (it recovers into an ERROR node), but the real target namespace
    still lands on the statement's ``namespace:`` field — see
    docs/architecture/vbnet-support.md D1. The alias name is recovered by
    scanning the statement's raw text rather than the ERROR node's internal
    shape, which tree-sitter makes no stability guarantees about.
    """
    namespace_node = stmt_node.child_by_field_name("namespace")
    if namespace_node is None:
        return [], []
    namespace = node_text(namespace_node, src)
    if not namespace:
        return [], []

    alias: str | None = None
    prefix = src[stmt_node.start_byte : namespace_node.start_byte]
    if "=" in prefix:
        head = prefix.split("=", 1)[0]
        # Strip the leading `Imports` keyword, keep the last identifier-like
        # token before `=` as the alias.
        tokens = [t for t in head.replace("\t", " ").split(" ") if t]
        if tokens and tokens[0].lower() == "imports":
            tokens = tokens[1:]
        if tokens:
            alias = tokens[-1]

    local = alias if alias else namespace.rsplit(".", 1)[-1]
    return [local], [
        NamedBinding(
            local_name=local,
            exported_name=namespace,
            source_file=None,
            is_module_alias=alias is not None,
        )
    ]
