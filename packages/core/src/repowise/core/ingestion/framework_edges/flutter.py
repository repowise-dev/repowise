"""Flutter framework edges: navigation + widget tree.

Flutter wires screens together through route tables and builder callbacks
rather than direct imports of a call site — a widget referenced only from
``MaterialApp(routes: {...})`` or a ``GoRoute(builder: ...)`` has no
in-source caller, so the dead-code pass would flag every page. Two shapes
cover the minimal viable set (DI/codegen frameworks like riverpod/get_it
are deliberately out of scope here — they need dynamic hints, not regex):

- Route tables and builders: ``'/cart': (context) => CartPage()`` and
  ``GoRoute(builder: (context, state) => DetailsPage(...))`` /
  ``MaterialPageRoute(builder: (context) => EditPage())`` — edge from the
  route-owning file to the page widget's defining file.
- ``runApp(MyApp())`` — edge to the root widget's file, which is also
  stamped ``is_entry_point``.

Plus the widget tree itself (issue #142): a ``build()`` method returns a
widget tree, so every constructor call in the body names a *child widget*
of the building class. The body is read by matching braces from the
signature rather than through a fixed window, so a long ``build()`` keeps
its tail. Only classes whose heritage makes them widgets become targets,
without which any repo class spelled like a constructor call
(``Repository(...)``) would mint an edge, and a name resolves only to a
file this one imports, which is also what settles a name two files declare.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext, resolve_import
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_class_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx

# '/route': (context) => CartPage(   — MaterialApp/CupertinoApp routes maps.
_ROUTE_ENTRY_RE = re.compile(
    r"""['"]/[\w/:\-]*['"]\s*:\s*\([^)]*\)\s*=>\s*(?:const\s+)?([A-Z]\w*)\s*\("""
)
# GoRoute / MaterialPageRoute / CupertinoPageRoute / showDialog builders —
# arrow form (=> EditPage(...)) and single-return block form
# ({ ... return EditPage(...); }, no nested braces before the return).
_BUILDER_ARROW_RE = re.compile(
    r"""(?:page)?[bB]uilder\s*:\s*\([^)]*\)\s*=>\s*(?:const\s+)?([A-Z]\w*)\s*\("""
)
_BUILDER_BLOCK_RE = re.compile(
    r"""(?:page)?[bB]uilder\s*:\s*\([^)]*\)\s*\{[^{}]*?return\s+(?:const\s+)?([A-Z]\w*)\s*\(""",
    re.S,
)
# runApp(...) — the root widget may be wrapped (MultiProvider(child: MainApp()));
# collect every constructor-looking name in the argument window and let the
# local class map decide which are this repo's widgets.
_RUNAPP_RE = re.compile(r"""runApp\s*\(""")
# A constructor call: the class name, an optional type-argument list
# (``Foo<int>(``, one level of nesting) and an optional named constructor
# (``Foo.named(``). Group 1 is the class either suffix hangs off, and must
# start the identifier: private ``_AppSearchBar(`` otherwise reads as a call
# to the unrelated public ``AppSearchBar``.
_CTOR_NAME_RE = re.compile(
    r"""(?<![\w$])([A-Z]\w*)(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?(?:\.[A-Za-z_]\w*)?\s*\("""
)
_RUNAPP_WINDOW = 400

# The widget tree: Widget build(BuildContext context) { ... } or => ... ;
_BUILD_METHOD_RE = re.compile(r"""\bWidget\s+build\s*\(\s*[^)]*\)\s*(?:=>|\{)""")

# Flutter's own widget supertypes. A repo class extending, mixing in or
# implementing one of these is a widget, and so is one whose parent is such
# a class. Two hops cover ``PageBase extends StatelessWidget``; a longer
# chain stays unresolved and emits no edge.
_WIDGET_BASES = frozenset({
    "StatelessWidget",
    "StatefulWidget",
    "State",
    "InheritedWidget",
    "RenderObjectWidget",
    "PreferredSizeWidget",
    "Widget",
})

_CLASS_KINDS = ("class", "interface", "struct", "record", "enum", "trait")


