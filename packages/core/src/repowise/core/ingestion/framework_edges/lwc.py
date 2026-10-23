"""Lightning Web Components: template tags, template bindings and exposed bundles.

* ``<c-child-name>`` in a template: a file edge to the child bundle's main module.
* ``{handleClick}`` in a template: a ``framework_binds`` edge to that class member.
* ``<isExposed>true</isExposed>`` in ``.js-meta.xml``: the main module is an entry
  point anchored to ``framework:lwc``; its ``configurationEditor`` gets a file edge.

Tags are read from raw text because the HTML grammar splits them at ``_``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..framework_facts import LWC
from ..resolvers.lwc import (
    apex_specifier,
    lwc_bundle_dir,
    lwc_bundles,
    lwc_namespaces,
    lwc_tag_index,
    nearest,
)
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_symbol_edge,
    anchor_edge,
    source_text,
)

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# `<ns-rest`: the namespace ends at the first hyphen.
_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9]*)-([A-Za-z0-9_-]+)")
# `{name}` / `{name.field}`: the leading identifier is a member of the class.
_BINDING_RE = re.compile(r"\{\s*([A-Za-z_$][\w$]*)")
_WIRE_RE = re.compile(r"@wire\s*\(\s*([A-Za-z_$][\w$]*)")
# Targets are ignored unless the bundle is exposed.
_EXPOSED_RE = re.compile(r"<isExposed>\s*true\s*</isExposed>", re.IGNORECASE)
# `for:item="x"`, `for:index="i"`, `iterator:it={...}`: template-local names.
_LOOP_VAR_RE = re.compile(r"""for:(?:item|index)\s*=\s*["']([A-Za-z_$][\w$]*)|iterator:([A-Za-z_$][\w$]*)""")
# A `<template ...>` open tag or a `</template>` close tag (loops live on templates).
# Quoted attribute values may hold `>`; `<template-x>` is a custom element, not a template.
_TEMPLATE_TAG_RE = re.compile(
    r"""<template(?=[\s>/])(?:[^>"']|"[^"]*"|'[^']*')*>|</template\s*>""", re.IGNORECASE
)
# A Flow screen component's property editor, named by tag in its targetConfig.
_EDITOR_RE = re.compile(r"""configurationEditor\s*=\s*["']([A-Za-z][A-Za-z0-9]*)-([A-Za-z0-9_-]+)["']""")


def _members(parsed: Any) -> dict[str, str]:
    """Method and accessor name -> symbol id for the module's top-level classes."""
    classes = {s.name for s in parsed.symbols if s.kind == "class" and not s.parent_name}
    return {
        s.name: s.id
        for s in parsed.symbols
        if s.kind == "method" and s.parent_name in classes
    }


def _tag_target(ns: str, body: str, referrer: str, ctx: ResolverContext) -> str | None:
    """The main module a ``<ns-body>`` tag written in *referrer* instantiates, or None.

    HTML tag names are case-insensitive; kebab names are lowercase, so is the lookup.
    """
    if ns.lower() not in lwc_namespaces(ctx):
        return None
    name = lwc_tag_index(ctx).get(body.lower())
    target = nearest(lwc_bundles(ctx)[name], referrer) if name is not None else None
    # A component's own tag (recursion) says nothing about who reaches it.
    if target is None or lwc_bundle_dir(target) == lwc_bundle_dir(referrer):
        return None
    return target


def _loop_scopes(text: str) -> list[tuple[int, int, frozenset[str]]]:
    """``(start, end, names)`` per loop template: the span where its variables are visible.

    The span runs from the end of the ``<template for:each ...>`` open tag to
    its matching ``</template>``, nested templates balanced on a stack. An
    unclosed one runs to the end of the text.
    """
    scopes: list[tuple[int, int, frozenset[str]]] = []
    stack: list[tuple[int, frozenset[str]]] = []
    for m in _TEMPLATE_TAG_RE.finditer(text):
        if m.group().startswith("</"):
            if stack:
                start, names = stack.pop()
                scopes.append((start, m.start(), names))
            continue
        names = frozenset(a or b for a, b in _LOOP_VAR_RE.findall(m.group()))
        stack.append((m.end(), names))
    scopes.extend((start, len(text), names) for start, names in stack)
    return [scope for scope in scopes if scope[2]]


