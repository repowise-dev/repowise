"""Salesforce flows and the metadata that places code.

A flow links to the Apex classes its actions name (and their one
``@InvocableMethod``), its subflows, its screen components and its Apex-defined
types. Apex ``Flow.Interview`` references and LWC ``<lightning-flow>`` tags
with a literal name link back to the flow.

Flexipages, quick actions, tabs, email services and Experience sites are read
from disk (as ``android_manifest`` is) for liveness only: each file they name is
anchored to ``framework:salesforce`` and marked an entry point.

Only own namespaces (``c``, sfdx-project.json, cumulusci.yml) are followed.
Apex and flow names fold case; component names match exactly. An Apex class is
one an Apex file of the referrer's sfdx project reaches; a flow or component
two projects declare binds to the nearest by path, and to none on a tie.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from repowise.core.fs_walk import glob_via

from ..framework_facts import SALESFORCE
from ..languages.apex_namespace import apex_namespaces
from ..resolvers.lwc import lwc_bundles, nearest
from ..salesforce_flow import FlowRefs, read_flow
from ..type_names import bare_type_name, type_qualifier
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

_FLOW = "salesforce_flow"
_XML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
# An Apex comment, or a string literal kept as-is so a `//` inside one is not a comment.
_APEX_COMMENT_OR_STRING_RE = re.compile(
    r"//[^\n]*|/\*.*?(?:\*/|\Z)|'(?:[^'\\\n]|\\.)*'", re.DOTALL
)
_INVOCABLE_RE = re.compile(r"@\s*InvocableMethod\b", re.IGNORECASE)
# `Flow.Interview.createInterview('Name', ...)` or `('ns', 'Name', ...)`.
_CREATE_INTERVIEW_RE = re.compile(
    r"\bFlow\s*\.\s*Interview\s*\.\s*createInterview\s*\(\s*'([^'\n]*)'(?:\s*,\s*'([^'\n]*)')?",
    re.IGNORECASE,
)
# `Flow.Interview.Name` and `Flow.Interview.ns.Name`.
_INTERVIEW_TYPE_RE = re.compile(
    r"\bFlow\s*\.\s*Interview\s*\.\s*(\w+)(?:\s*\.\s*(\w+))?", re.IGNORECASE
)
_LIGHTNING_FLOW_RE = re.compile(
    r"""<lightning-flow\b[^>]*?\bflow-api-name\s*=\s*["']([A-Za-z]\w*)["']""", re.IGNORECASE
)
_NS_UU_RE = re.compile(r"^([A-Za-z0-9]+)__(.+)$")
_SFDX_NS_RE = re.compile(r'"namespace"\s*:\s*"(\w+)"')
_CCI_NS_RE = re.compile(r"^\s+namespace:\s*['\"]?(\w+)", re.MULTILINE)

# Liveness metadata: glob -> (element names that hold a component, a flow, an Apex class).
_COMPONENT_TAGS = r"componentName|lightningComponent|lightningWebComponent|lwcComponent|auraComponent"
_TAG_VALUE_RE = re.compile(
    rf"<({_COMPONENT_TAGS}|flowDefinition|apexClass)>\s*([^<\s]+)\s*</\1>"
)
_PLACEMENT_SUFFIXES = (".flexipage-meta.xml", ".quickAction-meta.xml", ".tab-meta.xml")
# Metadata API names are bare suffixes (``.tab`` is also tab-separated data),
# so they count only in their metadata directory.
_MDAPI_PLACEMENT = {".flexipage": "flexipages", ".quickAction": "quickActions", ".tab": "tabs"}
# Placement metadata is small; a bigger file is not one.
_MAX_PLACEMENT_BYTES = 1_000_000
# The globs only narrow the walk; ``_is_placement_file`` decides what is read.
_LIVENESS_GLOBS = (
    *(f"*{s}" for s in (*_PLACEMENT_SUFFIXES, *_MDAPI_PLACEMENT)),
    "*.xml",  # emailservices/<Name>.xml-meta.xml, mdapi emailservices/<Name>.xml
    "*.json",  # Experience Builder views under experiences/
)
_EXPERIENCE_DEFINITION_RE = re.compile(r'"definition"\s*:\s*"(\w+:\w+)"')


def _mask(text: str, pattern: re.Pattern[str], keep: str = "") -> str:
    def blank(m: re.Match[str]) -> str:
        s = m.group(0)
        return s if keep and s[0] in keep else re.sub(r"[^\n]", " ", s)

    return pattern.sub(blank, text)


