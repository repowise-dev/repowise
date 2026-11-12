"""Decide whether an update can patch the graph instead of rebuilding it.

Resolving one file reads only that file plus, for every *other* file, what it
exposes: its symbols, its heritage and, for languages that re-export through
imports, its imports. If an edit changes none of that, no other file can
resolve differently, and re-resolving just the edited file reproduces a full
rebuild. This module classifies an edit by comparing the file's parse before
and after, taken from the parse cache.

A documentation file can also be added or removed: it gives the graph only
its own file node, so the patch is exact as long as no other file could
resolve to it (:func:`_path_set_change_reason`).

An edit that does change what other files see (a source file added or
removed, a changed surface) is planned as a *refreshing* patch in the
languages of :data:`SURFACE_PATCH_LANGUAGES`: the planner records what the
edit changed (:mod:`.surface_names`) and :mod:`.affected` widens the scope to
every file that could resolve differently.

The rule is deliberately conservative: anything it cannot prove falls back to
the full rebuild.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# Body-only patching is enabled only for languages covered by the resolver
# convergence fixtures. Expand this set together with those fixtures.
PATCHABLE_LANGUAGES = frozenset({"java", "python", "typescript", "javascript", "go", "csharp", "kotlin", "rust", "ruby", "php", "scala", "swift", "c", "cpp"})

# Prose no parser extracts from and no build pass reads. ``.mdx`` is excluded
# because the TypeScript warmup reads its imports from disk. Config and data
# files stay out: resolvers and warmups read manifests by name from disk
# (package.json, tsconfig, pyproject, pubspec, ...). Upgrade path: admit a
# config language once every by-name reader is enumerated here.
_INERT_LANGUAGES = frozenset({"markdown", "asciidoc"})


# Languages whose files may be added, removed or change their surface in a
# patch. Each is covered by the patch-parity fixtures; expand together. C/C++
# resolution also follows includes transitively, which names cannot bound:
# the build checks afterwards that no file's include reach moved
# (:mod:`.include_reach`).
SURFACE_PATCH_LANGUAGES = frozenset({"python", "typescript", "javascript", "c", "cpp"})


@dataclass(frozen=True)
class PatchPlan:
    """Whether to patch, which files to re-resolve, and why not when refusing.

    ``removed`` names deleted files whose seeded nodes the patch must drop.
    ``refresh`` marks an edit that changed what other files can see: the
    build re-runs its whole-graph passes, and the scope must first be widened
    to every file the edit can reach, using ``names`` (identifiers the edit
    added, removed or changed), ``stems`` (lowercased file-name words of added
    or removed files), ``importers_of`` (files whose importers must be
    re-resolved) and ``module_imports_changed`` (edited files whose ``*`` or
    whole-module imports changed, so the names they pass on did).
    ``include_only`` are the names among ``names`` that only C/C++ edits
    touched: a file in another language reaches them only through a parsed
    site that names them (the repo-wide name tiers), not through its text.
    ``site_names`` are C/C++
    namespaces an edit opened or closed, which reach only a C/C++ file whose
    parsed sites name them.
    """

    eligible: bool
    scope: tuple[str, ...] = ()
    reason: str | None = None
    removed: tuple[str, ...] = ()
    refresh: bool = False
    names: frozenset[str] = frozenset()
    stems: frozenset[str] = frozenset()
    importers_of: tuple[str, ...] = ()
    module_imports_changed: tuple[str, ...] = ()
    include_only: frozenset[str] = frozenset()
    site_names: frozenset[str] = frozenset()


def is_inert(parsed: Any) -> bool:
    """Whether *parsed* gives the graph nothing but its own file node."""
    return (
        parsed.file_info.language in _INERT_LANGUAGES
        and not parsed.file_info.path.lower().endswith(".mdx")
        and parse_is_empty(parsed)
    )


def parse_is_empty(parsed: Any) -> bool:
    """Whether *parsed* declares, imports and references nothing."""
    return (
        not parsed.module_name
        and not any(
            (
                parsed.symbols,
                parsed.imports,
                parsed.exports,
                parsed.export_aliases,
                parsed.calls,
                parsed.heritage,
                parsed.type_refs,
                parsed.local_refs,
                parsed.registrars,
                parsed.references,
                parsed.schema_refs,
            )
        )
    )


def _symbol_key(sym: Any) -> tuple:
    return (
        sym.id,
        sym.name,
        sym.qualified_name,
        str(sym.kind),
        sym.signature,
        sym.visibility,
        tuple(sym.decorators),
        tuple(sym.modifiers),
        sym.parent_name,
        sym.is_declaration,
        sym.is_exported_symbol,
        sym.is_async,
        sym.type_parameter_count,
        sym.extension_receiver,
        sym.written_receiver,
        sym.language,
    )


def surface_fingerprint(parsed: Any) -> str:
    """Hash of everything other files can observe about *parsed*.

    Line numbers, docstrings, complexity and call sites are excluded: a body
    edit moves them without changing how any other file resolves.
    """
    h = hashlib.sha256()
    parts: list[Any] = [
        parsed.file_info.language,
        parsed.module_name,
        sorted((_symbol_key(s) for s in parsed.symbols), key=repr),
        sorted(
            ((r.child_name, r.parent_name, str(r.kind)) for r in parsed.heritage),
            key=repr,
        ),
        sorted(parsed.exports),
        sorted(parsed.export_aliases.items()),
        sorted(parsed.registrars),
        sorted(
            (
                (
                    i.module_path,
                    tuple(i.imported_names),
                    i.is_relative,
                    i.is_reexport,
                    i.is_module_declaration,
                    i.type_only,
                    i.deferred,
                    tuple(
                        (
                            b.local_name,
                            b.exported_name,
                            b.is_module_alias,
                            b.is_global,
                            b.is_static_import,
                            b.line,
                            b.column,
                        )
                        for b in i.bindings
                    ),
                )
                for i in parsed.imports
            ),
            key=repr,
        ),
    ]
    h.update(repr(parts).encode())
    return h.hexdigest()


def _path_set_change_reason(
    added: Collection[str],
    removed: Collection[str],
    parsed_files: Iterable[Any],
    previous_paths: Collection[str],
) -> str | None:
    """Why adding or removing these inert files could change another file's graph.

    Resolvers reach a path from an import's text (an exact path) or through
    the stem map. The stem map answers two ways: its best candidate for a
    stem, which import resolvers look up by a key usually but not always
    spelled in the import (Ruby maps ``-`` to ``_``; Go may key on a module
    directory), and a one-file bucket, which some type-reference passes read
    with no import at all. So a moved file is safe when no import's text
    contains its stem and its stem's bucket gives both answers unchanged
    (:func:`_stem_answers`). A ``README.md`` or ``index.md`` may patch
    beside others of its name unless it displaces or reveals a source file
    as the best candidate.
    """
    from repowise.core.ingestion.graph._stem import stem_key

    moved = set(added) | set(removed)
    if not moved:
        return None
    parsed_files = list(parsed_files)
    languages = {pf.file_info.path: pf.file_info.language for pf in parsed_files}
    stems = {stem_key(path) for path in moved}
    before = _stem_buckets(previous_paths, stems)
    after = _stem_buckets(languages, stems)
    # NUL never occurs in a stem, so a match cannot straddle two imports.
    import_text = "\0".join(
        imp.module_path.lower() for pf in parsed_files for imp in pf.imports
    )
    for path in sorted(moved):
        stem = stem_key(path)
        answers = _stem_answers(before.get(stem, []), stem, languages, moved)
        if answers != _stem_answers(after.get(stem, []), stem, languages, moved):
            return f"{path}: changes what its import stem resolves to"
        if stem and stem in import_text:
            return f"{path}: an import may resolve to it"
    return None


def _stem_buckets(paths: Iterable[str], stems: Collection[str | None]) -> dict[str | None, list[str]]:
    """The stem-map buckets of *paths* for just *stems*."""
    from repowise.core.ingestion.graph._stem import stem_key

    buckets: dict[str | None, list[str]] = {}
    for path in paths:
        stem = stem_key(path)
        if stem in stems:
            buckets.setdefault(stem, []).append(path)
    return buckets


def _stem_answers(
    bucket: list[str], stem: str | None, languages: dict[str, str], inert: Collection[str]
) -> tuple[str | None, str | None]:
    """What a stem-map bucket answers: ``(best source candidate, type target)``.

    ``ResolverContext.stem_lookup`` returns the best candidate under the stem
    map's own ordering, prose included; the resolvers whose key the import
    may not spell (Ruby, Luau) keep it only when it is source, so a prose
    best candidate answers as ``None``. The import-free type-reference passes
    (``ingestion.type_ref_resolution``) read a bucket only when it holds one
    path, and accept it only if it is source. A path of unknown language (a
    removed source file) counts as source.

    Ceiling: Go keys a module-root import on the module's directory name and
    keeps any best candidate, so a doc can win it when that directory holds
    no ``.go`` file, which a compiling Go import never names. The original
    unique-stem rule accepts the same case; upgrade by comparing the raw best
    candidate for stems that name a Go module directory.
    """
    from repowise.core.ingestion.graph._stem import _stem_priority

    if not bucket:
        return None, None
    best = min(bucket, key=lambda path: _stem_priority(path, stem or ""))
    if best in inert or languages.get(best) in _INERT_LANGUAGES:
        return None, None
    return best, best if len(bucket) == 1 else None


def _manifest_named_reason(moved: Collection[str], manifest_sources: Mapping[str, bytes]) -> str | None:
    """Refuse a moved doc whose file name a JSON manifest spells.

    JSON manifests map an import onto a path the import never spells
    (package.json ``imports``/``exports``/``main``, tsconfig ``paths``), and
    resolvers keep any file there, prose included. Resolvers probe only
    source extensions, so a manifest reaches a doc only by its full file name.
    YAML and TOML manifests (mkdocs nav, pyproject) map no import to a path.
    """
    if not moved:
        return None
    text = b"\0".join(manifest_sources.values()).decode("utf-8", errors="ignore").lower()
    for path in sorted(moved):
        if path.rsplit("/", 1)[-1].lower() in text:
            return f"{path}: a manifest names it"
    return None


def _stale_seed_reason(added: Collection[str], seed_nodes: Iterable[tuple[str, Any]]) -> str | None:
    """An added path must be new to the seeded graph, or stale attributes merge onto it."""
    if not added:
        return None
    seeded = {node_id for node_id, _attrs in seed_nodes}
    stale = next((path for path in sorted(added) if path in seeded), None)
    return None if stale is None else f"{stale}: already in the graph snapshot"


def _surface_patchable(parsed: Any) -> bool:
    """Whether an added, removed or surface-changed file can be patched around."""
    return parsed.file_info.language in SURFACE_PATCH_LANGUAGES and not getattr(
        parsed, "grammar_missing", False
    )


def _removal_plan(
    path: str, parse_cache: Any, traversed_paths: Collection[str]
) -> tuple[Any | None, str | None]:
    """The removed file's last parse, or why its removal cannot be patched."""
    if path in traversed_paths:
        return None, f"{path}: could not be parsed"
    before = parse_cache.previous_entry(path, exclude_hash="")
    if before is None or not (is_inert(before) or _surface_patchable(before)):
        return None, f"{path}: file removed"
    return before, None