def _binding_edges(graph: nx.DiGraph, template: str, text: str, main: Any) -> int:
    """``{member}`` in *template* -> that method or getter of the bundle's class.

    A binding inside a loop that declares the same name (``for:item``,
    ``for:index``, ``iterator:``) reads the loop variable, not the member.
    """
    members = _members(main)
    scopes = _loop_scopes(text)
    names = {
        m.group(1)
        for m in _BINDING_RE.finditer(text)
        if m.group(1) in members
        and not any(s <= m.start() < e and m.group(1) in local for s, e, local in scopes)
    }
    module_sym = f"{template}::__module__"
    return sum(add_symbol_edge(graph, module_sym, members[n]) for n in names)


def _template_edges(graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext) -> int:
    bundles = lwc_bundles(ctx)
    source_map = getattr(ctx, "source_map", None) or {}
    count = 0
    for path in sorted(parsed_files):
        bundle_dir = lwc_bundle_dir(path) if path.endswith(".html") else None
        if bundle_dir is None:
            continue
        text = _COMMENT_RE.sub(" ", source_text(path, parsed_files[path], source_map))
        for m in _TAG_RE.finditer(text):
            target = _tag_target(m.group(1), m.group(2), path, ctx)
            if target is not None:
                count += _add_edge_if_new(graph, path, target)
        own = bundle_dir.rpartition("/")[2]
        main = next((p for p in bundles.get(own, ()) if lwc_bundle_dir(p) == bundle_dir), None)
        if main in parsed_files:
            count += _binding_edges(graph, path, text, parsed_files[main])
    return count


def _apex_method(apex: Any, cls: str, method: str) -> str | None:
    """The id of *cls*.*method* in a parsed Apex file, matched case-insensitively."""
    cls, method = cls.lower(), method.lower()
    found = [
        s
        for s in apex.symbols
        if s.kind == "method" and s.name.lower() == method and (s.parent_name or "").lower() == cls
    ]
    # Only an @AuraEnabled method is callable from LWC, which settles an overload.
    aura = [s for s in found if any(d.lower().startswith("@auraenabled") for d in s.decorators)]
    ids = {s.id for s in (aura or found)}
    return ids.pop() if len(ids) == 1 else None


def _apex_bindings(parsed: Any, parsed_files: dict[str, Any]) -> dict[str, str]:
    """Local name -> Apex method id for each ``@salesforce/apex`` import that resolved."""
    bound: dict[str, str] = {}
    for imp in parsed.imports:
        spec = apex_specifier(imp.module_path)
        apex = parsed_files.get(imp.resolved_file or "")
        if spec is None or apex is None:
            continue
        target = _apex_method(apex, spec[1], spec[2])
        if target is not None:
            bound.update((b.local_name, target) for b in imp.bindings if b.local_name)
    return bound


def _wire_edges(graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext) -> int:
    """``@wire(getThings, ...)`` with an Apex import binding -> that Apex method.

    The edge starts at the ``wire(...)`` call's caller: the class for a field,
    the method for a handler.
    """
    source_map = getattr(ctx, "source_map", None) or {}
    count = 0
    for path, parsed in parsed_files.items():
        if lwc_bundle_dir(path) is None:
            continue
        text = source_text(path, parsed, source_map)
        bound = _apex_bindings(parsed, parsed_files) if "@wire" in text else None
        if not bound:
            continue
        callers = {c.line: c.caller_symbol_id for c in parsed.calls if c.target_name == "wire"}
        for m in _WIRE_RE.finditer(text):
            caller = callers.get(text.count("\n", 0, m.start()) + 1)
            target = bound.get(m.group(1))
            if caller is not None and target is not None:
                count += add_symbol_edge(graph, caller, target)
    return count


def _exposure_edges(graph: nx.DiGraph, ctx: ResolverContext) -> int:
    if ctx.repo_path is None:
        return 0
    count = 0
    for name, mains in lwc_bundles(ctx).items():
        for main in mains:
            meta = f"{main.rpartition('/')[0]}/{name}.js-meta.xml"
            try:
                text = (ctx.repo_path / meta).read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            text = _COMMENT_RE.sub(" ", text)
            for m in _EDITOR_RE.finditer(text):
                editor = _tag_target(m.group(1), m.group(2), main, ctx)
                if editor is not None:
                    count += _add_edge_if_new(graph, main, editor)
            if not _EXPOSED_RE.search(text):
                continue
            count += anchor_edge(graph, LWC, main)
            node = graph.nodes.get(main)
            if node is not None:
                node["is_entry_point"] = True
    return count


class _LwcHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return bool(lwc_bundles(dctx.ctx))

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return (
            _template_edges(graph, parsed_files, ctx)
            + _wire_edges(graph, parsed_files, ctx)
            + _exposure_edges(graph, ctx)
        )


HANDLERS: list[FrameworkHandler] = [_LwcHandler()]