class _Index:
    """Name -> file maps for one repository, built from the parsed file set."""

    def __init__(self, parsed_files: dict[str, Any], ctx: ResolverContext) -> None:
        self.parsed_files = parsed_files
        self.apex = apex_namespaces(ctx)
        self.flows: dict[str, list[str]] = {}  # lowercased API name -> flow paths
        self.lwc = lwc_bundles(ctx)  # bundle name -> main modules
        self.aura: dict[str, list[str]] = {}  # bundle name -> markup (or controller JS)
        aura_files: dict[str, dict[str, str]] = {}
        for path in sorted(parsed_files):
            parsed = parsed_files[path]
            p = PurePosixPath(path)
            lang = parsed.file_info.language
            if lang == _FLOW:
                sym = next(iter(parsed.symbols), None)
                if sym is not None:
                    self.flows.setdefault(sym.name.lower(), []).append(path)
            elif len(p.parts) >= 3 and p.parts[-3] == "aura":
                aura_files.setdefault(str(p.parent), {})[p.name] = path
        for bundle_dir, files in aura_files.items():
            bundle = PurePosixPath(bundle_dir).name
            # The markup is the bundle; without the Aura markup indexed, its
            # controller is what the placement makes live.
            target = next(
                (files[f"{bundle}{s}"] for s in (".cmp", ".app") if f"{bundle}{s}" in files),
                files.get(f"{bundle}Controller.js"),
            )
            if target is not None:
                self.aura.setdefault(bundle, []).append(target)
        self.own_ns = _own_namespaces(ctx)

    # -- name resolution ------------------------------------------------

    def _strip_ns(self, raw: str, index: dict[str, list[str]]) -> str | None:
        """*raw* without an own-namespace prefix (``ns__X``, ``ns.X``); None if foreign."""
        name = raw.strip()
        if name.lower() in index:
            return name
        if "." in name:
            ns = type_qualifier(name)
            return bare_type_name(name) if ns.lower() in self.own_ns else None
        m = _NS_UU_RE.match(name)
        if m:
            return m.group(2) if m.group(1).lower() in self.own_ns else None
        return name

    def apex_class(self, raw: str, referrer: str) -> str | None:
        m = _NS_UU_RE.match(raw.strip())
        found = self.apex.referred_file(referrer, f"{m.group(1)}.{m.group(2)}" if m else raw)
        prefix, name = (m.group(1), m.group(2)) if m else (type_qualifier(raw), bare_type_name(raw))
        # A namespace only cumulusci.yml declares is still this repository's own.
        if found is None and prefix and prefix.lower() in self.own_ns:
            found = self.apex.referred_file(referrer, name)
        return found

    # Ceiling: two legacy versions of one flow in one directory (``Foo-1.flow``,
    # ``Foo-2.flow``) tie and bind neither; take the highest version if it matters.
    def flow(self, raw: str, referrer: str) -> str | None:
        name = self._strip_ns(raw, self.flows)
        return nearest(self.flows.get(name.lower(), []), referrer) if name else None

    def components(self, raw: str, referrer: str) -> list[str]:
        """The LWC main module and Aura bundle file ``c:name`` (or a bare name) means."""
        ns, sep, name = raw.strip().partition(":")
        if not sep:
            ns, name = "c", ns
            m = _NS_UU_RE.match(name)
            if m and name not in self.lwc and name not in self.aura:
                ns, name = m.group(1), m.group(2)
        if ns.lower() not in self.own_ns:
            return []
        found = (nearest(index.get(name, ()), referrer) for index in (self.lwc, self.aura))
        return [f for f in found if f is not None]

    def symbol_of(self, path: str) -> str | None:
        """The id of the flow's symbol, or the Apex file's class named like the file."""
        parsed = self.parsed_files.get(path)
        if parsed is None:
            return None
        stem = PurePosixPath(path).stem.lower()
        return next(
            (
                s.id
                for s in parsed.symbols
                if s.parent_name is None
                and (s.language == _FLOW or (s.kind == "class" and s.name.lower() == stem))
            ),
            None,
        )

    def invocable(self, cls_path: str) -> str | None:
        """The class's single ``@InvocableMethod``, or None."""
        parsed = self.parsed_files.get(cls_path)
        if parsed is None:
            return None
        stem = PurePosixPath(cls_path).stem.lower()
        ids = {
            s.id
            for s in parsed.symbols
            if s.kind == "method"
            and (s.parent_name or "").lower() == stem
            and any(_INVOCABLE_RE.search(d) for d in s.decorators)
        }
        return ids.pop() if len(ids) == 1 else None


