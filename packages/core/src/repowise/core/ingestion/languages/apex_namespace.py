"""Apex's implicit class namespace, and its case-insensitive names.

Apex has no import statement. Every top-level class, interface and enum of a
Salesforce project is visible by name from every other Apex file deployed with
it, and identifiers are case-insensitive: ``accountservice.run()`` calls
``AccountService.Run``. Two facts follow, and this module holds both.

The namespace. One sfdx project (the directory holding ``sfdx-project.json``,
all its package directories together) is one flat namespace; files under no
project, the legacy ``src/`` layout, share one more. A repository of many
projects (LightningFlowComponents ships 192) declares the same class name in
several of them, and a name is only ever looked up in the caller's own project.
The project's ``namespace`` is the prefix the code may write before its own
classes (``npsp.Foo``); any other prefix names a managed package's class. A
project may also keep a second copy of its classes outside its package
directories (a legacy ``mdapi/`` export); a name declared in both is read in the
copy the caller is deployed with.

The spelling. Every resolver index is keyed on the declared name, so the one
seam that folds case is :func:`spell_apex_names`, run once before the graph is
resolved: it rewrites the names Apex call sites and heritage clauses wrote to
the spelling the repository declares. Declarations keep their own spelling.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ..symbol_identity import outer_scope, symbol_id_path

_SFDX_PROJECT = "sfdx-project.json"
_TYPE_KINDS = frozenset({"class", "interface", "enum"})


@dataclass(frozen=True, slots=True)
class ApexNamespaces:
    """Which project each Apex file belongs to, and the types each project declares."""

    #: Apex file -> its project directory (repo-relative; "" is the repo root).
    project_of: dict[str, str]
    #: project -> its own namespace prefixes, lowercased.
    own_prefixes: dict[str, frozenset[str]]
    #: project -> its package directories (repo-relative, no trailing slash).
    package_dirs: dict[str, tuple[str, ...]]
    #: (project, lowercased name) -> ids of the top-level types declared so.
    top_level: dict[tuple[str, str], tuple[str, ...]]
    #: (outer type id, lowercased name) -> id of the member type declared so.
    members: dict[tuple[str, str], str]

    def top_level_type(self, file_path: str, name: str) -> str | None:
        """The one top-level type *name* means from *file_path*'s project, or None.

        A name declared twice in one project is read in the declaration
        deployed with the caller: the one in the caller's package directory,
        or, for a caller outside every package directory, the one outside them
        too. Any other pair is a tie nothing breaks, and neither is chosen.
        """
        project = self.project_of.get(file_path)
        if project is None:
            return None
        found = self.top_level.get((project, name.lower()), ())
        if len(found) > 1:
            unit = self._deploy_unit(project, file_path)
            found = tuple(
                type_id
                for type_id in found
                if self._deploy_unit(project, symbol_id_path(type_id)) == unit
            )
        return found[0] if len(found) == 1 else None

    def _deploy_unit(self, project: str, path: str) -> str | None:
        """The package directory *path* is deployed from, or None outside them all."""
        return next(
            (d for d in self.package_dirs.get(project, ()) if path.startswith(f"{d}/")), None
        )

    def member_type(self, owner_id: str, name: str) -> str | None:
        return self.members.get((owner_id, name.lower()))

    def strips_prefix(self, file_path: str, segment: str) -> bool:
        """Is *segment* the caller's own project namespace, written before one of its classes?"""
        project = self.project_of.get(file_path)
        return project is not None and segment.lower() in self.own_prefixes.get(project, ())


def build_apex_namespaces(parsed_files: dict[str, Any]) -> ApexNamespaces:
    """Index every parsed Apex file by project. Reads each ``sfdx-project.json`` once."""
    project_of: dict[str, str] = {}
    prefixes: dict[str, frozenset[str]] = {}
    package_dirs: dict[str, tuple[str, ...]] = {}
    top_level: dict[tuple[str, str], list[str]] = defaultdict(list)
    members: dict[tuple[str, str], str] = {}
    projects = _ProjectFinder()
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "apex":
            continue
        project = projects.project_of(path, parsed.file_info.abs_path)
        project_of[path] = project
        prefixes.setdefault(project, projects.prefixes.get(project, frozenset()))
        package_dirs.setdefault(project, projects.package_dirs.get(project, ()))
        for symbol in parsed.symbols:
            if symbol.kind not in _TYPE_KINDS:
                continue
            if symbol.parent_name is None:
                top_level[(project, symbol.name.lower())].append(symbol.id)
            else:
                members.setdefault((outer_scope(symbol.id), symbol.name.lower()), symbol.id)
    return ApexNamespaces(
        project_of=project_of,
        own_prefixes=prefixes,
        package_dirs=package_dirs,
        top_level={key: tuple(ids) for key, ids in top_level.items()},
        members=members,
    )


