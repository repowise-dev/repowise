"""Go structural interface satisfaction.

Background
----------
Go interfaces are satisfied *structurally*: a concrete type implements an
interface iff its method set is a superset of the interface's method set.
There is no ``implements`` keyword and no import recording the relationship,
so nothing in the import / heritage / call graph connects a concrete type to
the interfaces it satisfies. The consequence for dead-code analysis is that
an interface whose only consumers reach it through a concrete implementor
(``var w io.Writer = &File{}``; dependency-injected services; the
``return concrete`` / accept-interface-return-struct idiom) reads as an
unreferenced public export — the dominant residual ``unused_export`` bucket
for Go after call/type resolution lands (Phase 3).

This pass closes that gap the way C#/Java heritage edges do, but computed
structurally because Go has no nominal ``implements`` clause:

1. For every concrete type, collect its method set from method declarations
   (receiver type → method names), aggregated across the package's files.
2. For every interface, collect its method set from ``method_elem`` members,
   expanding embedded interfaces (``type_elem``) transitively within the repo.
3. A concrete type *satisfies* an interface when its method set ⊇ the
   interface's (non-empty) method set. Emit a ``method_implements`` edge
   ``concrete_type → interface_type``.

The edge direction (implementor → interface) lands the usage signal on the
**interface** symbol, which the dead-code analyzer reads as "something
implements this, so it is not dead" — exactly the existing treatment of
incoming ``method_implements`` edges (shared with C++/C# qualified-method
resolution). ``method_implements`` is reused rather than a plain heritage
``implements`` edge so the suppression is Go-local and needs no change to
the analyzer's edge-type tables.

The pass is **self-contained**: it parses Go source with the shared
tree-sitter grammar and emits edges. It does not depend on the resolver
context, the call resolver, or ``GoPackageIndex``. Matching is by method
*name* (signatures are not compared), so it is a deliberate over-approximation
of Go's structural rule — it can connect a type to an interface it does not
truly satisfy when names coincide. For dead-code that is the safe direction:
it suppresses *false* "dead interface" findings; it never invents a finding.
A genuinely dead interface with no name-matching implementor is still flagged.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import networkx as nx
    from tree_sitter import Node

log = structlog.get_logger(__name__)

_IMPLEMENTS_CONFIDENCE = 0.6

# Cap on embedded-interface expansion depth — guards against cyclic or
# pathological embedding chains. Real Go embedding nests only a level or two.
_MAX_EMBED_DEPTH = 6


def _receiver_type_name(method_node: "Node", src: bytes) -> str | None:
    """Return the bare receiver type of a ``method_declaration``.

    ``func (f *File) M()`` → ``File``; ``func (f File) M()`` → ``File``.
    The receiver is the first ``parameter_list`` child (the one preceding the
    method name); the declared type may be wrapped in a ``pointer_type``.
    """
    recv = None
    for child in method_node.children:
        if child.type == "parameter_list":
            recv = child
            break
    if recv is None:
        return None
    for pdecl in recv.children:
        if pdecl.type != "parameter_declaration":
            continue
        type_node = pdecl.child_by_field_name("type")
        if type_node is None:
            continue
        if type_node.type == "pointer_type":
            for c in type_node.children:
                if c.type in ("type_identifier", "generic_type"):
                    type_node = c
                    break
        if type_node.type == "generic_type":
            inner = type_node.child_by_field_name("type")
            if inner is not None:
                type_node = inner
        if type_node.type == "type_identifier":
            return src[type_node.start_byte : type_node.end_byte].decode(
                "utf-8", "ignore"
            )
    return None


def _interface_facts(iface_node: "Node", src: bytes) -> tuple[set[str], list[str]]:
    """Return (explicit method names, embedded interface bare names)."""
    methods: set[str] = set()
    embedded: list[str] = []
    for elem in iface_node.children:
        if elem.type == "method_elem":
            name_node = elem.child_by_field_name("name")
            if name_node is None:
                # Older grammars expose the name as the first field_identifier.
                name_node = next(
                    (c for c in elem.children if c.type == "field_identifier"), None
                )
            if name_node is not None:
                methods.add(
                    src[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8", "ignore"
                    )
                )
        elif elem.type == "type_elem":
            text = src[elem.start_byte : elem.end_byte].decode("utf-8", "ignore").strip()
            # Embedded interface: ``io.Reader`` / ``Reader``. Skip type-set
            # constraints (``~int | string``) — those are generic bounds, not
            # method-bearing embeds.
            if "|" in text or "~" in text:
                continue
            bare = text.split(".")[-1].strip()
            if bare and bare[0].isalpha():
                embedded.append(bare)
    return methods, embedded


class _FileFacts:
    __slots__ = ("interfaces", "concrete_methods", "type_kind")

    def __init__(self) -> None:
        # name -> (explicit methods, embedded bare names)
        self.interfaces: dict[str, tuple[set[str], list[str]]] = {}
        # receiver type name -> method names declared in this file
        self.concrete_methods: dict[str, set[str]] = {}
        # type name -> "interface" | "concrete"
        self.type_kind: dict[str, str] = {}


def _extract_file_facts(root: "Node", src: bytes) -> _FileFacts:
    facts = _FileFacts()

    def walk(node: "Node") -> None:
        if node.type == "type_spec":
            name_node = node.child_by_field_name("name")
            type_node = node.child_by_field_name("type")
            if name_node is not None and type_node is not None:
                name = src[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", "ignore"
                )
                if type_node.type == "interface_type":
                    facts.interfaces[name] = _interface_facts(type_node, src)
                    facts.type_kind[name] = "interface"
                else:
                    facts.type_kind[name] = "concrete"
        elif node.type == "method_declaration":
            recv = _receiver_type_name(node, src)
            name_node = node.child_by_field_name("name")
            if recv is not None and name_node is not None:
                mname = src[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", "ignore"
                )
                facts.concrete_methods.setdefault(recv, set()).add(mname)
        for child in node.children:
            walk(child)

    walk(root)
    return facts


def _parse_go(src: bytes) -> "Node | None":
    """Parse Go source into a tree-sitter root node using the shared grammar."""
    from ..parser import _get_language  # local import: avoid cycle at module load

    try:
        from tree_sitter import Parser
    except Exception:  # pragma: no cover - tree_sitter always present in practice
        return None
    lang = _get_language("go")
    if lang is None:
        return None
    return Parser(lang).parse(src).root_node


def resolve_go_interface_satisfaction(
    graph: "nx.DiGraph", parsed_files: dict[str, Any]
) -> int:
    """Emit ``method_implements`` edges for structural Go interface satisfaction.

    Returns the number of edges added.
    """
    go_files = {
        path: parsed
        for path, parsed in parsed_files.items()
        if parsed.file_info.language == "go"
    }
    if not go_files:
        return 0

    # --- Pass 1: collect per-file facts -----------------------------------
    # Per package directory: type name -> defining file, and method sets.
    # Go's package is a directory, so methods of a type may be spread across
    # sibling files; aggregate by (pkg_dir, type_name).
    type_def_file: dict[tuple[str, str], str] = {}
    concrete_methods: dict[tuple[str, str], set[str]] = {}
    interface_methods: dict[tuple[str, str], set[str]] = {}
    interface_embeds: dict[tuple[str, str], list[str]] = {}

    for path, parsed in go_files.items():
        try:
            src = parsed.file_info.abs_path
            with open(src, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        root = _parse_go(data)
        if root is None:
            continue
        facts = _extract_file_facts(root, data)
        pkg = posixpath.dirname(path)

        for tname, kind in facts.type_kind.items():
            type_def_file[(pkg, tname)] = path
            if kind == "interface":
                methods, embeds = facts.interfaces[tname]
                interface_methods.setdefault((pkg, tname), set()).update(methods)
                interface_embeds.setdefault((pkg, tname), []).extend(embeds)
        for recv, mnames in facts.concrete_methods.items():
            concrete_methods.setdefault((pkg, recv), set()).update(mnames)

    if not interface_methods:
        return 0

    # --- Pass 2: expand embedded interfaces (transitive, within repo) ------
    # Resolve an embedded bare name to an interface in the same package first,
    # then any package. External embeds (io.Reader) stay unresolved — their
    # methods are simply absent from the set, which only makes matching more
    # conservative (a concrete type still needs the explicit methods).
    name_to_iface_keys: dict[str, list[tuple[str, str]]] = {}
    for key in interface_methods:
        name_to_iface_keys.setdefault(key[1], []).append(key)

    def _expand(key: tuple[str, str], depth: int, seen: set) -> set[str]:
        if key in seen or depth > _MAX_EMBED_DEPTH:
            return set(interface_methods.get(key, set()))
        seen.add(key)
        result = set(interface_methods.get(key, set()))
        pkg = key[0]
        for bare in interface_embeds.get(key, ()):
            # Prefer an embedded interface in the same package.
            target = None
            if (pkg, bare) in interface_methods:
                target = (pkg, bare)
            else:
                candidates = name_to_iface_keys.get(bare)
                if candidates and len(candidates) == 1:
                    target = candidates[0]
            if target is not None:
                result |= _expand(target, depth + 1, seen)
        return result

    expanded_iface: dict[tuple[str, str], frozenset[str]] = {}
    for key in interface_methods:
        methods = _expand(key, 0, set())
        if methods:
            expanded_iface[key] = frozenset(methods)

    # --- Pass 3: index concrete types by method name, then match ----------
    method_to_concrete: dict[str, list[tuple[str, str]]] = {}
    for ckey, mnames in concrete_methods.items():
        for m in mnames:
            method_to_concrete.setdefault(m, []).append(ckey)

    count = 0
    for ikey, imethods in expanded_iface.items():
        iface_file = type_def_file.get(ikey)
        if iface_file is None:
            continue
        iface_id = f"{iface_file}::{ikey[1]}"
        if not graph.has_node(iface_id):
            continue
        # Candidate concrete types = those declaring the interface's rarest
        # method; verify the full superset relation against each.
        rarest = min(imethods, key=lambda m: len(method_to_concrete.get(m, ())))
        candidates = method_to_concrete.get(rarest, ())
        for ckey in candidates:
            if ckey == ikey:
                continue
            if not imethods <= concrete_methods.get(ckey, set()):
                continue
            concrete_file = type_def_file.get(ckey)
            if concrete_file is None:
                continue
            concrete_id = f"{concrete_file}::{ckey[1]}"
            if concrete_id == iface_id or not graph.has_node(concrete_id):
                continue
            if graph.has_edge(concrete_id, iface_id):
                continue
            graph.add_edge(
                concrete_id,
                iface_id,
                edge_type="method_implements",
                confidence=_IMPLEMENTS_CONFIDENCE,
                imported_names=[],
            )
            count += 1

    return count
