"""Apex's implicit class namespace, and its case-insensitive names.

Apex has no import statement. Every top-level class, interface and enum of a
Salesforce project is visible by name from every other Apex file deployed with
it, and identifiers are case-insensitive: ``accountservice.run()`` calls
``AccountService.Run``. Two facts follow, and this module holds both.

The namespace. One sfdx project (the directory holding ``sfdx-project.json``,
all its package directories together) is one flat namespace; files under no
project, the legacy ``src/`` layout, share one more. A repository of many
projects (LightningFlowComponents ships 192) declares the same class name in
several of them, and a name is looked up in the caller's own project, then in
the un-namespaced packages it declares a dependency on. A namespace prefix
(``npsp.Foo``, ``usf3.Foo``) names the repo project of that namespace; any
other prefix names a managed package's class. A
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
class _Manifest:
    """What one ``sfdx-project.json`` declares."""

    namespace: str = ""  # lowercased; "" when the project has none
    package_dirs: tuple[str, ...] = ()  # repo-relative, no trailing slash
    packages: frozenset[str] = frozenset()  # the packages its directories build
    dependencies: frozenset[str] = frozenset()  # the packages they depend on


@dataclass(frozen=True, slots=True)
class ApexNamespaces:
    """Which project each Apex file belongs to, and the types each project declares."""

    #: Apex file -> its project directory (repo-relative; "" is the repo root).
    project_of: dict[str, str]
    #: project -> what its manifest declares.
    manifests: dict[str, _Manifest]
    #: (project, lowercased name) -> ids of the top-level types declared so.
    top_level: dict[tuple[str, str], tuple[str, ...]]
    #: (outer type id, lowercased name) -> id of the member type declared so.
    members: dict[tuple[str, str], str]
    #: (project, lowercased name) -> ids of the member types declared so, at any depth.
    nested: dict[tuple[str, str], tuple[str, ...]]
    #: project -> the un-namespaced projects building a package it depends on.
    dependencies: dict[str, tuple[str, ...]]
    #: lowercased namespace -> the projects declaring it.
    by_namespace: dict[str, tuple[str, ...]]

    def top_level_type(self, file_path: str, name: str) -> str | None:
        """The one top-level type *name* means from *file_path*, or None.

        Looked up in the caller's own project, else in the un-namespaced
        projects it declares a dependency on (a namespaced one is reached only
        through its prefix). A name declared twice in one project is read in
        the declaration deployed with the caller: the one in the caller's
        package directory, or outside them all for a caller outside them. Any
        other pair is a tie, and neither is chosen.
        """
        project = self.project_of.get(file_path)
        if project is None:
            return None
        if (project, name.lower()) in self.top_level:
            found = self._declared(project, name, self._deploy_unit(project, file_path))
        else:
            found = tuple(
                type_id
                for dependency in self.dependencies.get(project, ())
                for type_id in self._declared(dependency, name)
            )
        return found[0] if len(found) == 1 else None

    def prefixed_type(self, file_path: str, prefix: str, name: str) -> str | None:
        """The type ``prefix.name`` means, *prefix* being the namespace of a repo project."""
        project = self.project_of.get(file_path)
        if project is None or not prefix:
            return None
        if prefix.lower() == self.manifests[project].namespace:
            return self.top_level_type(file_path, name)
        found = tuple(
            type_id
            for other in self.by_namespace.get(prefix.lower(), ())
            for type_id in self._declared(other, name)
        )
        return found[0] if len(found) == 1 else None

    def member_type(self, owner_id: str, name: str) -> str | None:
        return self.members.get((owner_id, name.lower()))

    def nested_type(self, file_path: str, name: str) -> str | None:
        """The one member type *name* the caller's project, else its dependencies, declares.

        For a heritage clause, which keeps only the last segment of ``Outer.Inner``.
        """
        project = self.project_of.get(file_path)
        if project is None:
            return None
        for scope in ([project], self.dependencies.get(project, ())):
            found = {t for p in scope for t in self.nested.get((p, name.lower()), ())}
            if found:
                return found.pop() if len(found) == 1 else None
        return None

    def _declared(self, project: str, name: str, unit: str | None = None) -> tuple[str, ...]:
        """*project*'s top-level types named *name*, narrowed to deploy *unit* when several."""
        found = self.top_level.get((project, name.lower()), ())
        if len(found) > 1:
            found = tuple(
                type_id
                for type_id in found
                if self._deploy_unit(project, symbol_id_path(type_id)) == unit
            )
        return found

    def _deploy_unit(self, project: str, path: str) -> str | None:
        """The package directory *path* is deployed from, or None outside them all."""
        return next(
            (d for d in self.manifests[project].package_dirs if path.startswith(f"{d}/")), None
        )



