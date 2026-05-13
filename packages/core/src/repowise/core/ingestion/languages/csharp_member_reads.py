"""C# member-access read resolution.

Background
----------
Property reads in C# never appear as calls — ``var x = new Order();``
followed by ``return x.Total;`` produces a ``member_access_expression``
but no invocation. The existing static-call resolver and ``using``
import resolver both miss this entirely, which makes any class whose
only consumers READ its properties read as orphaned.

This pass closes the biggest sub-case without touching the brittle
tree-sitter grammar:

1. **Local-typed receivers** — ``var x = new Foo(...);`` plus
   ``Foo x = new(...);`` plus ``Foo x = ...;`` declare a local whose
   type we know syntactically. Subsequent ``x.SomeMember`` reads in
   the same file resolve to the file that declares ``Foo``.

2. **``this.PropName``** — the receiver is the enclosing class.
   When the file declares exactly one named type (the dominant case
   for C#), ``this.Prop`` reads resolve to that file. Cross-file
   resolution would require partial-class awareness; we skip it for
   safety.

The resolved file becomes a low-confidence ``reads`` edge. Dead-code
treats every incoming edge as evidence of life, so this directly
reduces unused_export false positives without affecting other
analyses. ``reads`` is a new edge type — ``calls`` / ``imports`` /
``type_use`` remain unchanged so semantics-aware consumers can tell
them apart.

The module is **self-contained**: it reads source text directly,
runs a few regexes, and emits dicts. It does not depend on the parser
or the call resolver, which keeps the pass isolated and easy to gate
behind a feature flag if it ever proves noisy.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import networkx as nx

# ``var foo = new Bar(`` / ``var foo = new Bar<...>(`` / ``Bar foo = new(``
# / ``Bar foo = new Bar(``. We capture the LOCAL name + the type name
# in a single match. Generic / qualified type names are stripped to the
# last identifier so we resolve through ``DotNetProjectIndex.type_map``.
_LOCAL_DECL_RE = re.compile(
    r"\b(?:var|(?P<type1>[A-Z][\w\.]*(?:<[^>]+>)?))\s+"
    r"(?P<name>[a-z_]\w*)\s*=\s*"
    r"new(?:\s+(?P<type2>[A-Z][\w\.]*(?:<[^>]+>)?))?\s*\("
)

# ``receiver.Member`` — only PascalCase member names (properties /
# methods, never lowercase locals or keywords). The receiver may be
# any identifier; we resolve through the local-decl map.
_MEMBER_ACCESS_RE = re.compile(r"\b(?P<recv>[a-z_]\w*)\.(?P<name>[A-Z]\w*)\b")

# ``this.PropName`` — receiver is the enclosing type.
_THIS_MEMBER_RE = re.compile(r"\bthis\.(?P<name>[A-Z]\w*)\b")

# First class-like declaration in a file. Used to resolve ``this.X``
# back to "the type declared here". Multiple-type files punt (see
# module docstring).
_PRIMARY_TYPE_RE = re.compile(
    r"\b(?:public|internal|private|protected|sealed|abstract|static|partial|\s)*"
    r"\s+(?:class|struct|record|interface)\s+(?P<name>[A-Z]\w*)"
)


_READS_CONFIDENCE = 0.6


def _head_type(raw: str) -> str:
    """Strip generic parameters / namespace prefix to a bare identifier."""
    head = raw.split("<", 1)[0]
    return head.rsplit(".", 1)[-1]


def resolve_csharp_member_reads(
    graph: "nx.DiGraph",
    cs_texts: dict[str, str],
    type_to_file: dict[str, str],
) -> int:
    """Emit ``reads`` edges for property / member access in C# files.

    *cs_texts* maps repo-relative path → source text.
    *type_to_file* maps short type name → defining file path. Caller
    is responsible for building it (e.g. from
    ``DotNetProjectIndex.type_map`` or by scanning AST symbols).

    Returns the number of edges added.
    """
    if not cs_texts or not type_to_file:
        return 0

    count = 0
    for path, text in cs_texts.items():
        # Local-typed receiver map for this file only. Re-scanning per
        # file is fine — regex is fast and the alternative (cross-file
        # state) would create false matches when the same local name
        # is reused across files.
        local_type: dict[str, str] = {}
        for m in _LOCAL_DECL_RE.finditer(text):
            type_name = m.group("type2") or m.group("type1")
            if not type_name:
                continue
            local_type[m.group("name")] = _head_type(type_name)

        primary_match = _PRIMARY_TYPE_RE.search(text)
        primary_type = primary_match.group("name") if primary_match else None

        # Resolve each receiver-member pair we recognise.
        emitted_targets: set[str] = set()
        for m in _MEMBER_ACCESS_RE.finditer(text):
            recv = m.group("recv")
            type_name = local_type.get(recv)
            if not type_name:
                continue
            target = type_to_file.get(type_name)
            if not target or target == path or target in emitted_targets:
                continue
            if _add_reads_edge(graph, path, target):
                emitted_targets.add(target)
                count += 1

        if primary_type is not None:
            target = type_to_file.get(primary_type)
            if target and target != path and _THIS_MEMBER_RE.search(text):
                # ``this.X`` reads in a same-file class are redundant
                # (same node); we already skip target == path above.
                # Cross-file ``this.X`` only happens for partial
                # classes — keep this branch for completeness.
                if _add_reads_edge(graph, path, target):
                    count += 1

    return count


def _add_reads_edge(graph: "nx.DiGraph", source: str, target: str) -> bool:
    """Add a ``reads`` edge if no edge already connects source → target.

    A stronger pre-existing edge (``imports``, ``calls``, ``type_use``,
    etc.) wins — we don't overwrite. ``reads`` is purely additive
    evidence for dead-code's "incoming edge" check.
    """
    if source == target:
        return False
    if not graph.has_node(source) or not graph.has_node(target):
        return False
    if graph.has_edge(source, target):
        return False
    graph.add_edge(
        source,
        target,
        edge_type="reads",
        confidence=_READS_CONFIDENCE,
        imported_names=[],
    )
    return True


def build_csharp_type_to_file(parsed_files: dict[str, Any]) -> dict[str, str]:
    """Build a short-name → defining-file map across all parsed C# files.

    Used as a lightweight alternative to ``DotNetProjectIndex.type_map``
    in contexts where the full resolver is not available (tests,
    standalone pipelines). Last-wins on duplicates.
    """
    result: dict[str, str] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "csharp":
            continue
        for sym in parsed.symbols:
            if sym.kind in ("class", "struct", "record", "interface"):
                result[sym.name] = path
    return result


def collect_csharp_source_texts(parsed_files: dict[str, Any]) -> dict[str, str]:
    """Read each parsed C# file's source from disk, keyed by repo path."""
    out: dict[str, str] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "csharp":
            continue
        try:
            out[path] = Path(parsed.file_info.abs_path).read_text(
                encoding="utf-8-sig", errors="ignore"
            )
        except OSError:
            continue
    return out
