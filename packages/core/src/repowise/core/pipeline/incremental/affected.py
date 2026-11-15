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
from collections.abc import Callable, Collection, Iterable
from dataclasses import replace
from pathlib import PurePosixPath
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

# Files that read a C/C++ declaration through includes and source text, not
# only by a site's name. Objective-C is not patched: reaching one sends the
# update to the full build.
_C_LANGUAGES = frozenset({"c", "cpp", "objectivec"})
_C_EXTENSIONS = frozenset(
    {".c", ".h", ".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp", ".tcc"}
)

#: How many ``*`` re-export hops a re-routed import's names are followed through.
_MAX_STAR_DEPTH = 16


def changed_field_names(
    parsed_files: dict[str, Any],
    source_map: dict[str, bytes],
    paths: Iterable[str],
    resolutions: dict[str, tuple],
) -> set[str] | None:
    """Names of the class fields whose declared type the edit to *paths* changed.

    Compares each file's fields now with the record the last build saved. A
    file with no record to compare, or no source, makes the answer unknown
    (``None``). Added and removed files count every field they declare. A
    C/C++ file's other source-read facts are compared the same way
    (:mod:`repowise.core.ingestion.cpp_cross_file_facts`), adding the names
    that reach a fact that changed.
    """
    from .resolution_cache import field_types
    from .surface import is_inert

    names: set[str] = set()
    for path in paths:
        parsed = parsed_files.get(path)
        if parsed is not None and is_inert(parsed):
            continue
        entry = resolutions.get(path)
        before = entry[2] if entry is not None else {}
        after = field_types(parsed, source_map.get(path)) if parsed is not None else {}
        if before is None or after is None:
            return None
        for class_id in set(before) | set(after):
            old, new = before.get(class_id, {}), after.get(class_id, {})
            names.update(field for field in set(old) | set(new) if old.get(field, 0) != new.get(field, 0))
        fact_names = _changed_fact_names(path, parsed, entry, source_map)
        if fact_names is None:
            return None
        names |= fact_names
    return names


def _changed_fact_names(
    path: str, parsed: Any | None, entry: tuple | None, source_map: dict[str, bytes]
) -> set[str] | None:
    """Names reaching a C/C++ source-read fact the edit changed; ``None`` when unknown."""
    from .resolution_cache import cpp_facts

    before = entry[3] if entry is not None else frozenset()
    after = cpp_facts(parsed, source_map.get(path)) if parsed is not None else frozenset()
    # A removed file has no parse: its extension says whether it had facts.
    if parsed is not None:
        c_family = parsed.file_info.language in ("c", "cpp")
    else:
        c_family = PurePosixPath(path).suffix.lower() in _C_EXTENSIONS
    if c_family and (before is None or after is None):
        return None
    return {name for name, _fact in (before or frozenset()) ^ (after or frozenset())}


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


def _imported_names_seen(
    seed: dict[str, Any], owners: dict[str, str], import_names: Iterable[tuple[str, Collection[str]]]
) -> set[str]:
    """The names an edited file's own imports touched, when another file imports it.

    A file nobody imports passes on none of its imports.
    """
    names: set[str] = set()
    for path, touched in import_names:
        if _importers(seed, owners, [path]):
            names |= set(touched)
    return names


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


def _rerouted_names(
    parsed_files: dict[str, Any],
    targets_of: Callable[[str], list[tuple[str, ...]]],
    imp: Any,
    targets: Iterable[str],
) -> set[str]:
    """What a re-routed import can re-export: its own names, or for ``*`` its targets' names."""
    from .surface_names import _words

    names = set(_words(*imp.imported_names))
    for binding in imp.bindings:
        names.update(_words(binding.local_name, binding.exported_name))
    if names - {"*"} and "*" not in imp.imported_names:
        return names
    return names | _star_closure_names(parsed_files, targets_of, targets)


def _star_closure_names(
    parsed_files: dict[str, Any],
    targets_of: Callable[[str], list[tuple[str, ...]]],
    targets: Iterable[str],
) -> set[str]:
    """Every name a whole-module import of *targets* can bind, through ``*`` re-exports.

    A target that no longer exists has no parse; every file its last-saved
    imports reached is followed instead, which can only over-collect.
    """
    names: set[str] = set()
    seen: set[str] = set()
    frontier = list(targets)
    for _depth in range(_MAX_STAR_DEPTH):
        if not frontier:
            break
        nxt: list[str] = []
        for path in frontier:
            if path in seen:
                continue
            seen.add(path)
            module_names, onward = _module_names(parsed_files.get(path), targets_of(path))
            names |= module_names
            nxt.extend(onward)
        frontier = nxt
    return names


