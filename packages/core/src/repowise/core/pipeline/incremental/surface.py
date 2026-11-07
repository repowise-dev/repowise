"""Decide whether an update can patch the graph instead of rebuilding it.

Resolving one file reads only that file plus, for every *other* file, what it
exposes: its symbols, its heritage and, for languages that re-export through
imports, its imports. If an edit changes none of that, no other file can
resolve differently, and re-resolving just the edited file reproduces a full
rebuild. This module classifies an edit by comparing the file's parse before
and after, taken from the parse cache.

A documentation file is the one kind of file that can also be added or
removed: it gives the graph only its own file node, so the patch is exact as
long as no other file could resolve to it (:func:`_path_set_change_reason`).

The rule is deliberately conservative: anything it cannot prove falls back to
the full rebuild.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any

# Body-only patching is enabled only for languages covered by the resolver
# convergence fixtures. Expand this set together with those fixtures.
PATCHABLE_LANGUAGES = frozenset({"java", "python", "typescript", "javascript", "go", "csharp", "kotlin", "rust", "ruby", "php", "scala", "swift"})

# Prose no parser extracts from and no build pass reads. ``.mdx`` is excluded
# because the TypeScript warmup reads its imports from disk. Config and data
# files stay out: resolvers and warmups read manifests by name from disk
# (package.json, tsconfig, pyproject, pubspec, ...). Upgrade path: admit a
# config language once every by-name reader is enumerated here.
_INERT_LANGUAGES = frozenset({"markdown", "asciidoc"})


@dataclass(frozen=True)
class PatchPlan:
    """Whether to patch, which files to re-resolve, and why not when refusing.

    ``removed`` names deleted files whose seeded nodes the patch must drop.
    """

    eligible: bool
    scope: tuple[str, ...] = ()
    reason: str | None = None
    removed: tuple[str, ...] = ()


def is_inert(parsed: Any) -> bool:
    """Whether *parsed* gives the graph nothing but its own file node."""
    return (
        parsed.file_info.language in _INERT_LANGUAGES
        and not parsed.file_info.path.lower().endswith(".mdx")
        and not parsed.module_name
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
    seed_nodes: Iterable[tuple[str, Any]],
) -> str | None:
    """Why adding or removing these inert files could change another file's graph.

    Resolvers reach a path two ways: from an import's text (an exact path, or
    the stem map keyed by its last segment) and from the stem map's bucket
    sizes, which some type-reference passes read with no import at all. So a
    moved file is safe when no other file shares its stem and no import's text
    contains its stem. An added path must also be new to the seeded graph, so
    no stale node attribute is merged onto it.
    """
    from repowise.core.ingestion.graph._stem import stem_key

    moved = set(added) | set(removed)
    if not moved:
        return None
    parsed_files = list(parsed_files)
    others = ({pf.file_info.path for pf in parsed_files} | set(previous_paths)) - moved
    other_stems = {stem_key(path) for path in others}
    # NUL never occurs in a stem, so a match cannot straddle two imports.
    import_text = "\0".join(
        imp.module_path.lower() for pf in parsed_files for imp in pf.imports
    )
    for path in sorted(moved):
        stem = stem_key(path)
        if stem in other_stems:
            return f"{path}: another file shares its import stem"
        if stem and stem in import_text:
            return f"{path}: an import may resolve to it"
    if added:
        seeded = {node_id for node_id, _attrs in seed_nodes}
        stale = next((path for path in sorted(added) if path in seeded), None)
        if stale is not None:
            return f"{stale}: already in the graph snapshot"
    return None


def plan_patch(
    *,
    parse_cache: Any,
    parsed_files: Collection[Any],
    changed: dict[str, Any],
    traversed_paths: Collection[str] = (),
    seed_nodes: Iterable[tuple[str, Any]] = (),
) -> PatchPlan:
    """Plan a patch for the files whose content changed since the last index.

    *changed* maps each changed path to its fresh :class:`ParsedFile`;
    *parsed_files* is every file parsed in this run and *traversed_paths*
    every file the walk found. The parse cache stands in for the previous
    run's manifest; *seed_nodes* are the snapshot's ``(node_id, attrs)`` rows.
    """
    if parse_cache is None:
        return PatchPlan(False, reason="no parse cache")
    previous_paths = parse_cache.known_paths()
    current = {pf.file_info.path for pf in parsed_files}
    removed = sorted(previous_paths - current)
    for path in removed:
        if path in traversed_paths:
            return PatchPlan(False, reason=f"{path}: could not be parsed")
        before = parse_cache.previous_entry(path, exclude_hash="")
        if before is None or not is_inert(before):
            return PatchPlan(False, reason=f"{path}: file removed")
    added: list[str] = []
    for path, parsed in sorted(changed.items()):
        if path not in previous_paths:
            if not is_inert(parsed):
                return PatchPlan(False, reason=f"{path}: file added")
            added.append(path)
            continue
        before = parse_cache.previous_entry(path, exclude_hash=parsed.content_hash)
        if before is not None and is_inert(before) and is_inert(parsed):
            continue
        language = parsed.file_info.language
        if language not in PATCHABLE_LANGUAGES:
            return PatchPlan(False, reason=f"{path}: {language} edits are not patched")
        if getattr(parsed, "grammar_missing", False):
            return PatchPlan(False, reason=f"{path}: parser grammar unavailable")
        if before is None:
            return PatchPlan(False, reason=f"{path}: new file or no earlier parse to compare")
        if surface_fingerprint(before) != surface_fingerprint(parsed):
            return PatchPlan(False, reason=f"{path}: its dependency surface changed")
    reason = _path_set_change_reason(added, removed, parsed_files, previous_paths, seed_nodes)
    if reason is not None:
        return PatchPlan(False, reason=reason)
    return PatchPlan(True, scope=tuple(sorted(changed)), removed=tuple(removed))
