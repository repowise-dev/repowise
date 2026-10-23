"""Lightning Web Components: template tags, template bindings and exposed bundles.

Three things reach an LWC bundle's code that no JS import says:

* a parent template's ``<c-child-name>`` tag instantiates the ``childName``
  bundle: a file edge from the template to the child's main module;
* a template's ``{handleClick}`` / ``{isOpen}`` binding runs that member of
  the bundle's own class: a ``framework_binds`` edge from the template's module
  symbol to the method or getter, so a handler only the template names has a
  caller;
* a ``.js-meta.xml`` with ``<isExposed>true</isExposed>`` or a ``<target>``
  lets App Builder, Experience Builder or a Flow place the component: its main
  module is anchored to ``framework:lwc`` and marked an entry point. Its
  ``configurationEditor="c-x-cpe"`` names the bundle Flow Builder loads to
  edit it: a file edge from the component to that editor.

Tags are read from raw text, not the HTML grammar, which splits
``<c-fsc_flow-combobox>`` at the underscore. The template is not linked to its
own module: a template does not make its component reachable, a parent does.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..framework_facts import LWC
from ..resolvers.lwc import (
    APEX_MODULE_PREFIXES,
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
_EXPOSED_RE = re.compile(r"<isExposed>\s*true\s*</isExposed>|<target>\s*\w", re.IGNORECASE)
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
    if ns.lower() not in {n.lower() for n in lwc_namespaces(ctx)}:
        return None
    name = lwc_tag_index(ctx).get(body.lower())
    target = nearest(lwc_bundles(ctx)[name], referrer) if name is not None else None
    # A component's own tag (recursion) says nothing about who reaches it.
    if target is None or lwc_bundle_dir(target) == lwc_bundle_dir(referrer):
        return None
    return target


def _template_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    bundles = lwc_bundles(ctx)
    source_map = getattr(ctx, "source_map", None) or {}
    count = 0
    for path in sorted(path_set):
        if not path.endswith(".html"):
            continue
        bundle_dir = lwc_bundle_dir(path)
        if bundle_dir is None:
            continue
        text = _COMMENT_RE.sub(" ", source_text(path, parsed_files[path], source_map))
        for m in _TAG_RE.finditer(text):
            target = _tag_target(m.group(1), m.group(2), path, ctx)
            if target is not None:
                count += _add_edge_if_new(graph, path, target)
        own = bundle_dir.rpartition("/")[2]
        main = next((p for p in bundles.get(own, ()) if lwc_bundle_dir(p) == bundle_dir), None)
        if main is None or main not in parsed_files:
            continue
        members = _members(parsed_files[main])
        module_sym = f"{path}::__module__"
        for name in {m.group(1) for m in _BINDING_RE.finditer(text)}:
            member = members.get(name)
            if member is not None:
                count += add_symbol_edge(graph, module_sym, member)
    return count


def _apex_method(apex: Any, cls: str, method: str) -> str | None:
    """The id of *cls*.*method* in a parsed Apex file, matched case-insensitively."""
    cls, method = cls.lower(), method.lower()
    ids = {
        s.id
        for s in apex.symbols
        if s.kind == "method" and s.name.lower() == method and (s.parent_name or "").lower() == cls
    }
    return ids.pop() if len(ids) == 1 else None


def _wire_edges(graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext) -> int:
    """``@wire(getThings, ...)`` with an Apex import binding -> that Apex method.

    An imperative ``getThings()`` is an ordinary call the resolver already
    binds through the import; the wire service invokes the method for the
    decorated member instead, so this is a ``framework_binds`` edge from the
    ``wire(...)`` call's caller (the class for a field, the method for a handler).
    """
    source_map = getattr(ctx, "source_map", None) or {}
    count = 0
    for path, parsed in parsed_files.items():
        if lwc_bundle_dir(path) is None or "@wire" not in source_text(path, parsed, source_map):
            continue
        bound: dict[str, str] = {}
        for imp in parsed.imports:
            spec = next((imp.module_path[len(p) :] for p in APEX_MODULE_PREFIXES
                         if imp.module_path.startswith(p)), None)  # fmt: skip
            apex = parsed_files.get(imp.resolved_file or "")
            if spec is None or apex is None:
                continue
            cls, _, method = spec.rpartition(".")
            target = _apex_method(apex, cls.rpartition(".")[2], method)
            if target is not None:
                bound.update((b.local_name, target) for b in imp.bindings if b.local_name)
        if not bound:
            continue
        callers = {c.line: c.caller_symbol_id for c in parsed.calls if c.target_name == "wire"}
        text = source_text(path, parsed, source_map)
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
            _template_edges(graph, parsed_files, ctx, path_set)
            + _wire_edges(graph, parsed_files, ctx)
            + _exposure_edges(graph, ctx)
        )


HANDLERS: list[FrameworkHandler] = [_LwcHandler()]