def _own_namespaces(ctx: ResolverContext) -> frozenset[str]:
    """``c`` plus the namespace each sfdx-project.json and cumulusci.yml declares."""
    names = {"c"}
    if ctx.repo_path is None:
        return frozenset(names)
    for cfg in glob_via(
        ctx.walk_snapshot,
        ctx.repo_path,
        ("sfdx-project.json", "cumulusci.yml"),
        prune_nested_git=ctx.prune_nested_git,
    ):
        try:
            text = cfg.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        pattern = _SFDX_NS_RE if cfg.name.endswith(".json") else _CCI_NS_RE
        names.update(m.group(1).lower() for m in pattern.finditer(text))
    return frozenset(names)


def _link(
    graph: nx.DiGraph, src: str, target: str, names: list[str], src_sym: str | None, target_sym: str | None
) -> int:
    added = _add_edge_if_new(graph, src, target, names)
    if src_sym is not None and target_sym is not None:
        added += add_symbol_edge(graph, src_sym, target_sym)
    return added


# -- flows -----------------------------------------------------------------


def _flow_edges(graph: nx.DiGraph, idx: _Index, ctx: ResolverContext) -> int:
    source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
    count = 0
    for path, parsed in idx.parsed_files.items():
        if parsed.file_info.language != _FLOW:
            continue
        raw = source_map.get(path)
        refs = read_flow(raw if raw is not None else source_text(path, parsed, {}).encode())
        flow_sym = idx.symbol_of(path)
        if refs is not None and flow_sym is not None:
            count += _one_flow_edges(graph, idx, path, flow_sym, refs)
    return count


def _one_flow_edges(graph: nx.DiGraph, idx: _Index, path: str, flow_sym: str, refs: FlowRefs) -> int:
    count = 0
    for cls in filter(None, (idx.apex_class(n, path) for n in refs.apex_actions)):
        target_sym = idx.invocable(cls) or idx.symbol_of(cls)
        count += _link(graph, path, cls, [PurePosixPath(cls).stem], flow_sym, target_sym)
    for cls in filter(None, (idx.apex_class(n, path) for n in refs.apex_types)):
        count += _add_edge_if_new(graph, path, cls, [PurePosixPath(cls).stem])
    for target in filter(None, (idx.flow(n, path) for n in refs.flows)):
        count += _link(graph, path, target, [], flow_sym, idx.symbol_of(target))
    for name in refs.components:
        count += sum(_add_edge_if_new(graph, path, t) for t in idx.components(name, path))
    return count


def _enclosing(parsed: Any, line: int, path: str) -> str:
    """The innermost method (else class) declared around *line*, else the module."""
    spans = [
        (s.end_line - s.start_line, s.id)
        for s in parsed.symbols
        if s.kind in ("method", "class") and s.start_line <= line <= s.end_line
    ]
    return min(spans)[1] if spans else f"{path}::__module__"


def _apex_flow_refs(graph: nx.DiGraph, idx: _Index, ctx: ResolverContext) -> int:
    """``Flow.Interview.<Name>`` and ``createInterview('<Name>')`` in Apex."""
    source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
    count = 0
    for path, parsed in idx.parsed_files.items():
        if parsed.file_info.language != "apex":
            continue
        text = source_text(path, parsed, source_map)
        if "interview" not in text.lower():
            continue
        code = _mask(text, _APEX_COMMENT_OR_STRING_RE)  # comments and strings blanked
        text = _mask(text, _APEX_COMMENT_OR_STRING_RE, keep="'")  # comments only
        hits: list[tuple[int, str]] = []
        for m in _CREATE_INTERVIEW_RE.finditer(text):
            # The call must start in code; only its argument is read from a literal.
            if code[m.start()] == " ":
                continue
            ns, name = (m.group(1), m.group(2)) if m.group(2) is not None else ("", m.group(1))
            hits.append((m.start(), f"{ns}__{name}" if ns else name))
        for m in _INTERVIEW_TYPE_RE.finditer(code):
            first, second = m.group(1), m.group(2)
            if first.lower() == "createinterview":
                continue
            # `Flow.Interview.ns.Name`: the first part is a namespace only
            # when no flow has that name.
            if second is not None and idx.flow(first, path) is None:
                hits.append((m.start(), f"{first}.{second}"))
            else:
                hits.append((m.start(), first))
        for offset, name in hits:
            target = idx.flow(name, path)
            if target is None:
                continue
            caller = _enclosing(parsed, text.count("\n", 0, offset) + 1, path)
            count += _link(graph, path, target, [], caller, idx.symbol_of(target))
    return count


