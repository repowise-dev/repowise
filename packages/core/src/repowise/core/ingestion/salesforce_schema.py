"""Salesforce object and field metadata as symbols, and the references bound to them.

Objects (``.object-meta.xml``, legacy ``.object``) are class symbols and fields
(``.field-meta.xml``, or inline in ``.object``) are property symbols. Names
compare case-folded, without the repository's own namespace prefix or CumulusCI
tokens. A reference binds one canonical copy; a name with no definition binds nothing.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from xml.parsers import expat

import yaml

from .models import FileInfo, ParsedFile, SchemaReference, Symbol

if TYPE_CHECKING:  # read paths import this module and must not load networkx
    import networkx as nx

_LANGUAGE = "salesforce_object"
_OBJECT_META = ".object-meta.xml"
_FIELD_META = ".field-meta.xml"
_SIGNATURE_MAX = 300
# An exact API-name match against metadata in the repository, not a guess.
_CONFIDENCE = 0.95

# CumulusCI rewrites these to the package namespace (or nothing) at deploy.
_NAMESPACE_TOKEN_RE = re.compile(r"(?:%%%|___)[A-Z]+(?:_[A-Z]+)*(?:%%%|___)")


# ---------------------------------------------------------------------------
# Metadata files -> symbols
# ---------------------------------------------------------------------------


@dataclass
class _Element:
    tag: str
    line: int
    end_line: int = 0
    text: str = ""
    children: list[_Element] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    def first(self, tag: str) -> str | None:
        return next((c.text for c in self.children if c.tag == tag and c.text), None)

    def all(self, tag: str) -> list[_Element]:
        return [c for c in self.children if c.tag == tag]


def _read_xml(source: bytes) -> _Element | None:
    """The element tree with line spans, or None for malformed XML.

    expat rather than ``xml.etree`` for the line numbers; it fetches no
    external entity and refuses entity-expansion bombs.
    """
    parser = expat.ParserCreate()
    stack: list[_Element] = []
    roots: list[_Element] = []

    def start(name: str, _attrs: dict[str, str]) -> None:
        el = _Element(name.rpartition(":")[2], parser.CurrentLineNumber)
        (stack[-1].children if stack else roots).append(el)
        stack.append(el)

    def end(_name: str) -> None:
        el = stack.pop()
        el.end_line = parser.CurrentLineNumber
        el.text = "".join(el.parts).strip()
        el.parts = []

    def chars(data: str) -> None:
        if stack:
            stack[-1].parts.append(data)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(source, True)
    except expat.ExpatError:
        return None
    return roots[0] if roots else None


def _strip_namespace_tokens(name: str) -> str:
    return _NAMESPACE_TOKEN_RE.sub("", name)


def _object_symbol(path: str, name: str, root: _Element) -> Symbol:
    label = root.first("label")
    signature = f'SObject {name} "{label}"' if label else f"SObject {name}"
    return Symbol(
        id=f"{path}::{name}",
        name=name,
        qualified_name=name,
        kind="class",
        signature=signature[:_SIGNATURE_MAX],
        start_line=root.line,
        end_line=max(root.end_line, root.line),
        docstring=root.first("description") or label,
        language=_LANGUAGE,
    )


def _field_symbol(
    sym_id: str, obj: str | None, name: str, el: _Element, parent: str | None
) -> tuple[Symbol, list[SchemaReference]]:
    kind = el.first("type")
    targets = el.all("referenceTo")
    signature = f"{obj}.{name}" if obj else name
    if kind:
        signature += f": {kind}"
    if targets:
        signature += f" -> {', '.join(t.text for t in targets)}"
    sym = Symbol(
        id=sym_id,
        name=name,
        qualified_name=f"{obj}.{name}" if obj else name,
        kind="property",
        signature=signature[:_SIGNATURE_MAX],
        start_line=el.line,
        end_line=max(el.end_line, el.line),
        docstring=el.first("description") or el.first("inlineHelpText") or el.first("label"),
        language=_LANGUAGE,
        parent_name=parent,
    )
    refs = [SchemaReference(sym_id, t.text, t.line) for t in targets if t.text]
    return sym, refs


def parse_object_metadata(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Symbols (and lookup references) of one object or field metadata file."""
    path = file_info.path
    root = _read_xml(source)
    if root is None:
        return ParsedFile(file_info, [], [], [], parse_errors=["malformed XML"])
    pure = PurePosixPath(path)
    lowered = pure.name.lower()
    symbols: list[Symbol] = []
    refs: list[SchemaReference] = []
    if lowered.endswith(_FIELD_META) and root.tag == "CustomField":
        name = _strip_namespace_tokens(pure.name[: -len(_FIELD_META)])
        # objects/<Obj>/fields/<F>.field-meta.xml; any other layout names no object.
        obj = pure.parent.parent.name if pure.parent.name == "fields" else None
        obj = _strip_namespace_tokens(obj) if obj else None
        sym, refs = _field_symbol(f"{path}::{name}", obj, name, root, None)
        symbols.append(sym)
    elif root.tag == "CustomObject":
        suffix = _OBJECT_META if lowered.endswith(_OBJECT_META) else ".object"
        obj = _strip_namespace_tokens(pure.name[: -len(suffix)])
        symbols.append(_object_symbol(path, obj, root))
        # Only the legacy single-file format carries fields inline.
        for el in root.all("fields"):
            name = el.first("fullName")
            if not name:
                continue
            name = _strip_namespace_tokens(name)
            sym, field_refs = _field_symbol(f"{path}::{obj}::{name}", obj, name, el, obj)
            symbols.append(sym)
            refs.extend(field_refs)
    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=[],
        exports=[s.name for s in symbols],
        schema_refs=refs,
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass
class _Project:
    package_dirs: tuple[str, ...]
    namespaces: frozenset[str]  # lowercased, each with its trailing "__"

    def key(self, name: str) -> str:
        """The platform's comparison key for an object or field API name."""
        folded = _strip_namespace_tokens(name.strip()).lower()
        for prefix in self.namespaces:
            rest = folded[len(prefix) :]
            # ``ns__Foo__c``: strip only a prefix that leaves a custom name.
            if folded.startswith(prefix) and "__" in rest:
                return rest
        return folded

    def preferred(self, paths_ids: Iterable[tuple[str, str]]) -> list[str]:
        """The one canonical copy of an org-wide object or field, or nothing.

        Copies across package dirs and ``unpackaged/config/*`` are fragments of
        the same object. Ranked: package dir (default first, then listed order),
        then anything else; within a rank the shortest path, then lexicographic.
        """

        def rank(pair: tuple[str, str]) -> tuple[int, int, str]:
            path = pair[0]
            place = next(
                (
                    i
                    for i, d in enumerate(self.package_dirs)
                    if path == d or path.startswith(d + "/")
                ),
                len(self.package_dirs),
            )
            return (place, len(path), path)

        best = min(paths_ids, key=rank, default=None)
        return [best[1]] if best is not None else []


