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
other prefix names a managed package's class. Markup and metadata (LWC, Aura,
Visualforce, flows) reach Apex by the same rules from the project they sit in,
then any class exactly one un-namespaced project declares: they name action
classes of packages installed separately in the same org. A
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
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from ..symbol_identity import outer_scope, symbol_id_path
from ..type_names import bare_type_name, dotted_type_segments, type_qualifier

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
    #: project -> the un-namespaced projects building a package it depends on.
    dependencies: dict[str, tuple[str, ...]]
    #: lowercased namespace -> the projects declaring it.
    by_namespace: dict[str, tuple[str, ...]]
    #: lowercased top-level type name -> the projects declaring it.
    declarers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Places files the index was not built over (markup, metadata).
    finder: _ProjectFinder = field(default_factory=lambda: _ProjectFinder(), compare=False)
    #: Non-Apex file -> its project, as placed so far.
    placed: dict[str, str] = field(default_factory=dict, compare=False)

    def top_level_type(self, file_path: str, name: str) -> str | None:
        """The one top-level type *name* means from Apex file *file_path*, or None."""
        project = self.project_of.get(file_path)
        return None if project is None else self._top_level(project, file_path, name)

    def referred_file(self, path: str, written: str) -> str | None:
        """The file of the top-level type ``[ns.]Name`` means from any repo file.

        *path* belongs to the project whose ``sfdx-project.json`` is nearest
        above it; ``c.`` names that project, like no prefix. A non-Apex file
        reaches what an Apex file there would, else a name its project does not
        declare and exactly one un-namespaced project does.
        """
        project = self._place(path)
        prefix, name = type_qualifier(written), bare_type_name(written)
        if project is None or not name:
            return None
        if prefix and prefix.lower() != "c":
            found = self._prefixed(project, path, prefix, name)
        else:
            found = self._top_level(project, path, name)
            if found is None and path not in self.project_of:
                found = self._unique_elsewhere(project, name)
        return symbol_id_path(found) if found else None

    def _unique_elsewhere(self, project: str, name: str) -> str | None:
        """The one type *name* names when one other, un-namespaced project declares it once."""
        declarers = self.declarers.get(name.lower(), ())
        if len(declarers) != 1 or declarers[0] == project:
            return None
        if self.manifests[declarers[0]].namespace:
            return None
        found = self._declared(declarers[0], name)
        return found[0] if len(found) == 1 else None

    def _top_level(self, project: str, file_path: str, name: str) -> str | None:
        """The one top-level type *name* means from *file_path* in *project*, or None.

        Looked up in the caller's own project, else in the un-namespaced
        projects it declares a dependency on (a namespaced one is reached only
        through its prefix). A name declared twice in one project is read in
        the declaration deployed with the caller: the one in the caller's
        package directory, or outside them all for a caller outside them. Any
        other pair is a tie, and neither is chosen.
        """
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
        return self._prefixed(project, file_path, prefix, name)

    def _prefixed(self, project: str, file_path: str, prefix: str, name: str) -> str | None:
        if prefix.lower() == self.manifests[project].namespace:
            found = self._declared(project, name, self._deploy_unit(project, file_path))
            return found[0] if len(found) == 1 else None
        found = tuple(
            type_id
            for other in self.by_namespace.get(prefix.lower(), ())
            for type_id in self._declared(other, name)
        )
        return found[0] if len(found) == 1 else None

    def member_type(self, owner_id: str, name: str) -> str | None:
        return self.members.get((owner_id, name.lower()))

    def type_path(self, file_path: str, written: str) -> str | None:
        """The type a written path (``Outer.Inner``, ``usf3.Outer.Inner``) names from *file_path*.

        The head is a top-level type the caller reaches, else a namespace
        prefix; each later segment is a member type of the one before. An
        outer type that is unknown or external names nothing.
        """
        head, *rest = dotted_type_segments(written)
        type_id = self.top_level_type(file_path, head)
        if type_id is None and rest:
            prefix, (head, *rest) = head, rest
            type_id = self.prefixed_type(file_path, prefix, head)
        for segment in rest:
            if type_id is None:
                return None
            type_id = self.member_type(type_id, segment)
        return type_id

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

    def _place(self, path: str) -> str | None:
        """*path*'s project, registering a project that holds no Apex; None with no Apex at all."""
        project = self.project_of.get(path, self.placed.get(path))
        if project is not None or self.finder.root is None:
            return project
        project = self.finder.project_of(path, str(self.finder.root / path))
        self.placed[path] = project
        if project not in self.manifests:
            self.manifests[project] = self.finder.manifests.get(project, _Manifest())
            self.dependencies[project] = _dependencies(project, self.manifests)
        return project