class _ProjectFinder:
    """The nearest ``sfdx-project.json`` above a file, each directory asked once."""

    def __init__(self) -> None:
        self._dirs: dict[str, str] = {}
        self.prefixes: dict[str, frozenset[str]] = {}
        self.package_dirs: dict[str, tuple[str, ...]] = {}

    def project_of(self, path: str, abs_path: str) -> str:
        rel = PurePosixPath(path)
        root = Path(abs_path)
        for _ in rel.parts:
            root = root.parent
        return self._project_of_dir(rel.parent.as_posix(), root)

    def _project_of_dir(self, directory: str, root: Path) -> str:
        directory = "" if directory == "." else directory
        found = self._dirs.get(directory)
        if found is not None:
            return found
        manifest = root / directory / _SFDX_PROJECT
        if manifest.is_file():
            found = directory
            self.prefixes[found], self.package_dirs[found] = _read_manifest(manifest, directory)
        elif directory:
            found = self._project_of_dir(PurePosixPath(directory).parent.as_posix(), root)
        else:
            found = ""
        self._dirs[directory] = found
        return found


def _read_manifest(manifest: Path, project: str) -> tuple[frozenset[str], tuple[str, ...]]:
    """``(own namespace prefixes, package directories)`` an ``sfdx-project.json`` declares."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        namespace = data.get("namespace")
        entries = data.get("packageDirectories") or []
        written = [e.get("path") for e in entries if isinstance(e, dict)]
    except (OSError, ValueError, AttributeError, TypeError):
        return frozenset(), ()
    prefixes = frozenset({namespace.lower()}) if isinstance(namespace, str) and namespace else frozenset()
    return prefixes, _package_dirs(written, project)


def _package_dirs(written: list[Any], project: str) -> tuple[str, ...]:
    """The written package directories, repo-relative.

    Ceiling: one outside the project (``../shared``) is not read.
    """
    found = []
    for path in written:
        if not isinstance(path, str):
            continue
        path = PurePosixPath(path.replace("\\", "/")).as_posix().removeprefix("./").rstrip("/")
        if path not in ("", ".") and not path.startswith(".."):
            found.append(PurePosixPath(project, path).as_posix())
    return tuple(found)


def spell_apex_names(parsed_files: Iterable[Any]) -> None:
    """Rewrite the names Apex call sites and heritage clauses wrote to their declared spelling.

    A written name the repository declares as written is kept. Otherwise its
    case-folded form must match exactly one declared spelling; a name declared
    in two spellings (``getRecordTypeId`` and ``GetRecordTypeId``) is left as
    written, a missed edge rather than a guessed one. A call's receiver is left
    alone: it may be a variable that shares a class's folded name, which the
    resolver tells apart. Idempotent, so a cached parse may pass through twice.

    A receiver-less call means its own file's declaration of the name first
    (of the same kind), however another class spells it: ``ContactFromDi(..)``
    in a class declaring ``contactFromDi`` is that method. A call site does not
    record ``new``, so a capitalised receiver-less name some type folds to is a
    constructor (``new OrgConfig()`` inside a method ``orgConfig``), and any
    other capitalised one matches only its own file's member (``new Contact()``,
    the sObject, never becomes another class's method ``contact``). Ceilings: a
    file declaring ``contact()`` reads its own ``new Contact()`` as that method,
    and a constructor written in lower case (``new accountService()``) misses.
    """
    apex = [parsed for parsed in parsed_files if parsed.file_info.language == "apex"]
    repo = _Spellings(symbol for parsed in apex for symbol in parsed.symbols)
    for parsed in apex:
        own = _Spellings(parsed.symbols)
        for index, call in enumerate(parsed.calls):
            target = repo.call_target(call, own)
            if target != call.target_name:
                parsed.calls[index] = replace(call, target_name=target)
        for relation in parsed.heritage:
            relation.parent_name = repo.spelled(relation.parent_name, repo.types)


class _Spellings:
    """``{folded name: declared spellings}`` for a set of Apex symbols, by kind."""

    def __init__(self, symbols: Iterable[Any]) -> None:
        self.names: dict[str, set[str]] = defaultdict(set)
        self.types: dict[str, set[str]] = defaultdict(set)
        self.members: dict[str, set[str]] = defaultdict(set)
        for symbol in symbols:
            self.names[symbol.name.lower()].add(symbol.name)
            kind = self.types if symbol.kind in _TYPE_KINDS else self.members
            kind[symbol.name.lower()].add(symbol.name)

    def spelled(self, name: str, declared: dict[str, set[str]]) -> str:
        """*name* as *declared* spells it, when that is one spelling and *name* is none."""
        spellings = declared.get(name.lower())
        if not spellings or name in self.names.get(name.lower(), ()) or len(spellings) > 1:
            return name
        return next(iter(spellings))

    def call_target(self, call: Any, own: _Spellings) -> str:
        """The spelling a call's target means; *own* is the caller file's."""
        name = call.target_name
        if call.receiver_name or call.receiver_call:
            return self.spelled(name, self.names)
        folded = name.lower()
        if not name[:1].isupper():
            table, mine = self.members, own.members
        elif folded in self.types:
            table, mine = self.types, own.types
        else:
            return own.spelled(name, own.members)
        return own.spelled(name, mine) if folded in mine else self.spelled(name, table)