def _module_names(
    parsed: Any | None, import_targets: list[tuple[str, ...]]
) -> tuple[set[str], list[str]]:
    """A module's bindable names, and the files its ``*`` imports re-export from.

    *import_targets* holds each import's targets; for a deleted module (no
    *parsed*) every one is followed.
    """
    from .surface_names import _words

    if parsed is None:
        return set(), [t for found in import_targets for t in found]
    names = {sym.name for sym in parsed.symbols} | set(parsed.exports)
    onward: list[str] = []
    for imp, found in zip(parsed.imports, import_targets, strict=True):
        names.update(_words(*imp.imported_names))
        names.update(_words(*(binding.local_name for binding in imp.bindings)))
        if not imp.imported_names or "*" in imp.imported_names:
            onward.extend(found)
    return names, onward


def _import_lookup(
    graph_builder: Any, resolutions: dict[str, tuple]
) -> Callable[[str], list[tuple[str, ...]]]:
    """``targets_of(path)``: each import's targets now, or as last saved for a deleted file.

    Resolves against a graph of its own, after the warmup a build runs for
    the file's language, so nothing here touches the graph the build will
    patch. Resolvers only add external nodes to that graph and warmups only
    stamp the nodes it holds, so an empty one gives the same answers as a
    copy. Both are made on first need: a patch that re-resolves no import
    pays for neither.
    """
    import networkx as nx

    from repowise.core.ingestion.graph_warmups import run_warmups

    parsed_files = graph_builder._parsed_files
    contexts: list[Any] = []
    warmed: set[str] = set()
    fresh: dict[str, list[tuple[str, ...]]] = {}

    def targets_of(path: str) -> list[tuple[str, ...]]:
        parsed = parsed_files.get(path)
        if parsed is None:
            return [found for _r, _s, found in resolutions.get(path, (None, []))[1]]
        if path not in fresh:
            if not contexts:
                contexts.append(graph_builder.resolver_context(nx.DiGraph()))
            language = parsed.file_info.language
            if language not in warmed:
                warmed.add(language)
                run_warmups({path: parsed}, contexts[0])
            fresh[path] = _resolve_unchanged_imports(path, parsed, contexts[0])
        return fresh[path]

    return targets_of


def _passed_on_names(
    parsed_files: dict[str, Any],
    resolutions: dict[str, tuple],
    targets_of: Callable[[str], list[tuple[str, ...]]],
    paths: Iterable[str],
) -> set[str]:
    """Names edited files pass on through whole-module imports, before and after.

    The old imports are known only by where they resolved, so every file they
    reached is followed, which can only over-collect.
    """
    reached: list[str] = []
    for path in paths:
        parsed = parsed_files.get(path)
        if parsed is not None:
            reached.extend(_module_names(parsed, targets_of(path))[1])
        reached.extend(t for _r, _s, found in resolutions.get(path, (None, []))[1] for t in found)
    return _star_closure_names(parsed_files, targets_of, reached)


def _may_reroute(
    parsed: Any, moved_stems: set[str | None], only_c_moved: bool, edited_languages: set[str]
) -> bool:
    """Whether an unedited file's imports can land elsewhere (see :func:`_rerouted_files`)."""
    if parsed.file_info.language in ("c", "cpp"):
        return any(PurePosixPath(i.module_path).stem.lower() in moved_stems for i in parsed.imports)
    if not only_c_moved or _family(parsed) in edited_languages:
        return True
    return any(stem and stem in i.module_path.lower() for i in parsed.imports for stem in moved_stems)


def _family(parsed: Any) -> str:
    """The language whose resolver reads *parsed*'s imports; JS and TS share one."""
    language = parsed.file_info.language
    return "typescript" if language == "javascript" else language


def _rerouted_files(
    parsed_files: dict[str, Any],
    targets_of: Callable[[str], list[tuple[str, ...]]],
    edited: Collection[str],
    resolutions: dict[str, tuple],
) -> tuple[set[str], set[str]]:
    """Unedited files whose imports now land elsewhere, and the names they carry.

    A C/C++ include resolves from the file set and the build configuration
    alone, and only ever to a file of its own stem: it reroutes only when a
    file of that stem was added or removed. Another language's import reaches
    a C/C++ file only by spelling its path or stem, so when only C/C++ files
    moved, an import that spells none of their stems cannot reroute.
    """
    from repowise.core.ingestion.graph._stem import stem_key

    moved = {p for p in parsed_files if p not in resolutions} | {p for p in resolutions if p not in parsed_files}
    moved_stems = {stem_key(p) for p in moved}
    only_c_moved = bool(moved) and all(PurePosixPath(p).suffix.lower() in _C_EXTENSIONS for p in moved)
    # An import can also bind differently because a file of its own language
    # changed what it declares, whatever moved.
    edited_languages = {_family(parsed_files[p]) for p in edited if p in parsed_files}
    rerouted: set[str] = set()
    names: set[str] = set()
    for path, parsed in parsed_files.items():
        if path in edited or not parsed.imports:
            continue
        if not _may_reroute(parsed, moved_stems, only_c_moved, edited_languages):
            continue
        saved = resolutions[path][1]
        for imp, now, (_resolved, _sources, before) in zip(
            parsed.imports, targets_of(path), saved, strict=True
        ):
            if now != before:
                rerouted.add(path)
                names |= _rerouted_names(parsed_files, targets_of, imp, (*now, *before))
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


