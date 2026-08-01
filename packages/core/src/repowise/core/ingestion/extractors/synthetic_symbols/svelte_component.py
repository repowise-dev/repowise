"""The component a ``.svelte`` file declares.

A Svelte component has no declaration in its own source — the file *is* the
component, and its name comes from the filename. Nothing in the ``<script>``
block names it, so the ordinary symbol pass finds nothing to hang the
component off, and ``<Button />`` in a parent's markup has no symbol to
resolve to.

This provider mints that symbol: one class-kind ``Symbol`` per component,
named after the file stem, spanning the file. It is the same shape of
compile-time-only name the Lombok and Java-record providers synthesise, so the
graph, dead-code, and wiki passes treat it identically.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node


def svelte_component_symbols(root: Node, src: str, file_info: FileInfo) -> list[Symbol]:
    """Return the single component symbol for a ``.svelte`` file."""
    name = PurePosixPath(file_info.path).stem
    if not name:
        return []

    # SvelteKit route files are named ``+page`` / ``+layout`` / ``+error``.
    # Strip the sigil so the symbol reads as a name rather than an operator;
    # the path already disambiguates which route it belongs to.
    name = name.lstrip("+")
    if not name or not (name[0].isalpha() or name[0] == "_"):
        return []

    end_line = root.end_point[0] + 1
    return [
        build_synthetic_symbol(
            name=name,
            kind="class",
            signature=f"<{name} />",
            start_line=1,
            end_line=end_line,
            file_info=file_info,
            parent_name=None,
        )
    ]
