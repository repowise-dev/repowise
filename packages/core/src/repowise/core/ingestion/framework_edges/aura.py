"""Salesforce Aura bundle edges.

An Aura component is a directory ``aura/<name>/``: markup (``<name>.cmp``,
``.app``, ``.evt``, ``.intf``, ``.design``, ``.tokens``) plus
``<name>Controller.js`` / ``Helper.js`` / ``Renderer.js``. Nothing in it is an
import. The platform wires the pieces by name, and this handler reads those
names:

* ``controller="Cls"`` on ``aura:component`` / ``aura:application`` -> the Apex
  class file.
* ``component.get("c.m")`` (any receiver) in bundle JS -> Apex method ``m`` of
  the bundle's controller class, found through ``extends="c:Base"`` when the
  bundle declares none. When the class has no ``m`` but the bundle's
  Controller.js does, it is a client-side action and binds there instead;
  ``getReference("c.m")`` always does.
* ``{!c.h}`` in markup -> function ``h`` of the bundle's Controller.js.
* ``helper.x(`` in bundle JS, and ``this.x(`` in Helper.js -> function ``x`` of the
  bundle's Helper.js, inherited through ``extends`` like the controller.
* ``<c:X>``, ``extends="c:X"``, ``implements="c:X"`` and the event ``type`` /
  ``event`` of ``aura:registerEvent`` / ``aura:handler`` -> bundle ``X``,
  Aura first, else the LWC bundle's ``X.js``. Only the own namespaces (``c``
  and the sfdx-project.json ``namespace``) are followed; ``force:``,
  ``lightning:``, ``flexipage:`` and the rest are the platform's.
* ``$A.createComponent("c:X", ...)`` and the ``["c:X", {...}]`` entries of
  ``$A.createComponents`` -> bundle ``X`` as a ``dynamic_uses`` edge, string
  literals only. A name built at run time is never guessed.

Aura names match by exact case (every reference in the validation corpora
does); Apex names fold case, as the platform does. A name with two candidate
bundles or classes binds to neither.

Entry points: an ``.app`` and a component implementing a platform placement
interface (App Builder, tabs, quick actions, communities, URL addressable)
are placed from outside the source tree, so the markup node is marked
``is_entry_point``; a Renderer's ``render`` / ``rerender`` / ``afterRender`` /
``unrender`` overrides are bound from the bundle's markup because the
framework calls them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from ..extractors.synthetic_symbols.aura_bundle_js import aura_js_role
from ..languages.specs.aura import aura_bundle
from ..type_names import bare_type_name, type_qualifier
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
# Formatters reflow `{!c.h}` across lines and append a `;` (NebulaLogger ships one).
_MARKUP_HANDLER_RE = re.compile(r"\{\s*[!#]\s*c\.([A-Za-z_]\w*)\s*;?\s*\}")
# A "c.x" string literal; group 1 is set when it is the argument of `.get(`.
_ACTION_RE = re.compile(r"""(\.get\(\s*)?(["'])c\.([A-Za-z_]\w*)\2""")
_HELPER_CALL_RE = re.compile(r"(?<![\w.$])helper\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
# Inside a helper, `this` is the (inherited) helper object.
_THIS_CALL_RE = re.compile(r"(?<![\w.$])this\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
_CREATE_ONE_RE = re.compile(r"""\$A\s*\.\s*createComponent\s*\(\s*(["'`])(\w+):(\w+)\1""")
_CREATE_MANY_RE = re.compile(r"\$A\s*\.\s*createComponents\s*\(")
_CREATE_MANY_ITEM_RE = re.compile(r"""\[\s*(["'`])(\w+):(\w+)\1""")

# Interfaces that let an admin place the component without any source naming
# it: App Builder pages, tabs, quick actions, overrides, communities, flows
# and URL navigation.
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


def _own_namespaces(parsed_files: dict[str, Any], source_map: dict[str, bytes]) -> frozenset[str]:
    """``c`` plus every ``namespace`` an sfdx-project.json declares, lowercased."""
    names = {"c"}
    for path, parsed in parsed_files.items():
        if PurePosixPath(path).name != "sfdx-project.json":
            continue
        try:
            ns = json.loads(source_text(path, parsed, source_map, "utf-8-sig")).get("namespace")
        except (ValueError, AttributeError):
            continue
        if isinstance(ns, str) and ns.strip():
            names.add(ns.strip().lower())
    return frozenset(names)


class _AuraIndex:
    def __init__(self, graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext) -> None:
        self.graph = graph
        self.parsed_files = parsed_files
        self.source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
        self.own_ns = _own_namespaces(parsed_files, self.source_map)
        self.bundles: dict[str, _Bundle] = {}  # bundle dir -> bundle
        aura_by_name: dict[str, list[str]] = {}
        lwc_by_name: dict[str, list[str]] = {}
        apex_by_name: dict[str, list[str]] = {}
        for path in sorted(parsed_files):
            parsed = parsed_files[path]
            lang = parsed.file_info.language
            if lang == "apex" and path.endswith(".cls"):
                # Apex names fold case and a class file is named after its class.
                # Deliberately local: the case-insensitive Apex class index the
                # Apex resolution pass builds should replace this once it lands.
                apex_by_name.setdefault(PurePosixPath(path).stem.lower(), []).append(path)
                continue
            bundle = aura_bundle(path)
            if bundle is None:
                if lang == "javascript":
                    parts = PurePosixPath(path).parts
                    # LWC main module: lwc/<name>/<name>.js. Only the location
                    # is read here; the LWC surface itself is not this module's.
                    if len(parts) >= 3 and parts[-3] == "lwc" and parts[-1] == f"{parts[-2]}.js":
                        lwc_by_name.setdefault(parts[-2], []).append(path)
                continue
            bdir, name = bundle
            if lang == "aura":
                b = self.bundles.setdefault(bdir, _Bundle(name))
                b.markup[PurePosixPath(path).suffix.lower()] = path
            elif lang == "javascript" and (role := aura_js_role(path)) is not None:
                self.bundles.setdefault(bdir, _Bundle(name)).js[role] = path
        for bdir, b in self.bundles.items():
            if b.main is not None:
                aura_by_name.setdefault(b.name, []).append(bdir)
        self.aura_by_name = aura_by_name
        self.lwc_by_name = lwc_by_name
        self.apex_by_name = apex_by_name
        self._root_attrs: dict[str, dict[str, str]] = {}
        self._functions: dict[str, dict[str, str]] = {}

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
        return _nearest(src, mains + self.lwc_by_name.get(name, []))

    def apex_class(self, written: str, src: str) -> str | None:
        """The Apex file of ``controller="[ns.]Cls"`` written in *src*, own namespace only."""
        head, tail = type_qualifier(written), bare_type_name(written)
        if head and head.lower() not in self.own_ns:
            return None
        return _nearest(src, self.apex_by_name.get(tail.lower(), []))

    def aura_bundle_dir(self, name: str, src: str) -> str | None:
        """The bundle dir an own-namespace ``extends``/``implements`` name from *src* means."""
        return _nearest(src, self.aura_by_name.get(name, []))

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
        """Innermost symbol of *path* spanning *line*, else its ``__module__``."""
        best: Any = None
        for s in self.parsed_files[path].symbols:
            if s.start_line <= line <= s.end_line and (
                best is None or s.start_line >= best.start_line
            ):
                best = s
        return best.id if best is not None else f"{path}::__module__"


def _nearest(src: str, candidates: list[str]) -> str | None:
    """The one candidate sharing the longest directory prefix with *src*, else None.

    A repository can hold several Salesforce projects, or a source and an
    mdapi copy of one, each declaring the same names; the platform resolves a
    name inside the package being deployed, which the nearest copy stands for.
    Two candidates equally near is a collision we do not break by guessing.
    """
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    src_parts = PurePosixPath(src).parts

    def shared(c: str) -> int:
        n = 0
        for a, b in zip(src_parts, PurePosixPath(c).parts, strict=False):
            if a != b:
                break
            n += 1
        return n

    scores = sorted(((shared(c), c) for c in candidates), reverse=True)
    return scores[0][1] if scores[0][0] > scores[1][0] else None


def _file_edge(graph: nx.DiGraph, src: str, dst: str, names: set[str]) -> int:
    """A ``framework`` file edge vouching for *names*, merged into an earlier one."""
    if src == dst or src not in graph or dst not in graph:
        return 0
    existing = graph.get_edge_data(src, dst)
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


def _markup_edges(idx: _AuraIndex, bdir: str, b: _Bundle, edges: _Edges) -> None:
    graph = idx.graph
    attrs = idx.root_attrs(bdir)
    main = b.main

    if main is not None:
        cls = attrs.get("controller")
        cls_file = idx.apex_class(cls, main) if cls else None
        if cls_file is not None:
            edges.file(main, cls_file, {bare_type_name(cls)})
        implements = [i.strip() for i in attrs.get("implements", "").split(",") if i.strip()]
        if main.endswith(".app") or any(
            i.lower() in _ENTRY_INTERFACES or i.lower().startswith(_ENTRY_INTERFACE_PREFIXES)
            for i in implements
        ):
            graph.nodes[main]["is_entry_point"] = True
        for written in [attrs.get("extends", ""), *implements]:
            name = idx.own_name(written)
            target = idx.bundle_target(name, main) if name else None
            if target is not None:
                edges.file(main, target)
        # The framework calls these: renderer overrides, and the controller
        # function an action-less `aura:method` of an implemented interface names.
        module = f"{main}::__module__"
        for n in _RENDERER_OVERRIDES:
            hit = idx.bundle_function(bdir, "Renderer", n)
            if hit is not None:
                edges.bind(module, hit, main)
        platform = {m for i in implements for m in _PLATFORM_INTERFACE_METHODS.get(i.lower(), ())}
        for n in sorted(idx.interface_methods(bdir) | platform):
            hit = idx.bundle_function(bdir, "Controller", n)
            if hit is not None:
                edges.bind(module, hit, main)

    for path in sorted(b.markup.values()):
        text = _mask_xml(idx.text(path))
        module = f"{path}::__module__"
        handlers = {m.group(1) for m in _MARKUP_HANDLER_RE.finditer(text)}
        for n in sorted(handlers | _default_action_methods(text)):
            hit = idx.bundle_function(bdir, "Controller", n)
            if hit is not None:
                edges.bind(module, hit, path)
        for m in _TAG_RE.finditer(text):
            ns, tag, blob = m.group(1), m.group(2), m.group(3)
            if ns == "aura" and tag in ("registerEvent", "handler"):
                written = _attrs(blob).get("type" if tag == "registerEvent" else "event", "")
                name = idx.own_name(written)
            elif ns.lower() in idx.own_ns:
                name = tag
            else:
                continue
            target = idx.bundle_target(name, path) if name else None
            if target is not None:
                edges.file(path, target)


def _js_edges(idx: _AuraIndex, bdir: str, b: _Bundle, edges: _Edges) -> None:
    graph = idx.graph
    apex: tuple[str, str] | None = None
    apex_resolved = False
    for path in sorted(b.js.values()):
        text = _mask_js_comments(idx.text(path))
        for m in _ACTION_RE.finditer(text):
            name = m.group(3)
            source = idx.enclosing(path, _line_of(text, m.start()))
            client = idx.bundle_function(bdir, "Controller", name)
            if m.group(1):
                if not apex_resolved:
                    apex, apex_resolved = idx.controller_class(bdir), True
                method_sym = idx.apex_method(apex[1], apex[0], name) if apex else None
                if apex is not None and (method_sym is not None or client is None):
                    # The Apex action. A method the class does not declare
                    # itself may be inherited, so the class edge stands alone.
                    edges.file(path, apex[1], {name})
                    if method_sym is not None:
                        edges.count += add_symbol_edge(graph, source, method_sym)
                    continue
            # Any other "c.x" literal (getReference, addHandler, enqueue of a
            # client action) names a client-side controller function.
            if client is not None:
                edges.bind(source, client, path)
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
                edges.count += _dynamic_edge(graph, path, target)


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