# What may stand on either side of a spelled word for it to be the whole word.
_WORD_BYTES = frozenset(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


def _word_pattern(words: Iterable[str], *, fold: bool = False) -> re.Pattern[bytes] | None:
    """Any of *words*, ending a word; *fold* matches them in lower-cased text.

    The start of the word is checked by :func:`_spells`, not here: a pattern
    that opens on a lookbehind or folds case gets no first-byte skip from the
    regex engine, and these run over every file's source.
    """
    # ``bytes.lower`` folds ASCII only, as a case-insensitive bytes pattern does.
    encoded = sorted({w.encode().lower() if fold else w.encode() for w in words if w}, key=len, reverse=True)
    if not encoded:
        return None
    alternation = b"|".join(map(re.escape, encoded))
    return re.compile(rb"(?:" + alternation + rb")(?![\w$])")


def _files_spelling(
    parsed_files: dict[str, Any],
    source_map: dict[str, bytes],
    names: set[str],
    stems: Collection[str],
    skip: set[str],
) -> set[str]:
    """Files whose source spells one of *names*, or one of *stems* in any case."""
    exact = _word_pattern(names)
    folded = _word_pattern(stems, fold=True)
    if exact is None and folded is None:
        return set()
    return {
        path
        for path, parsed in parsed_files.items()
        if path not in skip
        and _resolvable(parsed)
        and _spells(source_map.get(path), exact, folded)
    }


def _files_naming(parsed_files: dict[str, Any], names: Collection[str]) -> set[str]:
    """Files whose parsed sites (calls, their qualifiers, bases, types) name one of *names*."""
    if not names:
        return set()
    wanted = set(names)
    found: set[str] = set()
    for path, parsed in parsed_files.items():
        spelled = {c.target_name for c in parsed.calls} | {c.scope_name for c in parsed.calls}
        spelled |= {r.parent_name for r in parsed.heritage} | {t.type_name for t in parsed.type_refs}
        if any(word in wanted for value in spelled if value for word in re.findall(r"\w+", value)):
            found.add(path)
    return found


def _spells(
    text: bytes | None, exact: re.Pattern[bytes] | None, folded: re.Pattern[bytes] | None
) -> bool:
    """Whether *text* spells a word of *exact*, or of *folded* in any case.

    A file with no text is assumed to.
    """
    if text is None:
        return True
    if exact is not None and _spells_word(exact, text):
        return True
    return folded is not None and _spells_word(folded, text.lower())


def _spells_word(pattern: re.Pattern[bytes], text: bytes) -> bool:
    """Whether *pattern* matches *text* at a position no word byte precedes."""
    match = pattern.search(text)
    while match is not None:
        start = match.start()
        if start == 0 or text[start - 1] not in _WORD_BYTES:
            return True
        match = pattern.search(text, start + 1)
    return False


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
    targets_of = _import_lookup(graph_builder, resolutions)
    rerouted, carried = _rerouted_files(parsed_files, targets_of, set(plan.scope), resolutions)
    scope |= rerouted
    # What a file passes on through its imports reaches only its importers.
    passing = [p for p in plan.module_imports_changed if _importers(seed, owners, [p])]
    carried |= _passed_on_names(parsed_files, resolutions, targets_of, passing)
    names = set(plan.names) | carried | _imported_names_seen(seed, owners, plan.import_names)
    # A changed default export travels under whatever name a re-export gives
    # it; ``default`` itself is spelled by syntax, not by the code reaching it.
    seeds = names | {"default"} if plan.importers_of else names
    names |= _with_aliases(seeds, parsed_files) - {"default"}
    # A C/C++ declaration reaches another language only through the repo-wide
    # name tiers, which key on what a site names and count a name's symbols:
    # such a file is reached when its parsed sites name a symbol the edit
    # added or removed, not when its text spells a name anywhere.
    include_only = (set(plan.include_only) - carried) & names
    c_files = {p: pf for p, pf in parsed_files.items() if pf.file_info.language in _C_LANGUAGES}
    others = {p: pf for p, pf in parsed_files.items() if p not in c_files}
    source_map = graph_builder._source_map
    scope |= _files_spelling(parsed_files, source_map, names - include_only, plan.stems, scope)
    scope |= _files_spelling(c_files, source_map, include_only, (), scope)
    scope |= _files_naming(others, include_only & plan.cross_names)
    scope |= _files_naming(parsed_files, plan.site_names)
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
