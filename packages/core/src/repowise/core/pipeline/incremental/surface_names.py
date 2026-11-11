"""What an edit that changes other files' view of the repo changed, by name.

A resolver binds a site in one file to a definition in another by the names
the site spells: an imported name, a called function, a base class, a type.
So when an edit adds, removes or changes what a file declares, the only
unchanged files that can resolve differently are those that spell one of the
identifiers the edit touched, plus the files whose imports now land elsewhere
(found by re-resolving them, see :mod:`.affected`). This module turns the
before/after parses of the edited files into those identifiers.

It also refuses path-set changes that re-key every file at once (a new
language, a flipped resolver switch, a moved Python package root), which no
scope smaller than the whole repo can absorb.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

_WORD = re.compile(r"[A-Za-z_$][\w$]*")

# Members no call site spells: constructors run through the class name. A
# change to one is a change to its class, so the class name stands for it.
_CONSTRUCTOR_NAMES = frozenset({"__init__", "__new__", "__post_init__"})
_JS_CONSTRUCTOR_LANGUAGES = frozenset({"typescript", "javascript"})

#: How the class-member expansion follows ``extends`` chains, at most.
_MAX_ANCESTOR_DEPTH = 32


@dataclass(frozen=True)
class SurfaceKeys:
    """The identifiers an edit touched, as :class:`~.surface.PatchPlan` carries them."""

    names: frozenset[str]
    stems: frozenset[str]
    importers_of: tuple[str, ...]
    module_imports_changed: tuple[str, ...] = ()


def _words(*values: Any) -> Iterator[str]:
    for value in values:
        if isinstance(value, str) and value:
            yield from _WORD.findall(value)


def _name_facts(parsed: Any) -> set[tuple]:
    """Everything another file can bind to by name in *parsed*, one hashable fact each."""
    from .surface import _symbol_key

    facts: set[tuple] = {("symbol", _symbol_key(sym)) for sym in parsed.symbols}
    facts.update(("export", name) for name in parsed.exports)
    facts.update(("alias", key, value) for key, value in parsed.export_aliases.items())
    facts.update(("registrar", name) for name in parsed.registrars)
    facts.update(("heritage", rel.child_name, rel.parent_name) for rel in parsed.heritage)
    facts.update(("module", parsed.module_name) for _ in (0,) if parsed.module_name)
    for imp in parsed.imports:
        facts.add(
            (
                "import",
                imp.module_path,
                tuple(imp.imported_names),
                tuple((b.local_name, b.exported_name) for b in imp.bindings),
            )
        )
    return facts


def _unspelled(name: str, language: str | None = None) -> bool:
    """A member no call site names: a constructor or another dunder."""
    if name == "constructor":
        return language in _JS_CONSTRUCTOR_LANGUAGES
    return name in _CONSTRUCTOR_NAMES or (name.startswith("__") and name.endswith("__"))


def _fact_names(fact: tuple) -> tuple[set[str], bool]:
    """``(identifiers, reached_through_importers)`` for one changed fact.

    A constructor change is spelled by its class's name. A default export is
    spelled by no one: it is reached only through an import of its file, so
    the file's importers stand in for it (the name ``default`` itself is then
    left out). A module-level dunder may be reached either way.
    """
    kind = fact[0]
    if kind == "symbol":
        key = fact[1]
        name, parent, language = key[1], key[8], key[15]
        if parent and _unspelled(name, language):
            return set(_words(parent)), False
        return set(_words(name, parent)), _unspelled(name, language)
    if kind == "import":
        names = set(_words(*fact[2]))
        for local, exported in fact[3]:
            names.update(_words(local, exported))
        return names, False
    names = set(_words(*fact[1:]))
    return names - {"default"}, "default" in names


def _class_index(parses: Iterable[Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``({class name: member names}, {class name: base-class words})`` over *parses*."""
    members: dict[str, set[str]] = defaultdict(set)
    bases: dict[str, set[str]] = defaultdict(set)
    for parsed in parses:
        for sym in parsed.symbols:
            if sym.parent_name and not _unspelled(sym.name, sym.language):
                members[sym.parent_name.rsplit(".", 1)[-1]].add(sym.name)
        for rel in parsed.heritage:
            bases[rel.child_name.rsplit(".", 1)[-1]].update(_words(rel.parent_name))
    return members, bases


def _with_inherited_members(names: set[str], parses: Iterable[Any]) -> set[str]:
    """*names* plus the members of every class they name and of its ancestors.

    A call ``obj.m()`` on an instance whose class gained, lost or re-parented
    an ancestor spells ``m``, not the class: so a touched class name brings in
    every member name its old and new hierarchy can supply.
    """
    members, bases = _class_index(parses)
    out = set(names)
    frontier = [name for name in names if name in members or name in bases]
    seen: set[str] = set()
    depth = 0
    while frontier and depth < _MAX_ANCESTOR_DEPTH:
        depth += 1
        nxt: list[str] = []
        for cls in frontier:
            if cls in seen:
                continue
            seen.add(cls)
            out.update(members.get(cls, ()))
            nxt.extend(base for base in bases.get(cls, ()) if base not in seen)
        frontier = nxt
    return out


