"""The component a single-file component declares.

An SFC has no declaration in its own source — the file *is* the component, and
its name comes from the filename. Nothing in the ``<script>`` block names it,
so the ordinary symbol pass finds nothing to hang the component off, and
``<Button />`` in a parent's markup has no symbol to resolve to.

This provider mints that symbol: one class-kind ``Symbol`` per component, named
after the file, spanning the file. It is the same shape of compile-time-only
name the Lombok and Java-record providers synthesise, so the graph, dead-code,
and wiki passes treat it identically.

Svelte and Vue derive the name differently. Svelte components are PascalCase by
convention and SvelteKit's routes carry a ``+`` sigil, so the stem is used
as-is. Vue tolerates ``warningBar.vue`` and ``back-to-top.vue`` while parents
still write ``<WarningBar />``, so the Vue stem is normalised through the same
rule the markup tags go through — see
:func:`~repowise.core.ingestion.sfc_source.vue_component_name_from_stem`.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ...sfc_source import vue_component_name_from_stem
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node


def _svelte_name(path: PurePosixPath) -> str:
    # SvelteKit route files are named ``+page`` / ``+layout`` / ``+error``.
    # Strip the sigil so the symbol reads as a name rather than an operator;
    # the path already disambiguates which route it belongs to.
    return path.stem.lstrip("+")


def _vue_name(path: PurePosixPath) -> str:
    return vue_component_name_from_stem(path.stem, path.parent.name)


def _razor_name(path: PurePosixPath) -> str:
    # Razor components are PascalCase by convention and the file *is* the
    # component, so the stem is the name; no kebab- or sigil-normalisation
    # needed (unlike SvelteKit's ``+page`` or Vue's ``back-to-top``).
    return path.stem


_NAMERS = {"svelte": _svelte_name, "vue": _vue_name, "razor": _razor_name}


def sfc_component_symbols(root: Node, src: str, file_info: FileInfo) -> list[Symbol]:
    """Return the single component symbol for a ``.svelte`` / ``.vue`` / ``.razor`` file."""
    path = PurePosixPath(file_info.path)
    if not path.stem:
        return []

    namer = _NAMERS.get(file_info.language)
    if namer is None:
        return []

    name = namer(path)
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