def _edit_plan(
    path: str, parsed: Any, parse_cache: Any, cleared: Collection[str] = ()
) -> tuple[Any | None, bool, str | None]:
    """``(before, surface_changed, refusal)`` for an edited, previously parsed file.

    A path in *cleared* is a config or data file whose edit changed nothing the
    build reads (:mod:`.config_inputs`); with an empty parse on both sides it
    is planned like an inert edit.
    """
    before = parse_cache.previous_entry(path, exclude_hash=parsed.content_hash)
    if before is not None and is_inert(before) and is_inert(parsed):
        return before, False, None
    if path in cleared and before is not None and parse_is_empty(before) and parse_is_empty(parsed):
        return before, False, None
    language = parsed.file_info.language
    if language not in PATCHABLE_LANGUAGES:
        return None, False, f"{path}: {language} edits are not patched"
    if getattr(parsed, "grammar_missing", False):
        return None, False, f"{path}: parser grammar unavailable"
    if before is None:
        return None, False, f"{path}: new file or no earlier parse to compare"
    if surface_fingerprint(before) == surface_fingerprint(parsed):
        return before, False, None
    if not (_surface_patchable(before) and _surface_patchable(parsed)):
        return None, False, f"{path}: its dependency surface changed"
    return before, True, None


@dataclass
class _EditSet:
    """An update's edits, sorted by how the patch must treat them.

    ``surface`` maps each added, removed or surface-changed path to its
    ``(before, after)`` parse, ``None`` on the side where the file did not exist.
    """

    surface: dict[str, tuple[Any | None, Any | None]] = field(default_factory=dict)
    inert_added: list[str] = field(default_factory=list)
    inert_removed: list[str] = field(default_factory=list)


