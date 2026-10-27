"""C# 14 extension blocks, which the bundled C# grammar predates.

Inside a static class, the grammar parses ``extension(string value) { ... }``
as a ``constructor_declaration`` named ``extension`` and the members of the
block as ``local_function_statement`` nodes in its body, without an error:

  - the symbol pass emits one bogus method named ``extension``;
  - the members never become symbols, so nothing sees them.

A static class cannot declare an instance constructor, so the shape never
matches real code. Each member becomes a method of the holder class, written
like the classic extension method the compiler lowers it to:

  - ``public bool IsBlank()`` → ``IsBlank(this string value) -> bool``
  - ``public static string Create(int n)`` → ``Create(int n) -> string``

``drop_extension_block_symbols`` removes the bogus ``extension`` method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ..helpers import node_text
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node


_EXTENSION = "extension"
_ACCESS = ("public", "internal", "protected", "private")


def _modifiers(node: Node, src: str) -> list[str]:
    return [node_text(c, src).strip() for c in node.children if c.type == "modifier"]


def _extension_blocks(root: Node, src: str) -> Iterator[tuple[Node, str]]:
    """``(block, holder class name)`` for each extension block in the file."""
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            holder = node_text(name_node, src).strip() if name_node is not None else ""
            body = node.child_by_field_name("body")
            if holder and holder != _EXTENSION and "static" in _modifiers(node, src) and body:
                for member in body.children:
                    if member.type != "constructor_declaration":
                        continue
                    member_name = member.child_by_field_name("name")
                    # A block that does not parse cleanly (extension properties,
                    # a generic ``extension<T>``) keeps its current handling.
                    if (
                        member_name is not None
                        and node_text(member_name, src) == _EXTENSION
                        and not member.has_error
                    ):
                        yield member, holder
        stack.extend(node.children)


def _member_symbol(
    member: Node, receiver: str, holder: str, src: str, file_info: FileInfo
) -> Symbol | None:
    name_node = member.child_by_field_name("name")
    params_node = member.child_by_field_name("parameters")
    if name_node is None or params_node is None:
        return None
    name = node_text(name_node, src).strip()
    params = node_text(params_node, src).strip()
    modifiers = _modifiers(member, src)
    is_static = "static" in modifiers
    if not is_static:
        rest = params[1:-1].strip()
        params = f"(this {receiver}{', ' + rest if rest else ''})"
    type_node = member.child_by_field_name("type")
    returns = f" -> {node_text(type_node, src).strip()}" if type_node is not None else ""
    symbol = build_synthetic_symbol(
        name=name,
        kind="method",
        signature=f"{name}{params}{returns}",
        start_line=member.start_point[0] + 1,
        end_line=member.end_point[0] + 1,
        file_info=file_info,
        parent_name=holder,
        visibility=next((m for m in modifiers if m in _ACCESS), "private"),
    )
    # Every extension member compiles to a static method of the holder.
    symbol.modifiers = tuple(modifiers) if is_static else (*modifiers, "static")
    return symbol


def csharp_extension_block_symbols(root: Node, src: str, file_info: FileInfo) -> list[Symbol]:
    """Emit each member of an extension block as a method of its holder class."""
    out: list[Symbol] = []
    for block, holder in _extension_blocks(root, src):
        params_node = block.child_by_field_name("parameters")
        body = block.child_by_field_name("body")
        if params_node is None or body is None:
            continue
        receiver = node_text(params_node, src).strip()[1:-1].strip()
        if not receiver:
            continue
        for member in body.children:
            if member.type == "local_function_statement":
                symbol = _member_symbol(member, receiver, holder, src, file_info)
                if symbol is not None:
                    out.append(symbol)
    return out


def drop_extension_block_symbols(symbols: list[Symbol], root: Node, src: str) -> list[Symbol]:
    """Remove the ``extension`` method the symbol pass makes of each block."""
    blocks = {(holder, block.start_point[0] + 1) for block, holder in _extension_blocks(root, src)}
    if not blocks:
        return symbols
    return [
        s
        for s in symbols
        if not (
            s.name == _EXTENSION
            and s.kind == "method"
            and any(
                holder == s.parent_name and s.start_line <= line <= s.end_line
                for holder, line in blocks
            )
        )
    ]
