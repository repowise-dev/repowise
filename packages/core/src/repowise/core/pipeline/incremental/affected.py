"""Widen a refreshing patch to every file its edit can make resolve differently.

:func:`~.surface.plan_patch` hands over the edited files plus what they
changed by name. An unchanged file can only resolve differently when

* one of its edges lands on a node the edit removes (a deleted file, or a
  symbol an edited file no longer declares);
* one of its imports now resolves to different files: found exactly, by
  re-resolving every unchanged file's imports against the new file set and
  comparing with what the last build recorded;
* it spells an identifier the edit touched, or one a re-export renames it to,
  or a file-name word of an added or removed file (type references look
  files up by stem);
* it imports a file whose default export changed.

Every such file is re-resolved. A file outside the patchable languages that
would need re-resolving, or a scope too large to save time, sends the update
to the full rebuild with the reason recorded.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import replace
from typing import Any

from ._common import logger

# Above this share of the repository's resolvable files, a patch saves too
# little over the full build to be worth its bookkeeping (measured: at a quarter
# of the files the patch costs about what the full build does). A scope of a
# few files is always cheap, whatever the repository's size.
_MAX_SCOPE_SHARE = 0.2
_SCOPE_FLOOR = 8

# Edges that are not one file resolving another: containment, and git history.
_NON_RESOLUTION_EDGES = frozenset({"defines", "has_method", "co_changes"})

# Spelled by syntax, not by the code that reaches them: a default export is
# reached through its file's importers (``importers_of``), and dunder or
# constructor members through their class name. Matching them as words would
# pull in nearly every file.
_UNSPELLED = frozenset({"default", "constructor"})


def _resolvable(parsed: Any) -> bool:
    """Whether a build has anything of *parsed* to resolve against other files."""
    return any(
        (
            parsed.imports,
            parsed.calls,
            parsed.heritage,
            parsed.type_refs,
            parsed.references,
            parsed.registrars,
            parsed.schema_refs,
        )
    )


def _node_owners(seed_nodes: Iterable[tuple[str, Any]], parsed: Collection[str]) -> dict[str, str]:
    """``{node id: the parsed file that declares it}`` for the seeded nodes."""
    owners: dict[str, str] = {}
    for node_id, attrs in seed_nodes:
        owner = attrs.get("file_path") or node_id
        if owner in parsed:
            owners[node_id] = owner
    return owners


def _dependents_of_vanishing_nodes(
    seed: dict[str, Any],
    owners: dict[str, str],
    graph: Any,
    removed: Collection[str],
    scope: Collection[str],
) -> set[str]:
    """Files with an edge into a node the patch removes."""
    gone = set(removed)
    rescoped = set(scope)
    vanishing = {
        node_id
        for node_id, attrs in seed.get("nodes", ())
        if node_id in gone
        or attrs.get("file_path") in gone
        or (attrs.get("file_path") in rescoped and node_id not in graph)
    }
    if not vanishing:
        return set()
    return {
        owners[source]
        for source, target, attrs in seed.get("edges", ())
        if target in vanishing
        and source in owners
        and attrs.get("edge_type") not in _NON_RESOLUTION_EDGES
    }


def _importers(
    seed: dict[str, Any], owners: dict[str, str], files: Collection[str]
) -> set[str]:
    """Files with an ``imports`` edge into one of *files*."""
    wanted = set(files)
    if not wanted:
        return set()
    return {
        owners[source]
        for source, target, attrs in seed.get("edges", ())
        if target in wanted and source in owners and attrs.get("edge_type") == "imports"
    }


def _resolve_unchanged_imports(path: str, parsed: Any, ctx: Any) -> list[tuple[str, ...]]:
    """Re-resolve *parsed*'s imports without leaving a trace on it."""
    from repowise.core.ingestion.graph._import_targets import resolve_import_targets

    language = parsed.file_info.language
    kept = [(imp.resolved_file, [b.source_file for b in imp.bindings]) for imp in parsed.imports]
    try:
        return [resolve_import_targets(imp, path, language, ctx) for imp in parsed.imports]
    finally:
        for imp, (resolved, sources) in zip(parsed.imports, kept, strict=True):
            imp.resolved_file = resolved
            for binding, source in zip(imp.bindings, sources, strict=True):
                binding.source_file = source


def _rerouted_names(parsed_files: dict[str, Any], imp: Any, targets: Iterable[str]) -> set[str]:
    """What a re-routed import can re-export: its own names, or for ``*`` its targets' names."""
    from .surface_names import _words

    names = set(_words(*imp.imported_names))
    for binding in imp.bindings:
        names.update(_words(binding.local_name, binding.exported_name))
    if names - {"*"} and "*" not in imp.imported_names:
        return names
    for target in targets:
        parsed = parsed_files.get(target)
        if parsed is not None:
            names.update(sym.name for sym in parsed.symbols)
            names.update(parsed.exports)
            # A star import also passes on what the target itself imported.
            for target_import in parsed.imports:
                names.update(_words(*target_import.imported_names))
                for binding in target_import.bindings:
                    names.update(_words(binding.local_name))
    return names