def build_apex_namespaces(parsed_files: dict[str, Any]) -> ApexNamespaces:
    """Index every parsed Apex file by project. Reads each ``sfdx-project.json`` once."""
    project_of: dict[str, str] = {}
    top_level: dict[tuple[str, str], list[str]] = defaultdict(list)
    members: dict[tuple[str, str], str] = {}
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
        declarers=_declarers(top_level),
        finder=projects,
    )


def apex_namespaces(ctx: Any) -> ApexNamespaces:
    """The index for a resolver context's parsed files, built once per context."""
    index = getattr(ctx, "_apex_namespaces", None)
    if index is None:
        index = build_apex_namespaces(ctx.parsed_files or {})
        ctx._apex_namespaces = index
    return index


class _ProjectFinder:
    """The nearest ``sfdx-project.json`` above a file, each directory asked once."""

    def __init__(self) -> None:
        self._dirs: dict[str, str] = {}
        self.manifests: dict[str, _Manifest] = {}
        self.root: Path | None = None  # the repository root, once a file was placed

    def project_of(self, path: str, abs_path: str) -> str:
        rel = PurePosixPath(path)
        root = Path(abs_path)
        for _ in rel.parts:
            root = root.parent
        self.root = root
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


def _declarers(top_level: Iterable[tuple[str, str]]) -> dict[str, tuple[str, ...]]:
    """Lowercased type name -> the projects declaring it."""
    found: dict[str, list[str]] = defaultdict(list)
    for project, name in top_level:
        found[name].append(project)
    return {name: tuple(projects) for name, projects in found.items()}


def _dependencies(project: str, manifests: dict[str, _Manifest]) -> tuple[str, ...]:
    """The un-namespaced projects building a package *project* depends on."""
    wanted = manifests[project].dependencies
    return tuple(
        other
        for other, manifest in manifests.items()
        if other != project and not manifest.namespace and manifest.packages & wanted
    )


def written_parents(text: str, line: int, parent: str) -> list[str]:
    """The paths a heritage clause on *line* writes for parents named *parent*.

    The relation keeps only the last segment; the clause runs from the
    declaration line to its opening brace. ``[parent]`` when none is found.
    """
    header = "\n".join(text.splitlines()[line - 1 : line + 9]).partition("{")[0]
    clause = re.split(r"(?i)\b(?:extends|implements)\b", header, maxsplit=1)
    paths = ["".join(m.split()) for m in _DOTTED_NAME.findall(clause[1] if len(clause) > 1 else "")]
    return [p for p in paths if bare_type_name(p).lower() == parent.lower()] or [parent]


_DOTTED_NAME = re.compile(r"[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*")


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
    in a class declaring ``contactFromDi`` is that method. The kind is read from
    ``CallSite.constructor``, never from the capitalisation: ``new OrgConfig()``
    and ``new orgConfig()`` both name the type, even inside a method
    ``orgConfig``, and a ``new`` that no project type folds to (``new Contact()``,
    the sObject) is left as written, so it never becomes a method ``contact``.
    Any other receiver-less call is a method call, whatever its case.
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

    def spelled(self, name: str, declared: dict[str, set[str]], *, same_kind: bool = False) -> str:
        """*name* as *declared* spells it, when that is one spelling and *name* is none.

        *name* counts as spelled when any symbol is declared as written, unless
        *same_kind*: ``new contact()`` is not spelled by a method ``contact``.
        """
        spellings = declared.get(name.lower())
        written = declared if same_kind else self.names
        if not spellings or name in written.get(name.lower(), ()) or len(spellings) > 1:
            return name
        return next(iter(spellings))

    def call_target(self, call: Any, own: _Spellings) -> str:
        """The spelling a call's target means; *own* is the caller file's."""
        name = call.target_name
        if call.receiver_name or call.receiver_call:
            return self.spelled(name, self.names)
        folded = name.lower()
        if call.constructor:
            # ``new Foo(..)`` names a type and never a method. A name no project
            # type folds to (``new Contact()``, the sObject) is left as written.
            if folded not in self.types:
                return name
            if folded in own.types:
                return own.spelled(name, own.types, same_kind=True)
            return self.spelled(name, self.types, same_kind=True)
        if folded in own.members:
            return own.spelled(name, own.members)
        return self.spelled(name, self.members)
