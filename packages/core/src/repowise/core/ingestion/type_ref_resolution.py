"""Resolve ``TypeReference`` records to file-level ``imports`` edges.

Background
==========
Static-typed, DI-heavy languages (C#, Java, Kotlin, Scala, Swift) place
half their dependency surface inside constructor and method parameter
lists rather than at the top of the file. A constructor like::

    public class BasketViewModel(IBasketService basket) { ... }

declares a hard dependency on ``IBasketService`` that the existing
``using``-directive resolver never sees — there is no statement to
translate into a file-to-file edge. The result is a graph in which every
class registered for DI as a concrete implementation reads as an
orphan, and dead-code analysis fires on every interface and ViewModel.

This module closes the gap. The parser emits ``TypeReference`` records
from ``@param.type`` captures in each language's ``.scm`` file; this
module resolves them to defining files and emits ``imports`` edges
during the graph build phase.

Design
======
Per-language *strategies* sit behind a single ``resolve_type_refs``
entrypoint. A strategy receives the ``ParsedFile``, the resolver
context, and the graph being built, and is responsible for emitting
edges into the graph. Strategies are registered in
``_STRATEGIES`` keyed by ``LanguageTag``.

Adding a new language is a matter of:
    1. Capturing ``@param.type`` in that language's ``.scm`` file.
    2. Writing a ``_resolve_<lang>_type_refs`` function (typically a
       30-line wrapper that calls the language's existing resolver
       index — Java uses the package map, Kotlin the package map plus
       Gradle sourceSets, Swift the SPM target map, etc).
    3. Registering it in ``_STRATEGIES``.

No changes are required to ``parser.py`` or ``graph.py`` to add a
language — the dispatcher walks every ``ParsedFile`` and routes by
language tag.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from .models import ParsedFile

if TYPE_CHECKING:
    import networkx as nx

    from .resolvers import ResolverContext

log = structlog.get_logger(__name__)

# Confidence floor for synthesised type-use edges. Lower than a real
# `using` directive (~1.0) because a same-name type can be defined in
# multiple files and we rank-pick the most likely. The dead-code
# analyzer treats any confidence > 0 as "reachable" so the exact value
# only matters for downstream weighting (PageRank, blast-radius).
_TYPE_USE_CONFIDENCE = 0.8


# ---------------------------------------------------------------------------
# Strategy: C# / .NET
# ---------------------------------------------------------------------------

def _resolve_csharp_type_refs(
    parsed: ParsedFile,
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> int:
    """Resolve C# ``@param.type`` captures via ``DotNetProjectIndex``.

    Returns the number of edges emitted. Same-file references and
    references to builtin types are dropped silently (the parser
    already filters builtins, but defence-in-depth is cheap here).
    """
    from .resolvers.dotnet import get_or_build_index

    if not parsed.type_refs:
        return 0

    index = get_or_build_index(ctx)
    if index is None or not index.type_map:
        return 0

    from_path = parsed.file_info.path
    from_abs = Path(parsed.file_info.abs_path) if parsed.file_info.abs_path else None
    if from_abs is None:
        return 0

    emitted = 0
    for ref in parsed.type_refs:
        candidates = index.rank_type_candidates(ref.type_name, from_abs)
        if not candidates:
            continue
        target_abs = candidates[0]
        # Convert to repo-relative POSIX path for graph keying.
        try:
            target_rel = target_abs.resolve().relative_to(index.repo_path).as_posix()
        except ValueError:
            continue
        if target_rel == from_path:
            continue
        if not graph.has_node(target_rel):
            # The defining file may have been gated out (e.g. excluded
            # by .gitignore but still on disk). Skip silently.
            continue
        _add_or_merge_type_use_edge(
            graph,
            src=from_path,
            dst=target_rel,
            type_name=ref.type_name,
            origin=ref.origin,
        )
        emitted += 1
    return emitted


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

Strategy = Callable[[ParsedFile, "ResolverContext", "nx.DiGraph"], int]

# Add new languages here — see module docstring. Keep the entries
# tightly scoped: each strategy must only touch its own language's
# index, never share resolver state across languages.
_STRATEGIES: dict[str, Strategy] = {
    "csharp": _resolve_csharp_type_refs,
}


def resolve_type_refs(
    parsed_files: dict[str, ParsedFile],
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> dict[str, int]:
    """Dispatch each parsed file to its language's type-ref strategy.

    Returns a per-language emitted-edge count for logging.
    """
    counts: dict[str, int] = {}
    for parsed in parsed_files.values():
        lang = parsed.file_info.language
        strategy = _STRATEGIES.get(lang)
        if strategy is None:
            continue
        emitted = strategy(parsed, ctx, graph)
        if emitted:
            counts[lang] = counts.get(lang, 0) + emitted
    if counts:
        log.info("type_use edges emitted", per_language=counts)
    return counts


# ---------------------------------------------------------------------------
# Edge writer
# ---------------------------------------------------------------------------

def _add_or_merge_type_use_edge(
    graph: "nx.DiGraph",
    src: str,
    dst: str,
    type_name: str,
    origin: str,
) -> None:
    """Add a ``type_use`` edge between two files, merging on conflict.

    The edge is persisted as its own ``edge_type='type_use'`` row so it
    is observable in ``graph_edges`` (the SQLite layer drops ad-hoc
    NetworkX attributes like ``via`` and ``origin``, so encoding the
    provenance in the edge type itself is the only round-tripping way
    to surface it). All file-reachability analyses
    (dead-code's ``in_degree`` check, PageRank, blast-radius) operate
    across edge types and still pick it up.

    If a stronger ``imports`` edge from a real ``using`` directive
    already connects the same files, leave it alone — the directive is
    strictly stronger evidence — and just record the type name in
    ``type_uses`` for traceability. Likewise, parallel type_use edges
    between the same pair of files are merged into one row with the
    full list of referenced types in ``imported_names``.
    """
    if graph.has_edge(src, dst):
        data = graph[src][dst]
        type_uses = data.setdefault("type_uses", [])
        if type_name not in type_uses:
            type_uses.append(type_name)
        # Keep imported_names in sync so the unused-export analyzer can
        # see the referenced type name as evidence of import-like usage.
        if data.get("edge_type") == "type_use":
            names = data.setdefault("imported_names", [])
            if type_name not in names:
                names.append(type_name)
        return
    graph.add_edge(
        src,
        dst,
        edge_type="type_use",
        origin=origin,
        confidence=_TYPE_USE_CONFIDENCE,
        type_uses=[type_name],
        imported_names=[type_name],
    )