def _read_project(repo_path: Path | None) -> _Project:
    dirs: list[str] = []
    namespaces: set[str] = set()
    if repo_path is not None:
        try:
            data = json.loads((repo_path / "sfdx-project.json").read_text(encoding="utf-8"))
            entries = [e for e in data.get("packageDirectories") or [] if isinstance(e, dict)]
            # The default package directory ranks first, the rest in listed order.
            for entry in sorted(entries, key=lambda e: e.get("default") is not True):
                d = str(entry.get("path") or "").replace("\\", "/").strip("/")
                d = d[2:] if d.startswith("./") else d
                if d:
                    dirs.append(d)
            if data.get("namespace"):
                namespaces.add(str(data["namespace"]))
        except (OSError, ValueError, AttributeError):
            pass
        try:
            config = yaml.safe_load((repo_path / "cumulusci.yml").read_text(encoding="utf-8"))
            namespace = config["project"]["package"]["namespace"]
            if namespace:
                namespaces.add(str(namespace))
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            pass
    return _Project(tuple(dirs), frozenset(f"{ns.lower()}__" for ns in namespaces))


@dataclass
class _Index:
    project: _Project
    objects: dict[str, list[tuple[str, str]]]  # key -> [(path, symbol id)]
    fields: dict[tuple[str, str], list[tuple[str, str]]]  # (object key, field key)
    lookups: dict[str, set[str]]  # field symbol id -> referenced object keys


def _build_index(parsed_files: dict[str, ParsedFile], project: _Project) -> _Index:
    objects: dict[str, list[tuple[str, str]]] = defaultdict(list)
    fields: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    lookups: dict[str, set[str]] = defaultdict(set)
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != _LANGUAGE:
            continue
        for sym in parsed.symbols:
            if sym.kind == "class":
                objects[project.key(sym.name)].append((path, sym.id))
            elif sym.kind == "property" and "." in sym.qualified_name:
                obj = sym.qualified_name.rsplit(".", 1)[0]
                fields[(project.key(obj), project.key(sym.name))].append((path, sym.id))
        for ref in parsed.schema_refs:
            lookups[ref.source_symbol_id].add(project.key(ref.object_name))
    return _Index(project, objects, fields, lookups)


def _resolve(index: _Index, ref: SchemaReference) -> Iterator[str]:
    key = index.project.key
    current = key(ref.object_name)
    if not ref.path:
        yield from index.project.preferred(index.objects.get(current, ()))
        return
    for i, segment in enumerate(ref.path):
        seg = key(segment)
        if i == len(ref.path) - 1:
            yield from index.project.preferred(index.fields.get((current, seg), ()))
            return
        # ``Rel__r`` hops through lookup ``Rel__c`` (``Account`` through ``AccountId``) to one object.
        lookup = seg[:-3] + "__c" if seg.endswith("__r") else seg + "id"
        copies = index.fields.get((current, lookup), ())
        yield from index.project.preferred(copies)
        # Every copy is a fragment of one field, so any of them may carry the referenceTo.
        targets = set().union(*(index.lookups.get(sid, set()) for _, sid in copies))
        if len(targets) != 1:
            return
        current = next(iter(targets))


def add_schema_edges(
    graph: nx.DiGraph, parsed_files: dict[str, ParsedFile], repo_path: Path | None
) -> int:
    """Add a symbol ``references`` and a file ``reads`` edge per bound reference.

    Returns the symbol edges added. Not ``calls``: a query runs no code.
    """
    if not any(p.schema_refs for p in parsed_files.values()):
        return 0
    index = _build_index(parsed_files, _read_project(repo_path))
    added = 0
    for parsed in parsed_files.values():
        for ref in parsed.schema_refs:
            source = ref.source_symbol_id
            if source not in graph:
                continue
            for target in _resolve(index, ref):
                if target not in graph:
                    continue
                if not graph.has_edge(source, target):
                    graph.add_edge(
                        source,
                        target,
                        edge_type="references",
                        confidence=_CONFIDENCE,
                        hint_source="salesforce_schema",
                    )
                    added += 1
                src_file = graph.nodes[source].get("file_path")
                tgt_file = graph.nodes[target].get("file_path")
                if (
                    src_file
                    and tgt_file
                    and src_file != tgt_file
                    and not graph.has_edge(src_file, tgt_file)
                ):
                    graph.add_edge(
                        src_file,
                        tgt_file,
                        edge_type="reads",
                        confidence=_CONFIDENCE,
                        imported_names=[],
                    )
    return added
