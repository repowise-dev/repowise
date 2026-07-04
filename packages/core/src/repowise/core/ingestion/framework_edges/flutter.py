"""Flutter navigation edges.

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
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
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
_CTOR_NAME_RE = re.compile(r"""([A-Z]\w*)\s*\(""")
_RUNAPP_WINDOW = 400


def _uses_flutter(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language != "dart":
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith(("package:flutter/", "package:go_router/")):
                return True
    return False


def _add_flutter_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("dart",))

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
                node = graph.nodes.get(target)
                if node is not None:
                    node["is_entry_point"] = True

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
