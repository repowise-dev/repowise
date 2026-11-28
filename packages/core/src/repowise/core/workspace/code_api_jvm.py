"""Code-API contracts of Maven and Gradle modules.

The JVM half of :mod:`.code_api`, which dispatches here. A module that deploys
provides its public Java and Kotlin types, and an import names a Java package
and then a type or a member, so a package is found from the build, a symbol is
named by its Java name, and an import is read by its target.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, TypeVar

from repowise.core.symbol_spans import enclosing_chains

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from repowise.core.analysis.published_packages import BuildFacts
    from repowise.core.build_units.model import BuildUnit, BuildUnitIndex
    from repowise.core.ingestion.resolvers.context import ResolverContext
    from repowise.core.ingestion.resolvers.jvm_workspace import JvmWorkspaceIndex
    from repowise.core.workspace.code_api import CodeSurface, PublishedPackage
    from repowise.core.workspace.repo_index import IndexedSymbol, RepoIndex

    #: A module an import reaches, the exports it consumes there, and whether its target is one.
    _Reached = tuple[PublishedPackage, list[str], bool]
    #: A module a join reaches, first, and what the join gives there.
    _Joined = TypeVar("_Joined", _Reached, tuple[PublishedPackage, str])

#: Kinds that declare members, and so may enclose an exported symbol.
_TYPE_KINDS = frozenset({"class", "interface", "struct", "enum"})

#: Kinds that a call runs, and so a type may inherit.
_METHOD_KINDS = frozenset({"method", "function"})

#: The member of a Kotlin ``companion object`` that an import names after the type.
_COMPANION = "Companion"


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def build_file_names() -> tuple[str, ...]:
    """The names of the build files of a Java module, from the language registry."""
    from repowise.core.ingestion.languages.registry import REGISTRY

    spec = REGISTRY.get("java")
    return tuple(spec.manifest_files) if spec is not None else ()


def _context(repo_path: Path, facts: BuildFacts) -> ResolverContext:
    """The resolver context over the files of *repo_path* that the JVM readers take."""
    from repowise.core.ingestion.resolvers import ResolverContext

    paths = {f"{d}/{n}" if d else n for d, names in facts.tree.items() for n in names}
    return ResolverContext(path_set=paths, stem_map={}, graph=None, repo_path=repo_path)


def _unit_names(ctx: ResolverContext, units: BuildUnitIndex) -> dict[str, str]:
    """``{unit id: the name the module is published by}``.

    A Maven unit's id holds its ``groupId:artifactId``, a parent's ``groupId``
    inherited. A Gradle unit's id holds its project path, which follows the
    root name its settings declare (``shop:client``). A build that declares no
    root name gives no name: Gradle then names the root after its directory,
    which two clones of one repo may name differently.
    """
    from repowise.core.ingestion.resolvers.jvm_gradle import get_or_build_jvm_gradle_index

    names: dict[str, str] = {}
    gradle = [unit for unit in units.units.values() if unit.ecosystem == "gradle"]
    root_name = get_or_build_jvm_gradle_index(ctx).root_project_name if gradle else None
    for unit in units.units.values():
        if unit.ecosystem == "maven":
            names[unit.id] = unit.id.removeprefix("maven:")
        elif unit.ecosystem == "gradle" and root_name:
            path = unit.id.removeprefix("gradle:")
            names[unit.id] = root_name if path == ":" else f"{root_name}{path}"
    return names


def _declared_units(
    ctx: ResolverContext, units: BuildUnitIndex
) -> dict[str, tuple[str, BuildUnit]]:
    """``{module root: (the name its build declares, its build unit)}``."""
    names = _unit_names(ctx, units)
    named: dict[str, tuple[str, BuildUnit]] = {}
    for unit in units.units.values():
        if unit.id in names and unit.root not in named:
            named[unit.root] = (names[unit.id], unit)
    return named


def _in_other_build(
    path: str, root: str, tree: Mapping[str, frozenset[str]], build_files: Collection[str]
) -> bool:
    """Whether a directory between *root* and *path* holds a build file of its own.

    The build of *root* does not list it, or a unit of it would own *path*:
    an example or sample project beside the library, with its own pom.
    """
    parts = path.split("/")[:-1]
    below = range(len(root.split("/")) + 1 if root else 1, len(parts) + 1)
    return any(
        not tree.get("/".join(parts[:depth]), frozenset()).isdisjoint(build_files)
        for depth in below
    )


def _owned_files(
    units: BuildUnitIndex, jvm: JvmWorkspaceIndex, tree: Mapping[str, frozenset[str]]
) -> dict[str, set[str]]:
    """``{module root: the Java and Kotlin files it publishes from}``.

    Left out: a test file, a file of a package that a ``module-info.java`` of
    the module does not export, and a file under a directory with a build
    file of its own that the build of the module does not list.
    """
    from repowise.core.test_paths import is_test_path

    build_files = build_file_names()
    owned: dict[str, set[str]] = {}
    for path, package in jvm.file_to_package.items():
        if is_test_path(path):
            continue
        for unit in units.units_owning(path):
            exported = jvm.jpms_exports.get(unit.root)
            if exported is not None and package not in exported:
                continue
            if not _in_other_build(path, unit.root, tree, build_files):
                owned.setdefault(unit.root, set()).add(path)
    return owned


def find_packages(
    alias: str,
    repo_path: Path,
    counts: dict[str, int],
    depended: Collection[str] = frozenset(),
) -> list[PublishedPackage]:
    """The Maven and Gradle modules that deploy, owned and named by the readers the index uses.

    ``jvm_published`` (the dead-code reader) says which module dirs deploy, or,
    in *depended*, are depended on by a pom of another repo or needed by such a module.
    The build units say which files each module owns, its build file and its
    name, and the JVM workspace index gives the Java package of each of those
    files, which is what an import names. Test files are no part of the
    surface, nor is a file under a directory with a build file of its own that
    the build of the module does not list, nor, where a ``module-info.java``
    exports packages, a file of a package it does not export. A Gradle name
    (``shop:client``) is not a Maven coordinate, but both tools publish to a
    Maven repository and an importer names a Java package either way, so both
    are ecosystem ``maven``.

    Counted: ``code_unpublished_manifest``, a module that does not deploy;
    ``code_unnamed_module``, one that deploys but whose build declares no
    name, or that no build lists; ``code_module_without_sources``, one that
    deploys but owns no Java or Kotlin file that is none of the above.
    """
    from repowise.core.analysis.published_packages import BuildFacts, jvm_published
    from repowise.core.build_units.collect import collect_build_units
    from repowise.core.ingestion.resolvers.jvm_workspace import get_or_build_jvm_index
    from repowise.core.workspace.code_api import PublishedPackage

    facts = BuildFacts(repo_path)
    modules = jvm_published(facts, depended)
    publishing = sorted(root for root, name in modules.items() if name is not None)
    for _ in range(len(modules) - len(publishing)):
        _count(counts, "code_unpublished_manifest")
    if not publishing:
        return []
    ctx = _context(repo_path, facts)
    units = collect_build_units(ctx)
    jvm = get_or_build_jvm_index(ctx)
    named = _declared_units(ctx, units)
    owned = _owned_files(units, jvm, facts.tree)
    out: list[PublishedPackage] = []
    for root in publishing:
        if root not in named:
            _count(counts, "code_unnamed_module")
            continue
        files = owned.get(root)
        if not files:
            _count(counts, "code_module_without_sources")
            continue
        name, unit = named[root]
        file_packages = {f: jvm.file_to_package[f] for f in sorted(files)}
        out.append(
            PublishedPackage(
                name=name,
                ecosystem="maven",
                repo=alias,
                manifest=unit.manifest,
                root=root,
                entry_files=frozenset(files),
                import_names=frozenset(file_packages.values()),
                file_packages=file_packages,
            )
        )
    return out


def _nests(outer: IndexedSymbol, inner: IndexedSymbol) -> bool:
    """Whether *inner*, on the lines of *outer*, is declared inside it.

    Only a type holds a member on its own lines: a record's constructor and
    its accessors share the header line and the record's name, and the
    constructor holds none of them.
    """
    from repowise.core.ingestion.symbol_identity import scope_names

    return outer.kind in _TYPE_KINDS and outer.name in scope_names(inner.symbol_id)


def exports(
    package: PublishedPackage, index: RepoIndex, symbols: list[IndexedSymbol]
) -> list[tuple[IndexedSymbol, str]]:
    """Each of *symbols* that *package* publishes, with the path an importer names it by.

    A symbol is named by its full Java name: the package of its file, every
    type that encloses it, and its name (``com.acme.lib.Order.Builder.build``),
    so one simple name in two packages of a module stays two contracts. Only a
    symbol that types alone enclose, all of them public, is reachable from
    another package: not a public method of a package-private Java class or of
    a Kotlin ``internal`` class. Nor is a member of an anonymous class, which
    no enclosing type names (``new Comparator() {..}`` in a field, an
    initializer or a method, and the body of an enum constant), nor of a local
    class. The index names only the direct parent of a member in its id, so a
    member whose id names another parent than its innermost enclosing symbol
    is one of those.
    """
    from repowise.core.ingestion.symbol_identity import scope_names

    by_file: dict[str, list[IndexedSymbol]] = {}
    for symbol in symbols:
        by_file.setdefault(symbol.file_path, []).append(symbol)
    out: list[tuple[IndexedSymbol, str]] = []
    for path, published in by_file.items():
        held = [s for s in index.symbols_for_file(path) if s.kind != "module"]
        chains = dict(zip((s.symbol_id for s in held), enclosing_chains(held, _nests), strict=True))
        for symbol in published:
            outer = chains.get(symbol.symbol_id, [])
            if scope_names(symbol.symbol_id) == [t.name for t in outer[-1:]] and all(
                t.kind in _TYPE_KINDS and t.visibility == "public" for t in outer
            ):
                names = [package.file_packages[path], *(t.name for t in outer), symbol.name]
                out.append((symbol, ".".join(names)))
    return out


def inherited(
    index: RepoIndex, exported: Mapping[str, tuple[PublishedPackage, str]]
) -> dict[str, dict[str, tuple[PublishedPackage, str]]]:
    """The members the exported types of one repo inherit, by the module of the type.

    *exported* gives each export of the repo by its symbol id, with its module
    and path. The result maps the path of an inherited member on the type
    (``com.acme.Mail.push``) to the module and the path of the export that
    declares it (``com.acme.Sender.push``). A member is looked up as Java looks
    up a method: the nearest class up the superclass chain that declares the
    name, else the one supertype that declares it and that no other declaring
    supertype extends. Two such supertypes give none.

    Only the supertypes ingestion resolved inside the repo are walked: one the
    repo does not declare ends the walk. So a method it declares is not
    inherited here, and where an interface of the repo declares that method
    too, the interface's method is the one joined. A declaring type that is
    not exported gives none: a missed consumer, never a wrong one.
    """
    walk = _Heritage(index)
    out: dict[str, dict[str, tuple[PublishedPackage, str]]] = {}
    for type_id, (package, path) in exported.items():
        for name, declaring in walk.inherited(type_id).items():
            owner = exported.get(declaring)
            if owner is not None:
                joined = (owner[0], f"{owner[1]}.{name}")
                out.setdefault(package.name, {})[f"{path}.{name}"] = joined
    return out


class _Heritage:
    """The supertypes and the declared methods of the types of one repo."""

    def __init__(self, index: RepoIndex) -> None:
        self._index = index
        self._kinds: dict[str, str] = {}
        self._methods: dict[str, set[str]] = {}
        self._read: set[str] = set()
        self._ancestors: dict[str, frozenset[str]] = {}

    def _file(self, symbol_id: str) -> None:
        from repowise.core.ingestion.symbol_identity import (
            base_symbol_id,
            file_of_symbol,
            outer_scope,
        )

        path = file_of_symbol(symbol_id)
        if path in self._read:
            return
        self._read.add(path)
        for symbol in self._index.symbols_for_file(path):
            self._kinds[symbol.symbol_id] = symbol.kind
            if symbol.kind in _METHOD_KINDS:
                owner = outer_scope(base_symbol_id(symbol.symbol_id))
                self._methods.setdefault(owner, set()).add(symbol.name)

    def kind(self, type_id: str) -> str | None:
        self._file(type_id)
        return self._kinds.get(type_id)

    def methods(self, type_id: str) -> set[str]:
        """The names of the methods *type_id* declares itself."""
        self._file(type_id)
        return self._methods.get(type_id, set())

    def ancestors(self, type_id: str) -> frozenset[str]:
        """Every supertype of *type_id*, at any distance."""
        found = self._ancestors.get(type_id)
        if found is None:
            reached: set[str] = set()
            pending = list(self._index.supertypes(type_id))
            while pending:
                node = pending.pop()
                if node not in reached:
                    reached.add(node)
                    pending.extend(self._index.supertypes(node))
            reached.discard(type_id)
            found = self._ancestors[type_id] = frozenset(reached)
        return found

    def _superclasses(self, type_id: str) -> list[str]:
        """The superclass chain of *type_id*, nearest first."""
        chain: list[str] = []
        node = type_id
        while True:
            parents = [p for p in self._index.supertypes(node) if self.kind(p) == "class"]
            if len(parents) != 1 or parents[0] in chain or parents[0] == type_id:
                return chain
            node = parents[0]
            chain.append(node)

    def inherited(self, type_id: str) -> dict[str, str]:
        """``{method name: the supertype that declares it}`` for each method *type_id*
        inherits and does not declare."""
        if not self._index.supertypes(type_id):
            return {}
        own = self.methods(type_id)
        out: dict[str, str] = {}
        for node in self._superclasses(type_id):
            for name in self.methods(node) - own:
                out.setdefault(name, node)
        declaring: dict[str, set[str]] = {}
        for node in self.ancestors(type_id):
            for name in self.methods(node) - own - out.keys():
                declaring.setdefault(name, set()).add(node)
        for name, nodes in declaring.items():
            specific = [n for n in nodes if not any(n in self.ancestors(o) for o in nodes)]
            if len(specific) == 1:
                out[name] = specific[0]
        return out


def file_class(package: PublishedPackage, symbol: IndexedSymbol) -> str | None:
    """The Java class a Kotlin file compiles its top-level functions into, or None.

    ``Utils.kt`` gives ``com.acme.lib.UtilsKt``. A file annotated
    ``@file:JvmName`` is named otherwise, which no index here holds, so a Java
    import of that class resolves to nothing and is counted.
    """
    from repowise.core.ingestion.symbol_identity import base_symbol_id, make_symbol_id

    path = symbol.file_path
    top_level = base_symbol_id(symbol.symbol_id) == make_symbol_id(path, symbol.name)
    if symbol.kind != "function" or not path.endswith(".kt") or not top_level:
        return None
    stem = PurePosixPath(path).stem
    return f"{package.file_packages[path]}.{stem[:1].upper()}{stem[1:]}Kt"


def _reached(
    parts: list[str], first: int, exported: set[str], file_classes: Mapping[str, set[str]]
) -> tuple[list[str], bool] | None:
    """The exports the import *parts* consume in one module, and whether its target is an export.

    None where the module declares neither a type nor a Kotlin file class of
    the first name.
    """
    head = ".".join(parts[:first])
    if head in exported:
        target = ".".join(parts)
        passed = [p for i in range(first, len(parts) + 1) if (p := ".".join(parts[:i])) in exported]
        below = sorted(e for e in exported if e.startswith(f"{target}."))
        return passed + below, target in exported
    if head in file_classes:
        if len(parts) == first:
            return sorted(file_classes[head]), True
        function = ".".join([*parts[: first - 1], *parts[first:]])
        named = len(parts) == first + 1 and function in file_classes[head]
        return ([function] if named else []), named
    return None


class ModuleNeeds:
    """What the Maven module of a file depends on, read when a join first needs it."""

    def __init__(self, repo_paths: Mapping[str, Path], repos: Collection[str]) -> None:
        self._repo_paths = dict(repo_paths)
        self._repos = repos
        self._needs: dict[str, dict[str, frozenset[str]]] | None = None
        self._units: dict[str, BuildUnitIndex] = {}

    def of(self, repo: str, path: str) -> frozenset[str]:
        """The names of the modules of the workspace that the module of *path* in *repo*
        depends on (:func:`.maven_dependencies.module_dependencies`).

        Those of each Maven module that owns *path*, as the build units say, and
        of every Maven module of *repo* where none does. Empty where *repo* has
        no Maven module.
        """
        if self._needs is None:
            from repowise.core.workspace.manifests import find_poms
            from repowise.core.workspace.maven_dependencies import module_dependencies

            poms = {alias: find_poms(root) for alias, root in self._repo_paths.items()}
            self._needs = module_dependencies(self._repo_paths, poms, self._repos)
        by_manifest = self._needs.get(repo, {})
        if not by_manifest:
            return frozenset()
        if repo not in self._units:
            from repowise.core.analysis.published_packages import BuildFacts
            from repowise.core.build_units.collect import collect_build_units

            root = self._repo_paths[repo]
            self._units[repo] = collect_build_units(_context(root, BuildFacts(root)))
        units = self._units[repo].units_owning(path)
        owners = [unit.manifest for unit in units if unit.manifest in by_manifest]
        return frozenset().union(*(by_manifest[m] for m in owners or by_manifest))


def consumed(
    java_package: str,
    rest: str,
    packages: list[PublishedPackage],
    surface: CodeSurface,
    repo: str,
    stats: dict[str, int],
    depended: Callable[[], Collection[str]],
) -> list[tuple[PublishedPackage, list[str]]]:
    """The exports a Java or Kotlin import in *repo* consumes, in each module that declares them.

    The first name after the Java package must be an export of a module that
    has that package: a Java package split across modules joins the module
    that declares the name, and a sub-package no module has joins nothing.
    Where several modules declare it, as two modules of one repo that hold
    one class, the import joins those that *depended* names, the modules the
    importing module depends on, and all of them where it depends on none.
    The import consumes each export its target passes through and every
    export under the target: a type gives the type and its members, a nested
    type the outer type and the nested type with its members, and a static
    member import or a Kotlin member import the type and that member. A
    static wildcard (``Money.*``) gives the type and all its members, since
    the index records no ``static``. ``A.Companion.make`` is ``A.make``, when
    ``A.make`` is an export and ``A.Companion`` is none, and so is
    ``A.Factory.make`` for a companion object named ``Factory``. A Java import of
    ``UtilsKt``, the class of ``Utils.kt``, gives the top-level functions of
    that file, and a static import of one of them gives that function.

    A package wildcard names no declaration, and the index holds no use of a
    type no repo of the graph defines, so it consumes nothing and is counted
    in ``code_wildcard_imports``. An import whose target no export of the
    module is, such as a field, a constant, an enum constant, a Kotlin
    property or the class of a ``@file:JvmName``, is counted in
    ``code_unresolved_imports``, and consumes the type it passes through if
    that is an export: a missed consumer, never a wrong one. An import of a
    module of *repo* itself is counted in neither.
    """
    packages = [package for package in packages if package.repo != repo]
    if packages and rest == "*":
        _count(stats, "code_wildcard_imports")
    if not packages or rest in ("", "*"):
        return []
    reached = _depended_only(_reached_in(java_package, rest, packages, surface), depended)
    if not any(named for _, _, named in reached):
        _count(stats, "code_unresolved_imports")
    return [(package, names) for package, names, _ in reached]


def call_target(module: str, symbol_path: str) -> str:
    """The full Java name a call reaches: the import *module* and the rest of *symbol_path*.

    A recorded call names its member from the imported name on: an import of
    ``a.b.Client`` gives ``Client.charge``, and a static import of
    ``a.b.Charges.refund`` gives ``Charges.refund``. So the two overlap by
    the names they share, and the longest overlap is taken once. An on-demand
    import ``a.b.*`` names the package, and the path starts with the type.
    """
    names = module.removesuffix(".*").split(".")
    path = symbol_path.split(".") if symbol_path else []
    width = next((w for w in range(min(len(names), len(path)), 0, -1) if names[-w:] == path[:w]), 0)
    return ".".join([*names, *path[width:]])


def called(
    java_package: str,
    rest: str,
    packages: list[PublishedPackage],
    surface: CodeSurface,
    repo: str,
    depended: Callable[[], Collection[str]],
) -> list[tuple[PublishedPackage, str]]:
    """The export a Java or Kotlin call in *repo* reaches, in each module that declares it.

    Only the called member, read as an import's target is: ``A.Companion.make``
    is ``A.make``, and ``UtilsKt.f`` is the top-level ``f`` of ``Utils.kt``. A
    method the type inherits is the export of the supertype that declares it
    (:func:`inherited`). A member no module exports, such as a method of a type
    the workspace does not declare, gives none: a missed consumer, never a
    wrong one. Where several modules declare it, the call joins those that
    *depended* names, as an import does.
    """
    parts = f"{java_package}.{rest}".split(".")
    first = java_package.count(".") + 2
    function = ".".join([*parts[: first - 1], *parts[first:]])
    out: list[tuple[PublishedPackage, str]] = []
    for package in packages:
        if package.repo == repo:
            continue
        exported = surface.members[package.name]
        target = ".".join(_without_companion(parts, first, exported))
        functions = surface.file_classes.get(package.name, {}).get(".".join(parts[:first]), ())
        if target in exported:
            out.append((package, target))
        elif len(parts) == first + 1 and function in functions:
            out.append((package, function))
        elif (declared := surface.inherited.get(package.name, {}).get(target)) is not None:
            out.append(declared)
    return _depended_only(out, depended)


def _reached_in(
    java_package: str, rest: str, packages: list[PublishedPackage], surface: CodeSurface
) -> list[_Reached]:
    """Each of *packages* that declares the first name of the import, with what it consumes there
    and whether the target is an export."""
    parts = f"{java_package}.{rest}".removesuffix(".*").split(".")
    first = java_package.count(".") + 2
    out: list[_Reached] = []
    for package in packages:
        exported = surface.members[package.name]
        file_classes = surface.file_classes.get(package.name, {})
        found = _reached(_without_companion(parts, first, exported), first, exported, file_classes)
        if found is not None:
            out.append((package, *found))
    return out


def _depended_only(
    reached: list[_Joined], depended: Callable[[], Collection[str]]
) -> list[_Joined]:
    """Of *reached*, the modules named in *depended*; all of them where it names none.

    *depended* is read only where more than one module is reached.
    """
    if len(reached) < 2:
        return reached
    names = depended()
    return [r for r in reached if r[0].name in names] or reached


def _without_companion(parts: list[str], first: int, exported: set[str]) -> list[str]:
    """*parts* without the companion object of an exported type of the module.

    The index keeps no companion object: its members are members of the type.
    So a name no export holds is the companion where it is ``Companion`` or
    where an export of the type follows it (``A.Factory.make`` is ``A.make``).
    """
    for i in range(first, len(parts)):
        owner = ".".join(parts[:i])
        if owner not in exported or f"{owner}.{parts[i]}" in exported:
            continue
        member = i + 1 < len(parts) and f"{owner}.{parts[i + 1]}" in exported
        if parts[i] == _COMPANION or member:
            return [*parts[:i], *parts[i + 1 :]]
    return parts