def _classify_removals(
    edits: _EditSet, removed: list[str], parse_cache: Any, traversed_paths: Collection[str]
) -> str | None:
    """File each removed path into *edits*; the reason to refuse, if any."""
    for path in removed:
        before, refusal = _removal_plan(path, parse_cache, traversed_paths)
        if refusal is not None:
            return refusal
        if is_inert(before):
            edits.inert_removed.append(path)
        else:
            edits.surface[path] = (before, None)
    return None


def _classify_changes(
    edits: _EditSet,
    changed: dict[str, Any],
    previous_paths: Collection[str],
    parse_cache: Any,
    cleared: Collection[str] = (),
) -> str | None:
    """File each changed or added path into *edits*; the reason to refuse, if any."""
    for path, parsed in sorted(changed.items()):
        if path in previous_paths:
            before, surface_changed, refusal = _edit_plan(path, parsed, parse_cache, cleared)
            if refusal is not None:
                return refusal
            if surface_changed:
                edits.surface[path] = (before, parsed)
        elif is_inert(parsed):
            edits.inert_added.append(path)
        elif _surface_patchable(parsed):
            edits.surface[path] = (None, parsed)
        else:
            return f"{path}: file added"
    return None


def plan_patch(
    *,
    parse_cache: Any,
    parsed_files: Collection[Any],
    changed: dict[str, Any],
    traversed_paths: Collection[str] = (),
    seed_nodes: Iterable[tuple[str, Any]] = (),
    cleared: Collection[str] = (),
    manifest_sources: Mapping[str, bytes] | None = None,
) -> PatchPlan:
    """Plan a patch for the files whose content changed since the last index.

    *changed* maps each changed path to its fresh :class:`ParsedFile`;
    *parsed_files* is every file parsed in this run and *traversed_paths*
    every file the walk found. The parse cache stands in for the previous
    run's manifest; *seed_nodes* are the snapshot's ``(node_id, attrs)`` rows;
    *manifest_sources* holds the bytes of the parsed JSON manifests.
    *cleared* names edited config and data files proven not to change what
    the build reads.

    An edit that only moved bodies (or documentation) is planned here in full.
    One that adds or removes source files or changes a file's surface comes
    back with ``refresh`` set and the changed files as a starting scope:
    :func:`~.affected.expand_scope` adds every file the edit can reach before
    the patch is accepted.
    """
    if parse_cache is None:
        return PatchPlan(False, reason="no parse cache")
    previous_paths = parse_cache.known_paths()
    current = {pf.file_info.path for pf in parsed_files}
    removed = sorted(previous_paths - current)
    edits = _EditSet()
    refusal = _classify_removals(edits, removed, parse_cache, traversed_paths) or _classify_changes(
        edits, changed, previous_paths, parse_cache, cleared
    )
    if refusal is not None:
        return PatchPlan(False, reason=refusal)
    surface_edits, inert_added, inert_removed = edits.surface, edits.inert_added, edits.inert_removed
    seed_nodes = list(seed_nodes)
    added = inert_added + [p for p, (before, _) in surface_edits.items() if before is None]
    reason = (
        _path_set_change_reason(inert_added, inert_removed, parsed_files, previous_paths)
        or _manifest_named_reason(inert_added + inert_removed, manifest_sources or {})
        or _stale_seed_reason(added, seed_nodes)
    )
    if reason is not None:
        return PatchPlan(False, reason=reason)
    scope = tuple(sorted(changed))
    if not surface_edits:
        return PatchPlan(True, scope=scope, removed=tuple(removed))
    from .surface_names import path_set_reason, surface_keys

    reason = path_set_reason(previous_paths, parsed_files, surface_edits)
    if reason is not None:
        return PatchPlan(False, reason=reason)
    keys = surface_keys(surface_edits, parsed_files)
    return PatchPlan(
        True,
        scope=scope,
        removed=tuple(removed),
        refresh=True,
        names=keys.names,
        stems=keys.stems,
        importers_of=keys.importers_of,
        module_imports_changed=keys.module_imports_changed,
        include_only=keys.include_only,
        site_names=keys.site_names,
    )