def _stem_words(path: str) -> set[str]:
    """Lowercased words of *path*'s file name, plus the package a dunder-init names."""
    p = PurePosixPath(path)
    words = {part.lower() for part in p.name.split(".")[:-1] if part}
    if p.stem in ("__init__", "index"):
        words.add(p.parent.name.lower())
    return {w for w in words if w}


def _is_module_import(fact: tuple) -> bool:
    """An import fact that binds a whole module (``*``, or no names at all)."""
    return fact[0] == "import" and (not fact[2] or "*" in fact[2])


def _edit_names(before: Any | None, after: Any | None) -> tuple[set[str], bool, bool]:
    """``(identifiers, via_importers, module_import_changed)`` for one file's edit.

    *via_importers*: the file's importers stand in for some names.
    *module_import_changed*: a whole-module import changed, so what the file
    passes on is the name set of the modules it reaches (``affected``).
    """
    old = _name_facts(before) if before is not None else set()
    new = _name_facts(after) if after is not None else set()
    moved = before is None or after is None
    names: set[str] = set()
    via_importers = False
    module_import_changed = False
    for fact in old ^ new:
        if moved and fact[0] == "import":
            # Another file sees an added or removed file's imports only by
            # importing that file, and that import re-routes (``affected``).
            continue
        module_import_changed = module_import_changed or _is_module_import(fact)
        fact_names, through_importers = _fact_names(fact)
        names |= fact_names
        via_importers = via_importers or through_importers
    return names, via_importers, module_import_changed


def surface_keys(
    surface_edits: dict[str, tuple[Any | None, Any | None]], parsed_files: Collection[Any]
) -> SurfaceKeys:
    """The identifiers, file-name words and importer sets an edit touched.

    *surface_edits* maps each added, removed or surface-changed path to its
    ``(before, after)`` parse, ``None`` where the file did not exist.
    """
    names: set[str] = set()
    stems: set[str] = set()
    importers_of: set[str] = set()
    module_imports_changed: set[str] = set()
    for path, (before, after) in surface_edits.items():
        edit_names, via_importers, module_import_changed = _edit_names(before, after)
        names |= edit_names
        if via_importers:
            importers_of.add(path)
        if module_import_changed:
            module_imports_changed.add(path)
        if before is None or after is None:
            stems.update(_stem_words(path))
    parses = [*parsed_files, *(before for before, _ in surface_edits.values() if before)]
    return SurfaceKeys(
        names=frozenset(_with_inherited_members(names, parses)),
        stems=frozenset(stems),
        importers_of=tuple(sorted(importers_of)),
        module_imports_changed=tuple(sorted(module_imports_changed)),
    )


def path_set_reason(
    previous_paths: Collection[str],
    parsed_files: Collection[Any],
    surface_edits: dict[str, tuple[Any | None, Any | None]],
) -> str | None:
    """Why adding or removing these files re-keys files no name points at, or ``None``.

    The resolver context switches on whole-repo facts (which languages are
    present, whether single-file components or platform-suffixed files
    exist), and Python symbols are qualified by a module path that depends on
    which ``__init__.py`` files exist. Any of those moving changes files the
    edit never names.
    """
    current = frozenset(pf.file_info.path for pf in parsed_files)
    previous = frozenset(previous_paths)
    if current == previous:
        return None
    added = {path for path, (before, _) in surface_edits.items() if before is None}
    return (
        _resolver_switch_reason(current, previous)
        or _language_set_reason(parsed_files, surface_edits, added)
        or _python_module_reason(parsed_files, added, current, previous)
    )


def _resolver_switch_reason(current: frozenset[str], previous: frozenset[str]) -> str | None:
    from repowise.core.ingestion.resolvers.typescript import detect_platform_files

    def has_sfc(paths: frozenset[str]) -> bool:
        return any(p.endswith((".vue", ".svelte", ".astro")) for p in paths)

    if has_sfc(current) != has_sfc(previous):
        return "a single-file component appeared or disappeared"
    if detect_platform_files(current) != detect_platform_files(previous):
        return "platform-specific files appeared or disappeared"
    return None


def _language_set_reason(
    parsed_files: Collection[Any],
    surface_edits: dict[str, tuple[Any | None, Any | None]],
    added: set[str],
) -> str | None:
    now = {pf.file_info.language for pf in parsed_files}
    then = {pf.file_info.language for pf in parsed_files if pf.file_info.path not in added}
    then.update(before.file_info.language for before, after in surface_edits.values() if after is None)
    return None if now == then else "a language appeared in or left the repository"


def _python_module_reason(
    parsed_files: Collection[Any], added: set[str], current: frozenset[str], previous: frozenset[str]
) -> str | None:
    from repowise.core.ingestion.languages.python_modules import dotted_module_for

    for pf in parsed_files:
        path = pf.file_info.path
        if pf.file_info.language != "python" or path in added:
            continue
        if dotted_module_for(path, current) != dotted_module_for(path, previous):
            return f"{path}: its Python module path changed"
    return None
