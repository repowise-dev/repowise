"""Delphi, FireMonkey and Lazarus form files: event handlers bound outside the code.

A form file (``.dfm``, ``.fmx``, ``.lfm``) names the methods of its unit's form
class that handle events: ``OnClick = Button1Click``. Nothing in the unit calls
them, so without the form they have no caller.

* the form file gets a file edge to the unit that shares its stem, naming the
  form class and every handler it binds;
* the form's module symbol gets a ``framework_binds`` edge to each handler.

Only text forms are read: a binary form starts with ``TPF0`` and is skipped.
A handler is kept when the unit's form class declares a method of that name,
compared case-insensitively since Pascal is, so a property value that is not an
event (``Align = alClient``) names no method and drops out. A form that
inherits from a form in another unit binds only the handlers its own unit's
class declares.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

from ..symbol_identity import module_symbol_id
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_symbol_edge,
    source_text,
)

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

FORM_EXTENSIONS = (".dfm", ".fmx", ".lfm")
_UNIT_EXTENSIONS = (".pas", ".pp")

# The root component: ``object MainForm: TMainForm``, or ``inherited`` / ``inline``.
_ROOT_RE = re.compile(r"^[ \t]*(?:object|inherited|inline)[ \t]+\w+[ \t]*:[ \t]*(\w+)", re.I | re.M)
# ``Name = Identifier`` alone on a line. A string, a number, a set, a list and
# a dotted name all fail the identifier test, and the lines of a string list or
# a binary blob never have the shape.
_ASSIGNMENT_RE = re.compile(r"^[ \t]*[\w.]+[ \t]*=[ \t]*([A-Za-z_]\w*)[ \t\r]*$", re.M)
_BINARY_MARK = "TPF0"


def form_handlers(text: str) -> tuple[str | None, list[str]]:
    """``(root class, identifiers assigned to properties)`` of a text form.

    The identifiers are candidates: an event handler, but also an enumeration
    value or a component reference. The caller keeps those the unit declares.
    A binary form yields no class and no identifiers.
    """
    if text.startswith(_BINARY_MARK) or "\x00" in text[:64]:
        return None, []
    root = _ROOT_RE.search(text)
    names: list[str] = []
    for match in _ASSIGNMENT_RE.finditer(text):
        name = match.group(1)
        if name.lower() != "nil" and name not in names:
            names.append(name)
    return (root.group(1) if root else None), names


def _unit_index(path_set: set[str]) -> dict[tuple[str, str], str]:
    """``(directory, stem)`` lowercased -> the unit file, for every Pascal unit."""
    index: dict[tuple[str, str], str] = {}
    for path in sorted(path_set):
        stem, ext = posixpath.splitext(posixpath.basename(path))
        if ext.lower() in _UNIT_EXTENSIONS:
            index.setdefault((posixpath.dirname(path).lower(), stem.lower()), path)
    return index


def _form_edges(graph: nx.DiGraph, form: str, text: str, unit: str, parsed: Any) -> int:
    root, candidates = form_handlers(text)
    if root is None:
        return 0
    wanted = {name.lower() for name in candidates}
    form_class = None
    handlers: dict[str, Any] = {}
    for sym in parsed.symbols:
        if sym.kind == "class" and sym.name.lower() == root.lower():
            form_class = sym
        elif (
            sym.kind == "method"
            and (sym.parent_name or "").lower() == root.lower()
            and sym.name.lower() in wanted
        ):
            handlers[sym.id] = sym
    if form_class is None:
        return 0
    names = [form_class.name, *sorted({s.name for s in handlers.values()})]
    count = int(_add_edge_if_new(graph, form, unit, names))
    module = module_symbol_id(form)
    return count + sum(add_symbol_edge(graph, module, symbol_id) for symbol_id in handlers)


class _PascalFormHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(path.lower().endswith(FORM_EXTENSIONS) for path in dctx.path_set)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        units = _unit_index(path_set)
        source_map = getattr(ctx, "source_map", None) or {}
        count = 0
        for form in sorted(path_set):
            if not form.lower().endswith(FORM_EXTENSIONS) or form not in parsed_files:
                continue
            stem = posixpath.splitext(posixpath.basename(form))[0]
            unit = units.get((posixpath.dirname(form).lower(), stem.lower()))
            if unit is None or unit not in parsed_files:
                continue
            text = source_text(form, parsed_files[form], source_map)
            count += _form_edges(graph, form, text, unit, parsed_files[unit])
        return count


HANDLERS: list[FrameworkHandler] = [_PascalFormHandler()]