def _rerouted_files(
    graph_builder: Any, scope: set[str], resolutions: dict[str, tuple]
) -> tuple[set[str], set[str]]:
    """Unchanged files whose imports now land elsewhere, and the names they carry.

    Resolves against a copy of the graph, after the same warmups a build
    runs, so nothing here touches the graph the build will patch.
    """
    from repowise.core.ingestion.graph_warmups import run_warmups

    parsed_files = graph_builder._parsed_files
    ctx = graph_builder.resolver_context(graph_builder._graph.copy())
    run_warmups(parsed_files, ctx)
    rerouted: set[str] = set()
    names: set[str] = set()
    for path, parsed in parsed_files.items():
        if path in scope or not parsed.imports:
            continue
        saved = resolutions[path][1]
        fresh = _resolve_unchanged_imports(path, parsed, ctx)
        for imp, now, (_resolved, _sources, before) in zip(parsed.imports, fresh, saved, strict=True):
            if now != before:
                rerouted.add(path)
                names |= _rerouted_names(parsed_files, imp, (*now, *before))
    return rerouted, names


def _with_aliases(names: set[str], parsed_files: dict[str, Any]) -> set[str]:
    """*names* plus every name a re-export or import alias gives one of them."""
    out = set(names)
    pairs: list[tuple[str, str]] = []
    for parsed in parsed_files.values():
        pairs.extend((local, exported) for exported, local in parsed.export_aliases.items())
        for imp in parsed.imports:
            pairs.extend(
                (b.exported_name, b.local_name)
                for b in imp.bindings
                if b.exported_name and b.local_name and b.exported_name != b.local_name
            )
    changed = True
    while changed:
        changed = False
        for source, alias in pairs:
            if source in out and alias not in out:
                out.add(alias)
                changed = True
    return out


def _word_pattern(words: Iterable[str], flags: int = 0) -> re.Pattern[bytes] | None:
    words = sorted({w for w in words if w}, key=len, reverse=True)
    if not words:
        return None
    alternation = b"|".join(re.escape(w.encode()) for w in words)
    return re.compile(rb"(?<![\w$])(?:" + alternation + rb")(?![\w$])", flags)


def _files_spelling(
    parsed_files: dict[str, Any],
    source_map: dict[str, bytes],
    names: set[str],
    stems: Collection[str],
    skip: set[str],
) -> set[str]:
    """Files whose source spells one of *names*, or one of *stems* in any case."""
    exact = _word_pattern(names - _UNSPELLED)
    folded = _word_pattern(stems, re.IGNORECASE)
    if exact is None and folded is None:
        return set()
    return {
        path
        for path, parsed in parsed_files.items()
        if path not in skip
        and _resolvable(parsed)
        and _spells(source_map.get(path), (exact, folded))
    }


def _spells(text: bytes | None, patterns: Iterable[re.Pattern[bytes] | None]) -> bool:
    """Whether *text* matches one of *patterns*; a file with no text is assumed to."""
    if text is None:
        return True
    return any(pattern is not None and pattern.search(text) for pattern in patterns)


def _scope_refusal(scope: set[str], parsed_files: dict[str, Any]) -> str | None:
    """Why re-resolving *scope* cannot stand in for the full build, or ``None``."""
    from .surface import PATCHABLE_LANGUAGES, is_inert

    for path in sorted(scope):
        parsed = parsed_files.get(path)
        if parsed is None or is_inert(parsed):
            continue
        language = parsed.file_info.language
        if language not in PATCHABLE_LANGUAGES and _resolvable(parsed):
            return f"{path}: a {language} file the edit affects is not patched"
    resolvable = sum(1 for parsed in parsed_files.values() if _resolvable(parsed))
    if len(scope) > max(_SCOPE_FLOOR, _MAX_SCOPE_SHARE * resolvable):
        return f"the edit reaches {len(scope)} of {resolvable} files"
    return None


def expand_scope(
    graph_builder: Any,
    plan: Any,
    *,
    seed: dict[str, Any],
    resolutions: dict[str, tuple],
) -> Any:
    """The refreshing *plan* with its scope widened to every file the edit reaches.

    Returns a refusing plan instead when that set includes a file the patch
    cannot re-resolve exactly or is too large to be worth patching.
    *resolutions* must cover every file outside ``plan.scope``.
    """
    from .surface import PatchPlan

    parsed_files = graph_builder._parsed_files
    scope = set(plan.scope)
    owners = _node_owners(seed.get("nodes", ()), parsed_files)
    scope |= _dependents_of_vanishing_nodes(
        seed, owners, graph_builder._graph, plan.removed, plan.scope
    )
    scope |= _importers(seed, owners, plan.importers_of)
    rerouted, carried = _rerouted_files(graph_builder, scope, resolutions)
    scope |= rerouted
    names = _with_aliases(set(plan.names) | carried, parsed_files)
    scope |= _files_spelling(
        parsed_files, graph_builder._source_map, names, plan.stems, scope
    )
    # An unedited file that resolves nothing has no edges to redo.
    scope = {path for path in scope if path in plan.scope or _resolvable(parsed_files[path])}
    reason = _scope_refusal(scope, parsed_files)
    logger.info(
        "graph_patch_scope",
        edited=len(plan.scope),
        rerouted=len(rerouted),
        scope=len(scope),
        refused=reason,
    )
    if reason is not None:
        return PatchPlan(False, reason=reason)
    return replace(plan, scope=tuple(sorted(scope)))
