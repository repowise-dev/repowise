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
# Strategy: Rust
# ---------------------------------------------------------------------------

_RUST_BUILTIN_TYPES = frozenset({
    "bool", "char", "str", "u8", "u16", "u32", "u64", "u128", "usize",
    "i8", "i16", "i32", "i64", "i128", "isize", "f32", "f64",
    "String", "Vec", "Option", "Result", "Box", "Arc", "Rc",
    "HashMap", "HashSet", "BTreeMap", "BTreeSet", "Cow",
    "Pin", "Future", "Send", "Sync", "Sized", "Copy", "Clone",
    "Debug", "Display", "Default", "Iterator", "IntoIterator",
    "From", "Into", "TryFrom", "TryInto", "AsRef", "AsMut",
    "Fn", "FnMut", "FnOnce", "Drop", "Deref", "DerefMut",
    "Self", "self",
})


def _resolve_rust_type_refs(
    parsed: ParsedFile,
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> int:
    """Resolve Rust ``@param.type`` captures via stem map + import graph."""
    if not parsed.type_refs:
        return 0

    from_path = parsed.file_info.path
    emitted = 0

    import_targets: set[str] = set()
    for imp in parsed.imports:
        if imp.resolved_file and not imp.resolved_file.startswith("external:"):
            import_targets.add(imp.resolved_file)

    for ref in parsed.type_refs:
        type_name = ref.type_name
        if not type_name or type_name in _RUST_BUILTIN_TYPES:
            continue
        bare = type_name.rsplit("::", 1)[-1]
        if bare in _RUST_BUILTIN_TYPES:
            continue

        target = _find_rust_type_file(bare, from_path, import_targets, ctx, graph)
        if target is None:
            continue
        _add_or_merge_type_use_edge(graph, src=from_path, dst=target,
                                    type_name=bare, origin=ref.origin)
        emitted += 1
    return emitted


def _find_rust_type_file(
    type_name: str,
    from_path: str,
    import_targets: set[str],
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> str | None:
    """Find the file defining *type_name*, preferring imported files."""
    # Sorted: import_targets is a set; first-match must be deterministic.
    for imp_file in sorted(import_targets):
        if not graph.has_node(imp_file):
            continue
        for succ in graph.successors(imp_file):
            nd = graph.nodes.get(succ, {})
            if nd.get("node_type") == "symbol" and nd.get("name") == type_name:
                return imp_file

    candidates = ctx.stem_map.get(type_name.lower(), [])
    if len(candidates) == 1 and candidates[0] != from_path:
        if graph.has_node(candidates[0]):
            return candidates[0]

    return None


# ---------------------------------------------------------------------------
# Strategy: Go
# ---------------------------------------------------------------------------

def _resolve_go_type_refs(
    parsed: ParsedFile,
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> int:
    """Resolve Go ``@param.type`` captures via the Go package index.

    Go references a type either bare (same package — no import) or
    package-qualified (``pkg.Type`` — the qualifier was already stripped by
    the head extractor). The candidate set is therefore the file's own
    package siblings plus every ``.go`` file in each imported local package.
    The type name is matched against the symbols those files define, and a
    file-level ``type_use`` edge is emitted — which is what lets the
    unused-export pass see a struct used only as a field/param/return type.
    """
    if not parsed.type_refs:
        return 0

    from .parser_helpers import _GO_BUILTIN_TYPES
    from .resolvers.go_workspace import get_or_build_go_index

    index = get_or_build_go_index(ctx)
    from_path = parsed.file_info.path

    # Candidate defining files: same-package siblings (referenced with no
    # import) + every file in each imported local package (Phase 1 fan-out
    # means a single import already maps to all of a package's files).
    candidates: set[str] = set()
    own_pkg = index.package_for_file(from_path)
    if own_pkg:
        candidates.update(own_pkg.files)
    for imp in parsed.imports:
        files = index.files_for_import(imp.module_path)
        if files:
            candidates.update(files)
        elif imp.resolved_file and not imp.resolved_file.startswith("external:"):
            candidates.add(imp.resolved_file)
    candidates.discard(from_path)
    if not candidates:
        return 0

    emitted = 0
    seen_targets: set[tuple[str, str]] = set()
    for ref in parsed.type_refs:
        name = ref.type_name
        if not name or name in _GO_BUILTIN_TYPES:
            continue
        target = _find_go_type_file(name, candidates, graph)
        if target is None or target == from_path:
            continue
        if (name, target) in seen_targets:
            continue
        seen_targets.add((name, target))
        _add_or_merge_type_use_edge(graph, src=from_path, dst=target,
                                    type_name=name, origin=ref.origin)
        emitted += 1
    return emitted


def _find_go_type_file(
    type_name: str,
    candidate_files: set[str],
    graph: "nx.DiGraph",
) -> str | None:
    """Return a candidate file that defines a type named *type_name*."""
    # Sorted: set iteration; first-match must be deterministic.
    for cand in sorted(candidate_files):
        if not graph.has_node(cand):
            continue
        for succ in graph.successors(cand):
            nd = graph.nodes.get(succ, {})
            if nd.get("node_type") == "symbol" and nd.get("name") == type_name:
                return cand
    return None


# ---------------------------------------------------------------------------
# Strategy: C / C++
# ---------------------------------------------------------------------------

# ``std::vector`` / ``std::optional`` / ``std::shared_ptr`` — when the
# parser captures these as type-ref heads (no unwrap path through the
# grammar caught the inner T), the lookup is guaranteed to find nothing
# useful. Filter them so we don't waste a per-ref graph walk. Same idea
# as the TS/Rust builtin filters above.
_CPP_STL_HEAD_NAMES: frozenset[str] = frozenset({
    "vector", "array", "deque", "list", "forward_list",
    "set", "multiset", "map", "multimap",
    "unordered_set", "unordered_multiset",
    "unordered_map", "unordered_multimap",
    "stack", "queue", "priority_queue", "span",
    "string", "string_view", "wstring", "u16string", "u32string",
    "pair", "tuple",
    "optional", "variant", "any", "bitset",
    "shared_ptr", "unique_ptr", "weak_ptr",
    "function", "reference_wrapper", "atomic", "atomic_ref",
    "future", "promise", "shared_future",
    "thread", "mutex", "lock_guard", "unique_lock", "shared_lock",
    "condition_variable", "condition_variable_any",
    "chrono", "duration", "time_point",
    "initializer_list", "common_type", "decay", "remove_reference",
    "enable_if", "is_same", "conditional",
    # Smart casts / trait helpers
    "make_shared", "make_unique", "make_pair", "make_tuple",
})


def _resolve_c_type_refs(
    parsed: ParsedFile,
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> int:
    """Resolve C / C++ ``@param.type`` captures via ``#include`` + stem map.

    A struct or typedef declared in a header and used as a field / param /
    return type in a ``.c`` that ``#include``s the header has no statement
    naming the type — the ``#include`` resolves to the header *file*, but
    the type itself never lands in ``imported_names``. This mirrors the Go
    type-ref strategy for ``.go`` and the Rust one for ``.rs``: resolve the
    bare type name against the files this translation unit includes, falling
    back to a unique global stem match, and emit a ``type_use`` edge so the
    unused-export pass sees the header type as used.

    C++ extensions over the bare C strategy:
      * ``std::*`` container heads (``vector``, ``optional``, ``map`` …)
        are filtered up-front — the inner ``T`` template argument is
        captured separately by the template_argument_list query and
        resolves on its own. The container head will never name a user
        type.
      * The same-target sibling file set from
        :class:`CppWorkspaceIndex` is considered alongside ``#include``d
        files, so a type declared in one TU and used in a sibling TU
        of the same CMake / Bazel target resolves even when no header
        wires them together.
    """
    if not parsed.type_refs:
        return 0

    from .parser_helpers import _C_BUILTIN_TYPES

    from_path = parsed.file_info.path
    is_cpp = parsed.file_info.language == "cpp"

    import_targets: set[str] = set()
    for imp in parsed.imports:
        if imp.resolved_file and not imp.resolved_file.startswith("external:"):
            import_targets.add(imp.resolved_file)

    sibling_files: set[str] = set()
    if is_cpp:
        try:
            from .resolvers.cpp_workspace import get_or_build_cpp_index

            cpp_index = get_or_build_cpp_index(ctx)
            sibling_files = set(cpp_index.siblings_in_targets(from_path))
        except Exception:
            sibling_files = set()

    emitted = 0
    seen_targets: set[tuple[str, str]] = set()
    for ref in parsed.type_refs:
        name = ref.type_name
        if not name or name in _C_BUILTIN_TYPES:
            continue
        if is_cpp and name in _CPP_STL_HEAD_NAMES:
            continue
        target = _find_c_type_file(
            name, from_path, import_targets, sibling_files, ctx, graph
        )
        if target is None or target == from_path:
            continue
        if (name, target) in seen_targets:
            continue
        seen_targets.add((name, target))
        _add_or_merge_type_use_edge(graph, src=from_path, dst=target,
                                    type_name=name, origin=ref.origin)
        emitted += 1
    return emitted


def _find_c_type_file(
    type_name: str,
    from_path: str,
    import_targets: set[str],
    sibling_files: set[str],
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> str | None:
    """Find the file defining *type_name*, preferring ``#include``d headers.

    Falls back to *sibling_files* (same workspace target — usually a
    sibling ``.cc`` declaring an internal class that another ``.cc`` in
    the same target uses by value), then the global stem map.
    """
    # Sorted: import_targets is a set; first-match must be deterministic.
    for imp_file in sorted(import_targets):
        if not graph.has_node(imp_file):
            continue
        for succ in graph.successors(imp_file):
            nd = graph.nodes.get(succ, {})
            if nd.get("node_type") == "symbol" and nd.get("name") == type_name:
                return imp_file

    # Sorted: set iteration; first-match must be deterministic.
    for sib in sorted(sibling_files):
        if sib == from_path or not graph.has_node(sib):
            continue
        for succ in graph.successors(sib):
            nd = graph.nodes.get(succ, {})
            if nd.get("node_type") == "symbol" and nd.get("name") == type_name:
                return sib

    candidates = ctx.stem_map.get(type_name.lower(), [])
    if len(candidates) == 1 and candidates[0] != from_path:
        if graph.has_node(candidates[0]):
            return candidates[0]

    return None


# ---------------------------------------------------------------------------
# Strategy: TypeScript / JavaScript
# ---------------------------------------------------------------------------

def _resolve_ts_type_refs(
    parsed: ParsedFile,
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> int:
    """Resolve TS/JS ``@param.type`` captures via import bindings + stem map.

    TypeScript has no central type-name registry the way C# has the
    project's type-map; the canonical place to learn ``Foo``'s defining
    file is the file's own ``import`` statements. The resolution order
    mirrors how ``tsc`` itself reasons about names:

        1. ``import { Foo } from './x'``    — bound name; the import's
           ``resolved_file`` is the answer.
        2. ``import * as ns from './x'``    — ``ns.Foo`` strips to ``Foo``;
           the namespace alias points at the module.
        3. Stem-map fallback                — unique global file whose
           basename matches the type name; matches Rust's strategy.
        4. Same-file definitions are skipped silently (no edge needed).

    This emits ``type_use`` edges at file granularity — the dead-code
    analyzer's unused-export pass consumes ``imported_names`` on either
    edge type, so an ``interface Foo`` referenced only as a field type
    in a sibling module is no longer flagged.
    """
    if not parsed.type_refs:
        return 0

    from_path = parsed.file_info.path

    # name -> (resolved_file, exported_name) for direct imports.
    name_to_source: dict[str, str] = {}
    # alias -> resolved_file for namespace / module imports.
    namespace_to_source: dict[str, str] = {}
    for imp in parsed.imports:
        if not imp.resolved_file or imp.resolved_file.startswith("external:"):
            continue
        for binding in imp.bindings:
            if binding.local_name == "*":
                continue
            if binding.is_module_alias:
                namespace_to_source[binding.local_name] = imp.resolved_file
            else:
                name_to_source[binding.local_name] = imp.resolved_file

    # Symbol names defined in this file — used to filter same-file refs.
    local_names: set[str] = {s.name for s in parsed.symbols if s.name}

    # Stamp same-file type references on the file node so the dead-code
    # analyzer can treat an ``interface Foo`` referenced as ``bar: Foo``
    # inside its own file as live, even though we never emit a self-loop
    # edge. The C#/Go strategies don't need this because their resolvers
    # rely on package-level symbol indexes and reach types through value
    # imports; TS commonly defines a private interface beside the class
    # that consumes it (``src/context.ts::Get`` is the canonical case).
    same_file_refs: set[str] = set()

    emitted = 0
    seen: set[tuple[str, str]] = set()
    for ref in parsed.type_refs:
        name = ref.type_name
        if not name:
            continue
        if name in local_names:
            same_file_refs.add(name)
            continue
        target = name_to_source.get(name) or namespace_to_source.get(name)
        if target is None:
            target = _find_ts_type_in_stem_map(name, from_path, ctx, graph)
        if target is None or target == from_path:
            continue
        if (name, target) in seen:
            continue
        seen.add((name, target))
        if not graph.has_node(target):
            continue
        _add_or_merge_type_use_edge(
            graph, src=from_path, dst=target, type_name=name, origin=ref.origin
        )
        emitted += 1

    if same_file_refs and graph.has_node(from_path):
        existing = graph.nodes[from_path].get("local_type_uses")
        if existing is None:
            graph.nodes[from_path]["local_type_uses"] = same_file_refs
        else:
            existing.update(same_file_refs)

    return emitted


def _find_ts_type_in_stem_map(
    type_name: str,
    from_path: str,
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> str | None:
    """Locate ``type_name`` via the global stem map.

    Used when no import binds the name — common when a sibling module
    in the same package augments a global ``namespace`` or when a type
    is reached via a tsconfig-paths alias whose binding wasn't extracted
    (rare but possible). A match is only accepted if exactly one file
    in the workspace defines a top-level symbol by that name.
    """
    candidates = ctx.stem_map.get(type_name.lower(), [])
    if len(candidates) != 1:
        return None
    candidate = candidates[0]
    if candidate == from_path or not graph.has_node(candidate):
        return None
    # Verify the candidate file actually exports a symbol matching name —
    # avoids accidentally matching ``utils.ts`` to a type called ``Utils``.
    for succ in graph.successors(candidate):
        nd = graph.nodes.get(succ, {})
        if nd.get("node_type") == "symbol" and nd.get("name") == type_name:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Strategy: Java / Kotlin (shared JVM workspace)
# ---------------------------------------------------------------------------

def _resolve_jvm_type_refs(
    parsed: ParsedFile,
    ctx: "ResolverContext",
    graph: "nx.DiGraph",
) -> int:
    """Resolve Java / Kotlin ``@param.type`` captures via ``JvmWorkspaceIndex``.

    JVM resolution order — mirrors how ``javac`` / ``kotlinc`` resolve a
    bare type name in a source file:

      1. Same-package siblings — both ``.java`` and ``.kt`` files in
         the package directory (Java + Kotlin co-located).
      2. Explicit imports (``import com.foo.Bar`` resolves to that file
         via the workspace's FQN map; wildcard ``import com.foo.*`` /
         ``import static com.foo.Bar.*`` fan out to every file in the
         package; both are already attached to the file's ``Import``
         records by the per-language resolver).
      3. ``java.lang`` builtins are filtered at extraction time by the
         per-language head extractor — they never reach this code.

    The candidate file set is collected once and then scanned for each
    type reference, mirroring the Go strategy. Edges are emitted at
    file granularity with the standard ``type_use`` confidence so the
    unused-export pass sees field/param/return-only types as live.
    """
    if not parsed.type_refs:
        return 0

    from .resolvers.jvm_workspace import get_or_build_jvm_index

    index = get_or_build_jvm_index(ctx)
    from_path = parsed.file_info.path

    candidates: set[str] = set()
    own_pkg = index.package_for_file(from_path)
    if own_pkg:
        pkg = index.packages.get(own_pkg)
        if pkg is not None:
            candidates.update(pkg.files)
    for imp in parsed.imports:
        resolved = imp.resolved_file
        if resolved and not resolved.startswith("external:"):
            candidates.add(resolved)
    candidates.discard(from_path)
    if not candidates:
        return 0

    emitted = 0
    seen_targets: set[tuple[str, str]] = set()
    for ref in parsed.type_refs:
        name = ref.type_name
        if not name:
            continue
        target = _find_jvm_type_file(name, candidates, graph)
        if target is None or target == from_path:
            continue
        if (name, target) in seen_targets:
            continue
        seen_targets.add((name, target))
        if not graph.has_node(target):
            continue
        _add_or_merge_type_use_edge(
            graph, src=from_path, dst=target, type_name=name, origin=ref.origin
        )
        emitted += 1
    return emitted


def _find_jvm_type_file(
    type_name: str,
    candidate_files: set[str],
    graph: "nx.DiGraph",
) -> str | None:
    """Return a candidate file that defines a top-level type named *type_name*."""
    # Sorted: set iteration; first-match must be deterministic.
    for cand in sorted(candidate_files):
        if not graph.has_node(cand):
            continue
        for succ in graph.successors(cand):
            nd = graph.nodes.get(succ, {})
            if nd.get("node_type") == "symbol" and nd.get("name") == type_name:
                return cand
    return None


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

Strategy = Callable[[ParsedFile, "ResolverContext", "nx.DiGraph"], int]

# Add new languages here — see module docstring. Keep the entries
# tightly scoped: each strategy must only touch its own language's
# index, never share resolver state across languages.
_STRATEGIES: dict[str, Strategy] = {
    "csharp": _resolve_csharp_type_refs,
    "rust": _resolve_rust_type_refs,
    "go": _resolve_go_type_refs,
    "c": _resolve_c_type_refs,
    "cpp": _resolve_c_type_refs,
    "typescript": _resolve_ts_type_refs,
    "javascript": _resolve_ts_type_refs,
    "java": _resolve_jvm_type_refs,
    "kotlin": _resolve_jvm_type_refs,
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

    If a stronger ``imports`` edge from a real ``using``/package directive
    already connects the same files, leave its ``edge_type`` and confidence
    alone — the directive is strictly stronger evidence — but still record
    the referenced type in both ``type_uses`` (provenance) and
    ``imported_names``. The latter matters because the unused-export pass
    treats ``imported_names`` as the set of names used from a file: a type
    referenced as a field/param/return is genuinely used, and Go's package
    import fan-out means the defining file almost always already carries an
    ``imports`` edge keyed by the *package* alias, not the type name —
    without this the type would still read as an unused export.
    """
    if graph.has_edge(src, dst):
        data = graph[src][dst]
        type_uses = data.setdefault("type_uses", [])
        if type_name not in type_uses:
            type_uses.append(type_name)
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
