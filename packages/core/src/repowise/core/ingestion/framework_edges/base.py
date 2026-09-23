"""Shared helpers and the ``FrameworkHandler`` protocol for framework-edge detection.

Split out of ``framework_edges.py`` (PR 3.5). Holds the cross-framework
primitives: the dedup ``_add_edge_if_new`` guard, the ``framework:`` anchor
edges, the unified ``read_text`` file reader (collapses the old ``_read_text``
/ ``_read_cs_text`` variants) and ``source_text``, which prefers the bytes
ingestion already read,
the class/function-name → file maps, and the ``FrameworkHandler`` protocol the
dispatcher iterates over.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import networkx as nx

    from ..framework_facts import FrameworkFacts
    from ..resolvers import ResolverContext


@dataclass(frozen=True)
class DetectionContext:
    """Inputs every handler's ``detect()`` may consult.

    Bundled once per ``add_framework_edges`` call so the dispatcher can iterate
    handlers uniformly regardless of which signal a given framework keys off.
    """

    stack_lower: set[str]
    parsed_files: dict[str, Any]
    ctx: ResolverContext
    path_set: set[str]


class FrameworkHandler(Protocol):
    """A single framework's detection + edge-emission pair."""

    def detect(self, dctx: DetectionContext) -> bool:
        """Return True when this framework's edges should be emitted."""
        ...

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        """Emit this framework's synthetic edges; return the count added."""
        ...


def _add_edge_if_new(graph: nx.DiGraph, source: str, target: str) -> bool:
    """Add a framework edge if no edge already exists. Returns True if added."""
    if source == target:
        return False
    if graph.has_edge(source, target):
        return False
    graph.add_edge(source, target, edge_type="framework", imported_names=[])
    return True


# Below `same_file` (0.95) because the framework can rebind at run time, level
# with the heritage-walk origins because the binding follows a documented rule
# and checks no signature.
FRAMEWORK_BIND_CONFIDENCE = 0.90


def add_symbol_edge(graph: nx.DiGraph, source: str, target: str) -> bool:
    """Link two *symbol* nodes the framework wires together.

    Not a flag on ``_add_edge_if_new``: a ``DiGraph`` holds one edge per ordered
    pair and ``defines`` already occupies (file, symbol), so that helper's
    first-wins guard would let containment silently swallow a symbol edge.
    Both ends must already be symbol nodes, so a wrong id cannot mint a bare
    node with no attributes for every later consumer to defend against.
    """
    if source == target:
        return False
    for node in (source, target):
        data = graph.nodes.get(node)
        if data is None or data.get("node_type") != "symbol":
            return False
    if graph.has_edge(source, target):
        return False
    graph.add_edge(
        source,
        target,
        edge_type="framework_binds",
        confidence=FRAMEWORK_BIND_CONFIDENCE,
        imported_names=[],
    )
    return True


def add_entry_edges(graph: nx.DiGraph, facts: FrameworkFacts, root: str, path_set: set[str]) -> int:
    """Anchor every file *facts* says its runtime loads under *root*.

    The ``framework:`` anchor is what dead-code liveness reads: a file it
    points at is reached from outside the import graph.
    """
    return sum(anchor_edge(graph, facts, target) for target in facts.entry_files(root, path_set))


def anchor_edge(graph: nx.DiGraph, facts: FrameworkFacts, target: str) -> bool:
    """Link *facts*' ``framework:`` anchor to *target*; True when the edge is new."""
    if facts.anchor not in graph:
        graph.add_node(facts.anchor, language="external")
    return _add_edge_if_new(graph, facts.anchor, target)


def read_text(parsed: Any, encoding: str = "utf-8") -> str:
    """Read a parsed file's source text, returning ``""`` on read failure.

    Collapses the former ``_read_text`` (utf-8) and ``_read_cs_text``
    (utf-8-sig) helpers — pass ``encoding="utf-8-sig"`` for C# BOM files.
    """
    try:
        return Path(parsed.file_info.abs_path).read_text(encoding=encoding, errors="ignore")
    except OSError:
        return ""


def source_text(
    path: str, parsed: Any, source_map: dict[str, bytes], encoding: str = "utf-8"
) -> str:
    """Source text for *path*, preferring the bytes ingestion already read.

    A handler whose file set is the repo's dominant language would otherwise
    make a second full pass over the tree.
    """
    raw = source_map.get(path)
    if raw is not None:
        return raw.decode(encoding, errors="replace")
    return read_text(parsed, encoding=encoding)


def _build_class_to_file(
    parsed_files: dict[str, Any], languages: tuple[str, ...]
) -> dict[str, str]:
    """Map declared class/interface/struct/enum/record names → file path."""
    result: dict[str, str] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in languages:
            continue
        for sym in parsed.symbols:
            if sym.kind in ("class", "interface", "struct", "record", "enum", "trait"):
                result.setdefault(sym.name, path)
    return result


def build_type_to_symbol(
    parsed_files: dict[str, Any], languages: tuple[str, ...]
) -> dict[str, str]:
    """Map a declared type name → its symbol id, for names declared exactly once.

    Stricter than :func:`_build_class_to_file`, which keeps the first declarer:
    an ambiguous name must stay unclaimed rather than bind to whichever file the
    walk reached first.
    """
    seen: dict[str, str | None] = {}
    for _path, parsed in parsed_files.items():
        if parsed.file_info.language not in languages:
            continue
        for sym in parsed.symbols:
            if sym.kind in ("class", "interface", "struct", "record", "enum", "trait"):
                seen[sym.name] = None if sym.name in seen else sym.id
    return {name: sid for name, sid in seen.items() if sid is not None}


def _build_function_to_file(
    parsed_files: dict[str, Any], languages: tuple[str, ...]
) -> dict[str, list[str]]:
    """Map declared function/method names → list of file paths declaring them."""
    result: dict[str, list[str]] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in languages:
            continue
        for sym in parsed.symbols:
            if sym.kind in ("function", "method"):
                result.setdefault(sym.name, []).append(path)
    return result


def _build_ts_var_to_file(
    parsed: Any, path: str, ctx: ResolverContext, path_set: set[str]
) -> dict[str, str]:
    """Map identifiers imported into a TS/JS file → their source file path.

    The TS/JS framework handlers (Express, Hono, Fastify, …) all need to
    resolve a bare identifier (``handler``, ``router``) used in a router
    DSL back to the file that exports it, so a synthetic edge from the
    DSL call site to the handler module can be emitted. Pulling this
    builder up to ``base`` avoids three near-identical copies.

    Keyed on ``local_names``, not ``imported_names``: the DSL names the
    handler as this file sees it, so ``import { handler as apiHandler }``
    has to answer to ``apiHandler``.
    """
    from ..resolvers import resolve_import

    var_to_file: dict[str, str] = {}
    for imp in parsed.imports:
        for name in imp.local_names:
            resolved = resolve_import(
                imp.module_path,
                path,
                parsed.file_info.language,
                ctx,
            )
            if resolved and resolved in path_set:
                var_to_file[name] = resolved
    return var_to_file