def _lwc_flow_refs(graph: nx.DiGraph, idx: _Index, ctx: ResolverContext) -> int:
    """``<lightning-flow flow-api-name="Name">`` in an LWC template, literal only."""
    source_map: dict[str, bytes] = getattr(ctx, "source_map", None) or {}
    count = 0
    for path, parsed in idx.parsed_files.items():
        p = PurePosixPath(path)
        if p.suffix != ".html" or len(p.parts) < 3 or p.parts[-3] != "lwc":
            continue
        text = source_text(path, parsed, source_map)
        if "lightning-flow" not in text:
            continue
        text = _mask(text, _XML_COMMENT_RE)
        for m in _LIGHTNING_FLOW_RE.finditer(text):
            target = idx.flow(m.group(1), path)
            if target is not None:
                count += _link(graph, path, target, [], f"{path}::__module__", idx.symbol_of(target))
    return count


# -- liveness metadata -------------------------------------------------------


def _is_placement_file(rel: str) -> bool:
    """Whether *rel* is metadata that places code, so worth reading."""
    p = PurePosixPath(rel)
    if p.name.endswith(_PLACEMENT_SUFFIXES):
        return True
    if _MDAPI_PLACEMENT.get(p.suffix) == p.parent.name:
        return True
    if p.parent.name == "emailservices":
        return p.name.endswith(".xml")
    return p.suffix == ".json" and "experiences" in p.parts


def _placed_names(rel: str, text: str) -> list[tuple[str, str]]:
    """``(kind, name)`` pairs a metadata file places: kind is component, flow or apex."""
    parent = PurePosixPath(rel).parent.name
    if rel.endswith(".json"):
        return [("component", m.group(1)) for m in _EXPERIENCE_DEFINITION_RE.finditer(text)]
    text = _mask(text, _XML_COMMENT_RE)
    out: list[tuple[str, str]] = []
    for m in _TAG_VALUE_RE.finditer(text):
        tag, value = m.group(1), m.group(2)
        if tag == "apexClass":
            # Permission sets grant class access with the same element; that
            # is no evidence of use, so only an email service counts.
            if parent == "emailservices":
                out.append(("apex", value))
        elif tag == "flowDefinition":
            out.append(("flow", value))
        else:
            out.append(("component", value))
    return out


def _placement_texts(ctx: ResolverContext, root: Path) -> Iterator[tuple[str, str]]:
    """``(repo-relative path, text)`` of every placement metadata file."""
    for file in glob_via(
        ctx.walk_snapshot, root, _LIVENESS_GLOBS, prune_nested_git=ctx.prune_nested_git
    ):
        try:
            rel = file.relative_to(root).as_posix()
        except ValueError:
            continue
        if not _is_placement_file(rel):
            continue
        try:
            if file.stat().st_size <= _MAX_PLACEMENT_BYTES:
                yield rel, file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue


def _placed_targets(idx: _Index, kind: str, value: str, rel: str) -> list[str]:
    if kind == "apex":
        return [t] if (t := idx.apex_class(value, rel)) else []
    if kind == "flow":
        return [t] if (t := idx.flow(value, rel)) else []
    return idx.components(value, rel)


def _liveness_edges(graph: nx.DiGraph, idx: _Index, ctx: ResolverContext, path_set: set[str]) -> int:
    if ctx.repo_path is None:
        return 0
    count = 0
    for rel, text in _placement_texts(ctx, ctx.repo_path.resolve()):
        for kind, value in _placed_names(rel, text):
            for target in _placed_targets(idx, kind, value, rel):
                if target not in path_set:
                    continue
                count += anchor_edge(graph, SALESFORCE, target)
                node = graph.nodes.get(target)
                if node is not None:
                    node["is_entry_point"] = True
    return count


class _SalesforceMetadataHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(
            p.file_info.language in ("apex", _FLOW) for p in dctx.parsed_files.values()
        ) or any(PurePosixPath(p).name == "sfdx-project.json" for p in dctx.path_set)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        idx = _Index(parsed_files, ctx)
        return (
            _flow_edges(graph, idx, ctx)
            + _apex_flow_refs(graph, idx, ctx)
            + _lwc_flow_refs(graph, idx, ctx)
            + _liveness_edges(graph, idx, ctx, path_set)
        )


HANDLERS: list[FrameworkHandler] = [_SalesforceMetadataHandler()]