def _uses_flutter(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language != "dart":
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith(("package:flutter/", "package:go_router/")):
                return True
    return False


def _widget_class_names(parsed_files: dict[str, Any]) -> set[str]:
    """Repo class names whose heritage makes them Flutter widgets."""
    parents: dict[str, set[str]] = {}
    for parsed in parsed_files.values():
        if parsed.file_info.language != "dart":
            continue
        for rel in parsed.heritage:
            parents.setdefault(rel.child_name, set()).add(rel.parent_name)
    direct = {name for name, bases in parents.items() if bases & _WIDGET_BASES}
    return direct | {name for name, bases in parents.items() if bases & direct}


def _class_owners(parsed_files: dict[str, Any]) -> dict[str, set[str]]:
    """Map a declared Dart class name to every file declaring it."""
    owners: dict[str, set[str]] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "dart":
            continue
        for sym in parsed.symbols:
            if sym.kind in _CLASS_KINDS:
                owners.setdefault(sym.name, set()).add(path)
    return owners


def _skip_string(text: str, start: int) -> int:
    """Index just past the string literal opening at *start*."""
    quote = text[start]
    delim = quote * 3 if text.startswith(quote * 3, start) else quote
    raw = start > 0 and text[start - 1] == "r"
    i = start + len(delim)
    while i < len(text):
        if not raw and text[i] == "\\":
            i += 2
            continue
        if not raw and text.startswith("${", i):
            i = _skip_interpolation(text, i + 2)
            continue
        if text.startswith(delim, i):
            return i + len(delim)
        i += 1
    return i


def _skip_interpolation(text: str, start: int) -> int:
    """Index just past the ``}`` closing a ``${...}`` interpolation."""
    depth = 1
    i = start
    while i < len(text):
        char = text[i]
        if char in "'\"":
            i = _skip_string(text, i)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _build_body(text: str, start: int, arrow: bool) -> str:
    """The build() body from *start*, with strings and comments blanked out.

    Block form ends at the ``}`` matching the signature's ``{``, arrow form
    at the ``;`` closing the expression. Blanking literals keeps a class
    name quoted inside a message from reading as a constructor call.
    """
    out: list[str] = []
    depth = 0
    i = start
    end = len(text)
    while i < end:
        char = text[i]
        if char == "/" and text.startswith("//", i):
            newline = text.find("\n", i)
            i = end if newline < 0 else newline
            continue
        if char == "/" and text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = end if close < 0 else close + 2
            out.append(" ")
            continue
        if char in "'\"":
            i = _skip_string(text, i)
            out.append(" ")
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                break
        elif arrow and char == ";" and depth == 0:
            break
        out.append(char)
        i += 1
    return "".join(out)


def _imported_paths(parsed: Any, path: str, ctx: ResolverContext) -> set[str] | None:
    """Repo files this file imports, or ``None`` when that cannot be known.

    A ``part of`` file sees its library's imports, which are not written on
    the file itself, so its own import list cannot settle an ambiguous name.
    """
    resolved: set[str] = set()
    for imp in parsed.imports:
        if imp.module_path.startswith("library:"):
            return None
        target = resolve_import(imp.module_path, path, "dart", ctx)
        if target:
            resolved.add(target)
    return resolved


def _child_target(owners: set[str], imports: set[str] | None) -> str | None:
    """The one imported file declaring a child widget, else ``None``.

    Dart makes nothing visible across files without an import, so requiring
    one both settles a name two files declare and refuses a repo class that
    merely shadows a framework widget. A class reached through an ``export``
    barrel is missed, which costs an edge rather than inventing one.
    """
    if imports is None:
        return None
    named = owners & imports
    return next(iter(named)) if len(named) == 1 else None


def _add_flutter_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("dart",))
    widget_classes = _widget_class_names(parsed_files)
    owners_by_name = _class_owners(parsed_files)

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "dart":
            continue
        text = read_text(parsed)
        if not text:
            continue

        entry_widgets: set[str] = set()
        for m in _RUNAPP_RE.finditer(text):
            window = text[m.end() : m.end() + _RUNAPP_WINDOW]
            entry_widgets |= set(_CTOR_NAME_RE.findall(window))
        routed_widgets = {m.group(1) for m in _ROUTE_ENTRY_RE.finditer(text)}
        routed_widgets |= {m.group(1) for m in _BUILDER_ARROW_RE.finditer(text)}
        routed_widgets |= {m.group(1) for m in _BUILDER_BLOCK_RE.finditer(text)}
        routed_widgets |= entry_widgets

        for widget in routed_widgets:
            target = class_to_file.get(widget)
            if target is None or target not in path_set:
                continue
            if _add_edge_if_new(graph, path, target):
                count += 1
            if widget in entry_widgets:
                # The runApp target is usually the same file (MyApp lives in
                # main.dart), so no edge is added and the node may not exist
                # yet, so create it before stamping.
                if target not in graph:
                    graph.add_node(target)
                graph.nodes[target]["is_entry_point"] = True

        # Widget tree: every constructor call inside a build() body names a
        # child widget of this file's widget class. Framework widgets
        # (Scaffold, Column, Text, ...) declare nothing in the repo and are
        # skipped; repo widgets get a parent→child edge.
        imports: set[str] | None = None
        imports_read = False
        for m in _BUILD_METHOD_RE.finditer(text):
            body = _build_body(text, m.end(), arrow=m.group(0).endswith("=>"))
            for child in _CTOR_NAME_RE.findall(body):
                if child not in widget_classes:
                    continue
                owners = owners_by_name.get(child)
                if not owners:
                    continue
                if not imports_read:
                    imports = _imported_paths(parsed, path, ctx)
                    imports_read = True
                target = _child_target(owners, imports)
                if target is None or target not in path_set:
                    continue
                if _add_edge_if_new(graph, path, target):
                    count += 1

    return count


class _FlutterHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return _uses_flutter(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_flutter_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_FlutterHandler()]
