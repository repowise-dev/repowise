"""Salesforce Visualforce edges: pages and components to their Apex and to each other.

* ``controller=`` / ``extensions=`` on the root tag -> the Apex classes and the
  constructor the platform calls.
* ``{!expr}`` -> the controller member its first identifier names (property,
  ``getX``, or ``setX`` for an input or ``assignTo``), looked up through the
  controller, then each extension, each up its ``extends`` chain.
* ``<c:X>``, ``pageName=``, ``template=`` and ``$Page.X`` -> components and pages;
  Apex ``Page.X`` -> the page, a ``PageReference('/apex/X')`` literal as
  ``dynamic_uses``.

Names fold case; Apex classes are those an Apex file of the same sfdx project
reaches, and two equally near pages or components bind neither. Pages are entry points.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from ...analysis.security_scan import mask_comments_and_strings
from ..languages.apex_namespace import apex_namespaces
from ..resolvers.lwc import lwc_namespaces, nearest
from ..return_types import signature_parameter_count
from ..type_names import bare_type_name
from .aura import _file_edge, _mask_xml
from .base import DetectionContext, FrameworkHandler, add_symbol_edge, source_text

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

_TAG_RE = re.compile(r"""<([A-Za-z]\w*):([A-Za-z_]\w*)((?:[^>"']|"[^"]*"|'[^']*')*)>""")
_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_EXPR_RE = re.compile(r"\{!(.*?)\}", re.DOTALL)
_EXPR_STRING_RE = re.compile(r"""'(?:\\.|[^'\\])*'|"[^"]*\"""")
# An identifier not reached through `.`, `$` or `:`; the caller drops calls.
_EXPR_IDENT_RE = re.compile(r"(?<![\w$.:])([A-Za-z_]\w*)(?!\w)")
_BARE_EXPR_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*$")
_PAGE_GLOBAL_RE = re.compile(r"(?<![\w.])\$Page\s*\.\s*([A-Za-z_]\w*)", re.IGNORECASE)
# Loop and apex:variable locals, read file-wide (this only drops bindings).
_VAR_ATTR_RE = re.compile(r"""\bvar\s*=\s*["']\s*(\w+)\s*["']""", re.IGNORECASE)
# Not followed by `(`: `page.getParameters()` on a local named `page` is a call.
_APEX_PAGE_RE = re.compile(r"(?<![\w.$])Page\s*\.\s*([A-Za-z_]\w*)\b(?!\s*\()", re.IGNORECASE)
# The name must end the path or the literal: `'/apex/Report_' + kind` is a prefix.
_PAGE_REFERENCE_RE = re.compile(
    r"\bnew\s+(?:System\s*\.\s*)?PageReference\s*\(\s*'/apex/([A-Za-z_]\w*)(?:[?#/]|'\s*\))",
    re.IGNORECASE,
)
# Tags whose `value` the platform writes back on submit, through a setter.
_SELECT_INPUT_TAGS = frozenset({"selectlist", "selectradio", "selectcheckboxes"})
_ROOT_TAGS = ("page", "component")
_EXTENDS_DEPTH = 6


def _attrs(blob: str) -> dict[str, str]:
    """Attribute name (lowercased: Visualforce ignores its case) -> value."""
    return {
        m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3)
        for m in _ATTR_RE.finditer(blob)
    }


class _VfIndex:
    def __init__(
        self, graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext
    ) -> None:
        self.graph = graph
        self.parsed_files = parsed_files
        self.source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
        self.own_ns = lwc_namespaces(ctx)
        self.apex = apex_namespaces(ctx)
        # A repository class named Page would shadow the platform's.
        self.shadows_page = any(name == "page" for _, name in self.apex.top_level)
        self.pages: dict[str, list[str]] = {}
        self.components: dict[str, list[str]] = {}
        for path in sorted(parsed_files):
            if parsed_files[path].file_info.language != "visualforce":
                continue
            p = PurePosixPath(path)
            index = self.pages if p.suffix.lower() == ".page" else self.components
            index.setdefault(p.stem.lower(), []).append(path)
        self._members: dict[tuple[str, str], dict[str, list[Any]]] = {}

    def text(self, path: str) -> str:
        return source_text(path, self.parsed_files[path], self.source_map, "utf-8-sig")

    def _own_name(self, written: str) -> str | None:
        """*written* without an own ``ns__`` prefix (names never hold ``__``), else None."""
        head, sep, tail = written.strip().partition("__")
        if sep:
            return tail if tail and head.lower() in self.own_ns else None
        return head

    def page(self, written: str, src: str) -> str | None:
        name = self._own_name(written)
        return nearest(self.pages.get(name.lower(), []), src) if name else None

    def component(self, name: str, src: str) -> str | None:
        return nearest(self.components.get(name.lower(), []), src)

    def apex_class(self, written: str, src: str) -> tuple[str, str] | None:
        """``(class file, class name)`` for ``[ns.]Cls`` written in *src*."""
        path = self.apex.referred_file(src, written)
        return (path, bare_type_name(written)) if path else None

    def chain(self, cls_file: str, cls: str) -> list[tuple[str, str]]:
        """The class and its ``extends`` ancestors the repository declares, nearest first."""
        out = [(cls_file, cls)]
        while len(out) < _EXTENDS_DEPTH:
            path, name = out[-1]
            parent = next(
                (
                    h.parent_name
                    for h in self.parsed_files[path].heritage
                    if h.kind == "extends" and h.child_name.lower() == name.lower()
                ),
                None,
            )
            hit = self.apex_class(parent, path) if parent else None
            if hit is None or hit in out:
                break
            out.append(hit)
        return out

    def members(self, cls_file: str, cls: str) -> dict[str, list[Any]]:
        """Lowercased name -> the public member symbols *cls* declares itself."""
        key = (cls_file, cls.lower())
        if key not in self._members:
            found: dict[str, list[Any]] = {}
            for s in self.parsed_files[cls_file].symbols:
                if (s.parent_name or "").lower() == key[1] and s.visibility == "public":
                    found.setdefault(s.name.lower(), []).append(s)
            self._members[key] = found
        return self._members[key]

    def constructors(self, cls_file: str, cls: str, arity: int) -> list[Any]:
        """Public constructors of *cls* taking *arity* parameters."""
        ctors = self.members(cls_file, cls).get(cls.lower(), [])
        return [s for s in ctors if signature_parameter_count(s.signature or "") == arity]


class _Edges:
    """File edges gathered per (source, target) so each carries every name it vouches for."""

    def __init__(self, graph: nx.DiGraph) -> None:
        self.graph = graph
        self.names: dict[tuple[str, str], set[str]] = {}
        self.dynamic: set[tuple[str, str]] = set()
        self.count = 0

    def file(self, src: str, dst: str | None, names: set[str] | None = None) -> None:
        if dst is not None:
            self.names.setdefault((src, dst), set()).update(names or ())

    def bind(self, src_file: str, dst_file: str, symbols: list[Any], name: str) -> None:
        for sym_id in dict.fromkeys(s.id for s in symbols):
            self.count += add_symbol_edge(self.graph, f"{src_file}::__module__", sym_id)
        self.file(src_file, dst_file, {name})

    def flush(self) -> int:
        for (src, dst), names in sorted(self.names.items()):
            self.count += _file_edge(self.graph, src, dst, names)
        # A `Page.X` framework edge to the same page is the stronger evidence and stays.
        for src, dst in sorted(self.dynamic):
            if (
                src != dst
                and src in self.graph
                and dst in self.graph
                and not self.graph.has_edge(src, dst)
            ):
                self.graph.add_edge(
                    src,
                    dst,
                    edge_type="dynamic_uses",
                    hint_source="vf_page_reference",
                    weight=1.0,
                    imported_names=[],
                )
                self.count += 1
        return self.count


def _expressions(text: str) -> list[str]:
    """The body of every ``{!...}`` with its string literals blanked."""
    return [
        _EXPR_STRING_RE.sub(lambda s: " " * len(s.group(0)), m.group(1))
        for m in _EXPR_RE.finditer(text)
    ]


def _member_names(expr: str) -> list[str]:
    """Identifiers of *expr* that can name a controller member, in order."""
    return [
        m.group(1)
        for m in _EXPR_IDENT_RE.finditer(expr)
        if not expr[m.end() :].lstrip().startswith("(")
    ]


def _controllers(idx: _VfIndex, path: str, root_attrs: dict[str, str]) -> list[tuple[str, str]]:
    """``(class file, class name)`` of the controller, then each extension in order."""
    out: list[tuple[str, str]] = []
    written = [root_attrs.get("controller", ""), *root_attrs.get("extensions", "").split(",")]
    for w in written:
        hit = idx.apex_class(w, path) if w.strip() and "{" not in w else None
        if hit is not None and hit not in out:
            out.append(hit)
    return out


def _bare_names(written: str) -> set[str]:
    """Lowercased names of the bare ``{!name}`` expressions in *written*."""
    return {m.group(1).lower() for b in _expressions(written) if (m := _BARE_EXPR_RE.match(b))}


class _MarkupNames:
    """Names an expression must not bind, and names written back to the controller."""

    def __init__(self, text: str, root_attrs: dict[str, str]) -> None:
        self.local = {m.group(1).lower() for m in _VAR_ATTR_RE.finditer(text)}
        for key in ("standardcontroller", "recordsetvar"):
            if root_attrs.get(key):
                self.local.add(root_attrs[key].strip().lower())
        # An `assignTo` binds even when the attribute shares its name.
        self.setters: set[str] = set()
        self.assigned: set[str] = set()

    def read_apex_tag(self, tag: str, attrs: dict[str, str]) -> None:
        if tag == "attribute" and attrs.get("name"):
            self.local.add(attrs["name"].strip().lower())
        if tag.startswith("input") or tag in _SELECT_INPUT_TAGS:
            self.setters |= _bare_names(attrs.get("value", ""))
        self.assigned |= _bare_names(attrs.get("assignto", ""))

    def skips(self, name: str) -> bool:
        return name in self.local and name not in self.assigned

    def writes(self, name: str) -> bool:
        return name in self.setters or name in self.assigned


def _tag_edges(
    idx: _VfIndex, path: str, tags: list[re.Match[str]], names: _MarkupNames, edges: _Edges
) -> None:
    """Component tags and included pages; records the names the tags declare or write."""
    for m in tags:
        ns, tag = m.group(1).lower(), m.group(2).lower()
        if ns in idx.own_ns:
            edges.file(path, idx.component(m.group(2), path))
        elif ns == "apex":
            attrs = _attrs(m.group(3))
            names.read_apex_tag(tag, attrs)
            page_attr = {"include": "pagename", "composition": "template"}.get(tag)
            if page_attr and attrs.get(page_attr):
                edges.file(path, idx.page(attrs[page_attr], path))


def _root_attrs(idx: _VfIndex, path: str, tags: list[re.Match[str]]) -> dict[str, str]:
    """Attributes of the ``<apex:page>`` / ``<apex:component>`` root; marks a page an entry point."""
    root = next(
        (m for m in tags if m.group(1).lower() == "apex" and m.group(2).lower() in _ROOT_TAGS),
        None,
    )
    if root is None:
        return {}
    if root.group(2).lower() == "page":
        idx.graph.nodes[path]["is_entry_point"] = True
    return _attrs(root.group(3))


def _bind_constructors(
    idx: _VfIndex, path: str, controllers: list[tuple[str, str]], custom: str, edges: _Edges
) -> None:
    has_custom = bool(custom.strip())
    for i, (cls_file, cls) in enumerate(controllers):
        # A controller is built with no argument, an extension with the controller.
        arity = 0 if i == 0 and has_custom else 1
        edges.bind(path, cls_file, idx.constructors(cls_file, cls, arity), cls)


def _markup_edges(idx: _VfIndex, path: str, edges: _Edges) -> None:
    text = _mask_xml(idx.text(path))
    tags = list(_TAG_RE.finditer(text))
    root_attrs = _root_attrs(idx, path, tags)
    controllers = _controllers(idx, path, root_attrs)
    _bind_constructors(idx, path, controllers, root_attrs.get("controller", ""), edges)

    names = _MarkupNames(text, root_attrs)
    _tag_edges(idx, path, tags, names, edges)
    for m in _PAGE_GLOBAL_RE.finditer(text):
        edges.file(path, idx.page(m.group(1), path))

    if not controllers:
        return
    seen: set[str] = set()
    for body in _expressions(text):
        for name in _member_names(body):
            low = name.lower()
            if low not in seen and not names.skips(low):
                seen.add(low)
                _bind_member(idx, path, controllers, low, names.writes(low), edges)


def _bind_member(
    idx: _VfIndex,
    path: str,
    controllers: list[tuple[str, str]],
    name: str,
    is_setter: bool,
    edges: _Edges,
) -> None:
    """Bind ``{!name}`` to the first controller or extension member it names."""
    for top in controllers:
        for cls_file, cls in idx.chain(*top):
            members = idx.members(cls_file, cls)
            if name in members and name != cls.lower():
                edges.bind(path, cls_file, members[name], name)
                return
            getter, setter = members.get(f"get{name}"), members.get(f"set{name}")
            if getter or (is_setter and setter):
                edges.bind(path, cls_file, getter or [], f"get{name}")
                if is_setter and setter:
                    edges.bind(path, cls_file, setter, f"set{name}")
                return


def _apex_edges(idx: _VfIndex, path: str, edges: _Edges) -> None:
    raw = idx.text(path)
    if not idx.shadows_page:
        code = mask_comments_and_strings(raw, hash_comments=False)
        for m in _APEX_PAGE_RE.finditer(code):
            target = idx.page(m.group(1), path)
            if target is not None:
                edges.file(path, target, {m.group(1)})
    literals = mask_comments_and_strings(raw, strings=False, hash_comments=False)
    for m in _PAGE_REFERENCE_RE.finditer(literals):
        target = idx.page(m.group(1), path)
        if target is not None:
            edges.dynamic.add((path, target))


def _add_visualforce_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext
) -> int:
    idx = _VfIndex(graph, parsed_files, ctx)
    edges = _Edges(graph)
    for path in sorted(parsed_files):
        lang = parsed_files[path].file_info.language
        if lang == "visualforce" and path in graph:
            _markup_edges(idx, path, edges)
        elif lang == "apex" and idx.pages:
            _apex_edges(idx, path, edges)
    return edges.flush()


class _VisualforceHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == "visualforce" for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_visualforce_edges(graph, parsed_files, ctx)


HANDLERS: list[FrameworkHandler] = [_VisualforceHandler()]
