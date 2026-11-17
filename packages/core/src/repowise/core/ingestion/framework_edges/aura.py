"""Salesforce Aura bundle edges.

An Aura bundle ``aura/<name>/`` (markup plus ``<name>Controller.js``,
``Helper.js``, ``Renderer.js``) is wired by name, not imports. Edges:

* ``controller="Cls"`` -> the Apex class; ``get("c.m")`` in bundle JS -> its
  method ``m``, or the Controller.js function when the class has none.
* ``{!c.h}`` and other ``"c.h"`` literals -> Controller.js functions;
  ``helper.x(`` -> Helper.js functions. Both inherit through ``extends``.
* Own-namespace ``<c:X>``, ``extends``, ``implements`` and event types -> the
  Aura bundle ``X``, else the LWC bundle; ``$A.createComponent(s)`` literals
  -> ``dynamic_uses``.

Aura names match by exact case; Apex names fold case and follow the Apex reach
rules (``languages.apex_namespace``). A name with two candidates binds neither.
An ``.app`` and a component implementing a placement interface are entry
points, and a Renderer's overrides are bound from the markup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from ..extractors.synthetic_symbols.aura_bundle_js import aura_js_role
from ..languages.apex_namespace import apex_namespaces
from ..languages.specs.aura import aura_bundle
from ..resolvers.lwc import lwc_namespaces, nearest
from ..type_names import bare_type_name
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

# The bundle's defining markup, in the order the platform allows one of them.
_MAIN_MARKUP = (".cmp", ".app", ".evt", ".intf")

_XML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
# A JS comment, or a string literal kept as-is so a `//` inside one is not a comment.
_JS_COMMENT_OR_STRING_RE = re.compile(
    r"//[^\n]*|/\*.*?(?:\*/|\Z)|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`",
    re.DOTALL,
)
_TAG_RE = re.compile(r"""<([A-Za-z]\w*):([A-Za-z_]\w*)((?:[^>"']|"[^"]*"|'[^']*')*)>""")
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_QUALIFIED_RE = re.compile(r"^\s*(\w+):(\w+)\s*$")
# Formatters reflow `{!c.h}` and append a `;`. Ceiling: `{!v.x ? c.a : c.b}` is not read.
_MARKUP_HANDLER_RE = re.compile(r"\{\s*[!#]\s*c\.([A-Za-z_]\w*)\s*;?\s*\}")
# A "c.x" string literal; group 1 is set when it is the argument of `.get(`.
_ACTION_RE = re.compile(r"""(\.get\(\s*)?(["'])c\.([A-Za-z_]\w*)\2""")
_HELPER_CALL_RE = re.compile(r"(?<![\w.$])helper\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
# Inside a helper, `this` is the (inherited) helper object.
_THIS_CALL_RE = re.compile(r"(?<![\w.$])this\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
_CREATE_ONE_RE = re.compile(r"""\$A\s*\.\s*createComponent\s*\(\s*(["'`])(\w+):(\w+)\1""")
_CREATE_MANY_RE = re.compile(r"\$A\s*\.\s*createComponents\s*\(")
_CREATE_MANY_ITEM_RE = re.compile(r"""\[\s*(["'`])(\w+):(\w+)\1""")

# Interfaces that let an admin place the component without any source naming it.
_ENTRY_INTERFACE_PREFIXES = ("flexipage:", "forcecommunity:", "lightningsnapin:")
_ENTRY_INTERFACES = frozenset(
    {
        "force:apphostable",
        "force:lightningquickaction",
        "force:lightningquickactionwithoutheader",
        "lightning:actionoverride",
        "lightning:isurladdressable",
        "lightning:availableforflowscreens",
        "lightning:availableforflowactions",
        "lightning:hometemplate",
        "lightning:apphometemplate",
        "lightning:recordhometemplate",
        "lightning:utilityitem",
        "lightning:backgroundutilityitem",
        "clients:availableformailappapppage",
    }
)
_RENDERER_OVERRIDES = ("render", "rerender", "afterRender", "unrender")
# Controller functions a platform interface obliges the component to define:
# the flow runtime runs a local action's `invoke`.
_PLATFORM_INTERFACE_METHODS = {"lightning:availableforflowactions": ("invoke",)}
_EXTENDS_DEPTH = 6


@dataclass
class _Bundle:
    name: str
    markup: dict[str, str] = field(default_factory=dict)  # suffix -> path
    js: dict[str, str] = field(default_factory=dict)  # role -> path

    @property
    def main(self) -> str | None:
        return next((self.markup[s] for s in _MAIN_MARKUP if s in self.markup), None)


def _mask_xml(text: str) -> str:
    return _XML_COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _mask_js_comments(text: str) -> str:
    def blank(m: re.Match[str]) -> str:
        s = m.group(0)
        return s if s[0] in "'\"`" else re.sub(r"[^\n]", " ", s)

    return _JS_COMMENT_OR_STRING_RE.sub(blank, text)


def _attrs(blob: str) -> dict[str, str]:
    return {m.group(1): m.group(2) if m.group(2) is not None else m.group(3) for m in _ATTR_RE.finditer(blob)}


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _call_span_end(text: str, open_paren: int) -> int:
    """Offset just past the ``)`` closing the call whose ``(`` is at *open_paren*."""
    depth = 0
    i = open_paren
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"`":
            m = _JS_COMMENT_OR_STRING_RE.match(text, i)
            i = m.end() if m else i + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


class _AuraIndex:
    def __init__(self, graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext) -> None:
        self.graph = graph
        self.parsed_files = parsed_files
        self.source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
        self.own_ns = lwc_namespaces(ctx)
        self.apex = apex_namespaces(ctx)
        self.bundles: dict[str, _Bundle] = {}  # bundle dir -> bundle
        self.lwc_by_name: dict[str, list[str]] = {}
        for path in sorted(parsed_files):
            self._add_file(path, parsed_files[path].file_info.language)
        self.aura_by_name: dict[str, list[str]] = {}
        for bdir, b in self.bundles.items():
            if b.main is not None:
                self.aura_by_name.setdefault(b.name, []).append(bdir)
        self._root_attrs: dict[str, dict[str, str]] = {}
        self._functions: dict[str, dict[str, str]] = {}

    def _add_file(self, path: str, lang: str) -> None:
        bundle = aura_bundle(path)
        if bundle is None:
            parts = PurePosixPath(path).parts
            # LWC main module: lwc/<name>/<name>.js. Only the location is read
            # here; the LWC surface itself is not this module's.
            if lang == "javascript" and len(parts) >= 3 and parts[-3] == "lwc" and parts[-1] == f"{parts[-2]}.js":
                self.lwc_by_name.setdefault(parts[-2], []).append(path)
            return
        bdir, name = bundle
        if lang == "aura":
            self.bundles.setdefault(bdir, _Bundle(name)).markup[PurePosixPath(path).suffix.lower()] = path
        elif lang == "javascript" and (role := aura_js_role(path)) is not None:
            self.bundles.setdefault(bdir, _Bundle(name)).js[role] = path

    # -- lookups -----------------------------------------------------------

    def text(self, path: str) -> str:
        return source_text(path, self.parsed_files[path], self.source_map, "utf-8-sig")

    def root_attrs(self, bdir: str) -> dict[str, str]:
        """Attributes of the bundle's root ``<aura:*>`` tag."""
        if bdir not in self._root_attrs:
            attrs: dict[str, str] = {}
            main = self.bundles[bdir].main
            if main is not None:
                for m in _TAG_RE.finditer(_mask_xml(self.text(main))):
                    if m.group(1) == "aura" and m.group(2) in (
                        "component", "application", "interface", "event"
                    ):
                        attrs = _attrs(m.group(3))
                        break
            self._root_attrs[bdir] = attrs
        return self._root_attrs[bdir]

    def own_name(self, qualified: str) -> str | None:
        """``X`` for an own-namespace ``ns:X``, else None."""
        m = _QUALIFIED_RE.match(qualified)
        if m is None or m.group(1).lower() not in self.own_ns:
            return None
        return m.group(2)

    def bundle_target(self, name: str, src: str) -> str | None:
        """The file a ``c:name`` reference from *src* lands on: Aura main markup, else LWC main js."""
        mains = [m for d in self.aura_by_name.get(name, []) if (m := self.bundles[d].main)]
        return nearest(mains + self.lwc_by_name.get(name, []), src)

    def apex_class(self, written: str, src: str) -> str | None:
        """The Apex file of ``controller="[ns.]Cls"`` written in *src*."""
        return self.apex.referred_file(src, written)

    def aura_bundle_dir(self, name: str, src: str) -> str | None:
        """The bundle dir an own-namespace ``extends``/``implements`` name from *src* means."""
        return nearest(self.aura_by_name.get(name, []), src)

    def chain(self, bdir: str) -> list[str]:
        """The bundle and the bundles it ``extends``, nearest first.

        A sub-component inherits its parent's Apex controller, client-side
        controller functions and helper, so a name the bundle does not declare
        is looked up the chain. Stops at a platform base, an ambiguous name or
        a cycle.
        """
        out = [bdir]
        while len(out) < _EXTENDS_DEPTH:
            base = self.own_name(self.root_attrs(out[-1]).get("extends", ""))
            parent = self.aura_bundle_dir(base, out[-1]) if base else None
            if parent is None or parent in out:
                break
            out.append(parent)
        return out

    def controller_class(self, bdir: str) -> tuple[str, str] | None:
        """``(written name, class file)`` of the bundle's Apex controller, via ``extends``."""
        for b in self.chain(bdir):
            written = self.root_attrs(b).get("controller")
            if written:
                cls_file = self.apex_class(written, b)
                return (written.strip(), cls_file) if cls_file else None
        return None

    def bundle_function(self, bdir: str, role: str, name: str) -> tuple[str, str, str] | None:
        """``(file, symbol id, name)`` of function *name* in the *role* JS up the extends chain."""
        for b in self.chain(bdir):
            path = self.bundles[b].js.get(role)
            sym = self.functions(path).get(name)
            if path is not None and sym is not None:
                return path, sym, name
        return None

    def interface_methods(self, bdir: str) -> set[str]:
        """``aura:method`` names declared with no ``action`` by the bundle's own interfaces."""
        names: set[str] = set()
        for b in self.chain(bdir):
            written = self.root_attrs(b).get("implements", "")
            for item in written.split(","):
                intf = self.own_name(item)
                idir = self.aura_bundle_dir(intf, b) if intf else None
                if idir is not None:
                    main = self.bundles[idir].main
                    if main is not None:
                        names |= _default_action_methods(_mask_xml(self.text(main)))
        return names

    def functions(self, path: str | None) -> dict[str, str]:
        """``{name: symbol id}`` of a JS file's (or Apex class's) functions and methods."""
        if path is None or path not in self.parsed_files:
            return {}
        if path not in self._functions:
            self._functions[path] = {
                s.name: s.id
                for s in self.parsed_files[path].symbols
                if s.kind in ("function", "method")
            }
        return self._functions[path]

    def apex_method(self, cls_file: str, written_cls: str, method: str) -> str | None:
        """Symbol id of *method* declared directly on the controller class (case folded)."""
        cls = bare_type_name(written_cls).lower()
        hits = [
            s
            for s in self.parsed_files[cls_file].symbols
            if s.kind == "method"
            and s.name.lower() == method.lower()
            and (s.parent_name or "").lower() == cls
        ]
        # An Aura action is public or global, so a private overload of the same
        # name is never the one the component calls.
        hits.sort(key=lambda s: s.visibility == "private")
        return hits[0].id if hits else None

    def enclosing(self, path: str, line: int) -> str:
        """Innermost symbol of *path* spanning *line*, else its ``__module__``.

        Line granularity: in a one-line bundle with several handlers the later
        one is credited. Bundle JS is written one handler per line block.
        """
        best: Any = None
        for s in self.parsed_files[path].symbols:
            if s.start_line <= line <= s.end_line and (
                best is None or s.start_line >= best.start_line
            ):
                best = s
        return best.id if best is not None else f"{path}::__module__"


def _file_edge(graph: nx.DiGraph, src: str, dst: str, names: set[str]) -> int:
    """A ``framework`` file edge vouching for *names*, merged into an earlier one."""
    if src == dst or src not in graph or dst not in graph:
        return 0
    existing = graph.get_edge_data(src, dst)
    # An edge with no names already vouches for every member; one of another
    # type is left alone (first wins, as in `_add_edge_if_new`).
    if existing is not None:
        if existing.get("edge_type") == "framework" and existing.get("imported_names"):
            merged = set(existing["imported_names"]) | names
            existing["imported_names"] = sorted(merged)
        return 0
    return int(_add_edge_if_new(graph, src, dst, sorted(names)))


def _dynamic_edge(graph: nx.DiGraph, src: str, dst: str) -> int:
    if src == dst or src not in graph or dst not in graph or graph.has_edge(src, dst):
        return 0
    # Literal but runtime-built: the dynamic tier, never `framework`.
    graph.add_edge(
        src,
        dst,
        edge_type="dynamic_uses",
        hint_source="aura_create_component",
        weight=1.0,
        imported_names=[],
    )
    return 1


def _default_action_methods(text: str) -> set[str]:
    """``aura:method`` names with no ``action``: those run the controller function of that name."""
    out: set[str] = set()
    for m in _TAG_RE.finditer(text):
        if m.group(1) == "aura" and m.group(2) == "method":
            attrs = _attrs(m.group(3))
            if "action" not in attrs and attrs.get("name"):
                out.add(attrs["name"])
    return out


class _Edges:
    """File edges gathered per (source, target) so each carries every name it vouches for."""

    def __init__(self, graph: nx.DiGraph) -> None:
        self.graph = graph
        self.names: dict[tuple[str, str], set[str]] = {}
        self.count = 0

    def file(self, src: str, dst: str, names: set[str] | None = None) -> None:
        self.names.setdefault((src, dst), set()).update(names or ())

    def bind(self, src: str, target: tuple[str, str, str], src_file: str) -> None:
        """Bind symbol *src* to ``(file, symbol, name)`` *target* and note the file edge."""
        path, sym, name = target
        self.count += add_symbol_edge(self.graph, src, sym)
        self.file(src_file, path, {name})

    def flush(self) -> int:
        for (src, dst), names in sorted(self.names.items()):
            self.count += _file_edge(self.graph, src, dst, names)
        return self.count


def _is_placeable(main: str, implements: list[str]) -> bool:
    """An ``.app``, or a component an admin can place without any source naming it."""
    return main.endswith(".app") or any(
        i.lower() in _ENTRY_INTERFACES or i.lower().startswith(_ENTRY_INTERFACE_PREFIXES)
        for i in implements
    )


def _root_edges(idx: _AuraIndex, bdir: str, main: str, edges: _Edges) -> None:
    """Edges the root tag of the bundle's main markup declares."""
    attrs = idx.root_attrs(bdir)
    cls = attrs.get("controller")
    cls_file = idx.apex_class(cls, main) if cls else None
    if cls_file is not None:
        edges.file(main, cls_file, {bare_type_name(cls)})
    implements = [i.strip() for i in attrs.get("implements", "").split(",") if i.strip()]
    if _is_placeable(main, implements):
        idx.graph.nodes[main]["is_entry_point"] = True
    for written in [attrs.get("extends", ""), *implements]:
        name = idx.own_name(written)
        target = idx.bundle_target(name, main) if name else None
        if target is not None:
            edges.file(main, target)
    # The framework calls these: renderer overrides and interface-named controller functions.
    module = f"{main}::__module__"
    platform = {
        m
        for b_dir in idx.chain(bdir)
        for i in idx.root_attrs(b_dir).get("implements", "").split(",")
        for m in _PLATFORM_INTERFACE_METHODS.get(i.strip().lower(), ())
    }
    wanted = [("Renderer", n) for n in _RENDERER_OVERRIDES]
    wanted += [("Controller", n) for n in sorted(idx.interface_methods(bdir) | platform)]
    for role, n in wanted:
        hit = idx.bundle_function(bdir, role, n)
        if hit is not None:
            edges.bind(module, hit, main)


def _markup_file_edges(idx: _AuraIndex, bdir: str, path: str, edges: _Edges) -> None:
    """Handler, tag and event edges of one markup file of the bundle."""
    text = _mask_xml(idx.text(path))
    module = f"{path}::__module__"
    handlers = {m.group(1) for m in _MARKUP_HANDLER_RE.finditer(text)}
    for n in sorted(handlers | _default_action_methods(text)):
        hit = idx.bundle_function(bdir, "Controller", n)
        if hit is not None:
            edges.bind(module, hit, path)
    for m in _TAG_RE.finditer(text):
        name = _referenced_bundle(idx, m)
        target = idx.bundle_target(name, path) if name else None
        if target is not None:
            edges.file(path, target)


def _referenced_bundle(idx: _AuraIndex, m: re.Match[str]) -> str | None:
    """The own-namespace bundle a tag names: as the tag itself, or as its event type."""
    ns, tag, blob = m.group(1), m.group(2), m.group(3)
    if ns == "aura" and tag in ("registerEvent", "handler"):
        return idx.own_name(_attrs(blob).get("type" if tag == "registerEvent" else "event", ""))
    return tag if ns.lower() in idx.own_ns else None


def _markup_edges(idx: _AuraIndex, bdir: str, b: _Bundle, edges: _Edges) -> None:
    if b.main is not None:
        _root_edges(idx, bdir, b.main, edges)
    for path in sorted(b.markup.values()):
        _markup_file_edges(idx, bdir, path, edges)


class _ApexController:
    """The bundle's Apex controller, resolved on first use."""

    def __init__(self, idx: _AuraIndex, bdir: str) -> None:
        self.idx, self.bdir = idx, bdir
        self._resolved = False
        self._value: tuple[str, str] | None = None

    def get(self) -> tuple[str, str] | None:
        if not self._resolved:
            self._value, self._resolved = self.idx.controller_class(self.bdir), True
        return self._value


def _action_edge(
    idx: _AuraIndex, bdir: str, path: str, m: re.Match[str], apex: _ApexController, edges: _Edges
) -> None:
    """Bind one "c.x" literal: the Apex action for `get(`, else a client controller function."""
    name = m.group(3)
    source = idx.enclosing(path, _line_of(m.string, m.start()))
    client = idx.bundle_function(bdir, "Controller", name)
    controller = apex.get() if m.group(1) else None
    if controller is not None:
        method_sym = idx.apex_method(controller[1], controller[0], name)
        if method_sym is not None or client is None:
            # The Apex action. A method the class does not declare itself may
            # be inherited, so the class edge stands alone.
            edges.file(path, controller[1], {name})
            if method_sym is not None:
                edges.count += add_symbol_edge(idx.graph, source, method_sym)
            return
    # Any other "c.x" literal (getReference, addHandler, enqueue of a client
    # action) names a client-side controller function.
    if client is not None:
        edges.bind(source, client, path)


def _js_edges(idx: _AuraIndex, bdir: str, b: _Bundle, edges: _Edges) -> None:
    apex = _ApexController(idx, bdir)
    for path in sorted(b.js.values()):
        text = _mask_js_comments(idx.text(path))
        for m in _ACTION_RE.finditer(text):
            _action_edge(idx, bdir, path, m, apex, edges)
        helper_calls = list(_HELPER_CALL_RE.finditer(text))
        if path == b.js.get("Helper"):
            helper_calls += _THIS_CALL_RE.finditer(text)
        for m in helper_calls:
            hit = idx.bundle_function(bdir, "Helper", m.group(1))
            if hit is not None:
                edges.bind(idx.enclosing(path, _line_of(text, m.start())), hit, path)
        for written in _created_components(text):
            name = idx.own_name(written)
            target = idx.bundle_target(name, path) if name else None
            if target is not None:
                edges.count += _dynamic_edge(idx.graph, path, target)


def _created_components(text: str) -> list[str]:
    """The ``ns:X`` string literals ``$A.createComponent(s)`` is called with."""
    out: list[str] = []
    for m in _CREATE_ONE_RE.finditer(text):
        out.append(f"{m.group(2)}:{m.group(3)}")
    for m in _CREATE_MANY_RE.finditer(text):
        span = text[m.end() - 1 : _call_span_end(text, m.end() - 1)]
        out.extend(f"{x.group(2)}:{x.group(3)}" for x in _CREATE_MANY_ITEM_RE.finditer(span))
    return out


def _add_aura_edges(graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext) -> int:
    idx = _AuraIndex(graph, parsed_files, ctx)
    edges = _Edges(graph)
    for bdir in sorted(idx.bundles):
        b = idx.bundles[bdir]
        _markup_edges(idx, bdir, b, edges)
        _js_edges(idx, bdir, b, edges)
    return edges.flush()


class _AuraHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == "aura" for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_aura_edges(graph, parsed_files, ctx)


HANDLERS: list[FrameworkHandler] = [_AuraHandler()]