def build_apex_namespaces(parsed_files: dict[str, Any]) -> ApexNamespaces:
    """Index every parsed Apex file by project. Reads each ``sfdx-project.json`` once."""
    project_of: dict[str, str] = {}
    top_level: dict[tuple[str, str], list[str]] = defaultdict(list)
    members: dict[tuple[str, str], str] = {}
    nested: dict[tuple[str, str], list[str]] = defaultdict(list)
    projects = _ProjectFinder()
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "apex":
            continue
        project = projects.project_of(path, parsed.file_info.abs_path)
        project_of[path] = project
        for symbol in parsed.symbols:
            if symbol.kind not in _TYPE_KINDS:
                continue
            if symbol.parent_name is None:
                top_level[(project, symbol.name.lower())].append(symbol.id)
            else:
                members.setdefault((outer_scope(symbol.id), symbol.name.lower()), symbol.id)
                nested[(project, symbol.name.lower())].append(symbol.id)
    manifests = {p: projects.manifests.get(p, _Manifest()) for p in set(project_of.values())}
    by_namespace: dict[str, list[str]] = defaultdict(list)
    for project, manifest in manifests.items():
        if manifest.namespace:
            by_namespace[manifest.namespace].append(project)
    return ApexNamespaces(
        project_of=project_of,
        manifests=manifests,
        dependencies={p: _dependencies(p, manifests) for p in manifests},
        by_namespace={ns: tuple(found) for ns, found in by_namespace.items()},
        top_level={key: tuple(ids) for key, ids in top_level.items()},
        members=members,
        nested={key: tuple(ids) for key, ids in nested.items()},
    )


class _ProjectFinder:
    """The nearest ``sfdx-project.json`` above a file, each directory asked once."""

    def __init__(self) -> None:
        self._dirs: dict[str, str] = {}
        self.manifests: dict[str, _Manifest] = {}

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
            self.manifests[found] = _read_manifest(manifest, directory)
        elif directory:
            found = self._project_of_dir(PurePosixPath(directory).parent.as_posix(), root)
        else:
            found = ""
        self._dirs[directory] = found
        return found


def _read_manifest(manifest: Path, project: str) -> _Manifest:
    """What an ``sfdx-project.json`` declares; nothing when it cannot be read."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        namespace = data.get("namespace")
        entries = [e for e in data.get("packageDirectories") or [] if isinstance(e, dict)]
        dependencies = [d for e in entries for d in e.get("dependencies") or []]
    except (OSError, ValueError, AttributeError, TypeError):
        return _Manifest()
    return _Manifest(
        namespace=namespace.lower() if isinstance(namespace, str) else "",
        package_dirs=_package_dirs([e.get("path") for e in entries], project),
        packages=_names(e.get("package") for e in entries),
        # ``Base@1.2.0-0`` is a version of the package ``Base``.
        dependencies=_names(
            d.get("package", "").partition("@")[0] for d in dependencies if isinstance(d, dict)
        ),
    )


def _dependencies(project: str, manifests: dict[str, _Manifest]) -> tuple[str, ...]:
    """The un-namespaced projects building a package *project* depends on."""
    wanted = manifests[project].dependencies
    return tuple(
        other
        for other, manifest in manifests.items()
        if other != project and not manifest.namespace and manifest.packages & wanted
    )


def _names(values: Iterable[Any]) -> frozenset[str]:
    return frozenset(value for value in values if isinstance(value, str) and value)


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
