"""Code-API contracts of Maven and Gradle modules.

The JVM half of :mod:`.code_api`, which dispatches here. A module that deploys
provides its public Java and Kotlin types, and an import names a Java package
and then a type or a member, so a package is found from the build, a symbol is
named by its Java name, and an import is read by its target.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

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

#: Kinds that declare members, and so may enclose an exported symbol.
_TYPE_KINDS = frozenset({"class", "interface", "struct", "enum"})

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


def find_packages(alias: str, repo_path: Path, counts: dict[str, int]) -> list[PublishedPackage]:
    """The Maven and Gradle modules that deploy, owned and named by the readers the index uses.

    ``jvm_published`` (the dead-code reader) says which module dirs deploy.
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
    modules = jvm_published(facts)
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
    reached: list[_Reached], depended: Callable[[], Collection[str]]
) -> list[_Reached]:
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
