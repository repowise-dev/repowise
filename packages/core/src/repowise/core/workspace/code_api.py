"""Code-API contracts — a library's public surface as a cross-repo contract.

The four transports already extracted all cross a wire. A package boundary does
not, which is the gap the reported .NET case falls through: a repo that ships a
library *provides* its public symbols, and a repo that imports the package
*consumes* them. Delete one, or add a required parameter to it, and every
importing repo breaks.

Both halves read the index rather than the text. A provider knows its symbol at
emission, so it carries a ``symbol_id`` and
:func:`..signature_schema.attach_signature_schemas` turns the parameter list
into its schema — which is what makes the field rules fire on a method. A
consumer comes from the ``imports`` edges to an ``external:`` target, which
already name the symbols they bring in. A Java or Kotlin import is read by its
target path, since the names its edge carries do not tell a member from a type.
Each call into the package (``external_call_sites``) is a consumer as well, with
its line and calling symbol, so a link says which function uses which export.

**Published, not merely public.** The surface is what the *manifest* exposes,
never every public symbol under the package: the two differ by an order of
magnitude, and the larger number is also wrong, since a symbol no entry point
re-exports cannot be imported by package name. Where an ecosystem has no entry
file the whole project directory is the surface, allowed only when the manifest
carries an explicit publish opt-in to bound it — .NET has one (``IsPackable``);
Go does not. A Maven or Gradle module that deploys is the files its build unit
owns, which :mod:`.code_api_jvm` reads.

**Package identity is the name the build declares, and only that.** It is the
one of this repo's several package notions that can be joined to an
``external:<pkg>`` node id, since that id is whatever the importing source
wrote. Nothing here reads or changes the others. A Maven module is named
``groupId:artifactId``. Gradle has no name of that kind, since it names a
publication by group and artifact: a Gradle module is named by the
``rootProject.name`` of its settings and its project path (``shop:client``).

Reuse, not extension: ``_removed_endpoint`` already covers a deleted public
symbol and the three field rules cover its parameters, so this module writes no
breaking-change rule.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import partial
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from repowise.core.fsutils import repo_relative
from repowise.core.pyparse import parse_python
from repowise.core.workspace import code_api_jvm
from repowise.core.workspace.origins import ContractOrigin, origin_confidence

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping
    from pathlib import Path

    from repowise.core.ingestion.external_calls import ExternalCallSite
    from repowise.core.workspace.repo_index import (
        ExternalImport,
        IndexedSymbol,
        RepoIndex,
        WorkspaceIndex,
    )

_log = logging.getLogger("repowise.workspace.code_api")

#: ``Contract.contract_type`` for everything this module produces.
CODE_CONTRACT_TYPE = "code"

#: Both sides are index-resolved, not regex-matched — the manifest and the
#: import edge each name their half outright.
_ORIGIN = ContractOrigin.PACKAGE_EXPORT

#: Manifest basenames handled here, mapped to the ecosystem they declare.
_MANIFESTS: dict[str, str] = {
    "package.json": "npm",
    "pyproject.toml": "pypi",
    "Cargo.toml": "cargo",
}

#: Not implemented, but counted, so the coverage figure names what it misses.
_UNSUPPORTED_MANIFESTS: dict[str, str] = {"go.mod": "go"}

#: Ecosystem tokens a resolver prefixes onto an external node id — a C#
#: `using` that matches a PackageReference resolves to `external:nuget:<ns>`.
_EXTERNAL_PREFIXES = ("nuget:", "pub:", "gem:")

#: How deep the walk looks for npm, PyPI and Cargo manifests: the bound
#: ``external_systems._discover`` uses, so both see one set of them. A JVM build
#: file seen within it starts the Maven and Gradle readers, which then read every
#: depth; a repo whose JVM build files all lie deeper is not read.
_MANIFEST_DEPTH = 3

#: The language an entry-file-less ecosystem's surface is written in.
_ECOSYSTEM_LANGUAGE = {"nuget": "csharp"}

#: Kinds a consumer can import by name.
_EXPORTABLE_KINDS = frozenset({"function", "method", "class", "interface", "struct", "enum"})

#: Kinds a call runs: a symbol declared inside one is local to it.
_CALLABLE_KINDS = frozenset({"function", "method"})


@dataclass(frozen=True)
class PublishedPackage:
    """One package a repo ships, as its own manifest declares it."""

    name: str  # the name the build declares; ``import_names`` is what an importer writes
    ecosystem: str
    repo: str  # workspace alias
    manifest: str  # repo-relative POSIX path
    root: str  # repo-relative POSIX dir, "" at the repo root
    #: The files whose public symbols are the surface: an entry file, or for a
    #: JVM module the files its build unit owns. ``None`` where the ecosystem
    #: has no entry file and *root* is the surface.
    entry_files: frozenset[str] | None = None
    #: ``__all__`` names defined elsewhere in the package.
    reexported: frozenset[str] = frozenset()
    #: What an importing source actually writes. Not the same as ``name``:
    #: a PyPI distribution ships a differently-named module, and Cargo
    #: rewrites `-` to `_`.
    import_names: frozenset[str] = frozenset()
    #: For a JVM module: the Java package each of its files declares, the
    #: start of every exported path (``com.acme.billing.client.Invoice``).
    file_packages: Mapping[str, str] = field(default_factory=dict, compare=False)


# ---------------------------------------------------------------------------
# Manifest reading — one small function per ecosystem
# ---------------------------------------------------------------------------


def _npm(data: dict[str, Any], root: str) -> tuple[str, frozenset[str]] | None:
    """``name`` plus every file ``exports`` / ``main`` / ``module`` / ``types`` names.

    ``private`` is deliberately not read. It blocks the public registry, not
    workspace consumption — this workspace's own ``@repowise-dev/types`` is
    ``private: true`` and imported across repos — and an app with no entry point
    is already excluded by having no surface.
    """
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    targets: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str):
            # `exports` values are spelt `./x`; `main`/`module`/`types` are
            # routinely bare. A URL or an `#imports` alias names no file.
            if value.startswith(("/", "#")) or "://" in value:
                return
            rel = value[2:] if value.startswith("./") else value
            if rel:
                targets.add(f"{root}/{rel}" if root else rel)
        elif isinstance(value, dict):  # conditional exports: {"import": "./x.js"}
            for nested in value.values():
                add(nested)
        elif isinstance(value, list):  # fallback arrays: [{"import": "./a"}, "./b"]
            for nested in value:
                add(nested)

    add(data.get("exports"))
    for key in ("main", "module", "types"):
        add(data.get(key))
    return (name, frozenset(targets)) if targets else None


def _pypi(text: str, root: str) -> tuple[str, frozenset[str], frozenset[str]] | None:
    """``[project].name`` (or Poetry's), and the distribution's top-level package."""
    import tomllib

    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    project = data.get("project")
    name = project.get("name") if isinstance(project, dict) else None
    if not isinstance(name, str):
        name = toml_table(data, "tool", "poetry").get("name")
    if not isinstance(name, str) or not name:
        return None
    prefix = f"{root}/" if root else ""
    dirs = pypi_package_dirs(data)
    if not dirs:
        # PEP 503 allows `-`/`.` in a distribution name where the module needs
        # an identifier, and the two often differ beyond that. A guess, used
        # only when the build backend declares nothing.
        module = name.replace("-", "_").replace(".", "_")
        dirs = [f"src/{module}", module]
    return name, frozenset(f"{prefix}{d}/__init__.py" for d in dirs), frozenset()


def toml_table(data: Any, *keys: str) -> dict[str, Any]:
    """Walk a chain of TOML tables, yielding {} the moment one is not a table."""
    for key in keys:
        if not isinstance(data, dict):
            return {}
        data = data.get(key, {})
    return data if isinstance(data, dict) else {}


def pypi_package_dirs(data: dict[str, Any]) -> list[str]:
    """Package directories the build backend declares, newest layouts first.

    Every read is shape-checked: a hand-edited ``pyproject.toml`` must not be
    able to crash extraction for the whole workspace, and setuptools' auto-
    discovery spelling (``packages = {find = {...}}``) is a table, not a list.
    """

    def strings(value: Any) -> list[str]:
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    dirs: list[str] = strings(toml_table(data, "tool", "hatch", "build", "targets", "wheel").get("packages"))
    dirs += [
        p.replace(".", "/") for p in strings(toml_table(data, "tool", "setuptools").get("packages"))
    ]
    for entry in toml_table(data, "tool", "poetry").get("packages") or []:
        include = entry.get("include") if isinstance(entry, dict) else None
        if isinstance(include, str):
            source = entry.get("from")
            dirs.append(f"{source}/{include}" if isinstance(source, str) else include)
    dirs += _flit_module_dirs(data)
    dirs += _uv_build_module_dirs(data)
    return [d.strip("/") for d in dirs if isinstance(d, str) and d.strip("/")]


def _module_paths(module: Any, roots: tuple[str, ...]) -> list[str]:
    """A dotted module name as package paths, one per *root* it may sit under.

    ``acme.providers.ftp`` is the import path of a namespace package; the
    directory a build backend ships for it is the dots spelled as separators,
    ``acme/providers/ftp``, under whichever root that backend uses.
    """
    if not isinstance(module, str):
        return []
    path = module.strip().replace(".", "/").strip("/")
    if not path:
        return []
    return [f"{root.strip('/')}/{path}" if root.strip("/") else path for root in roots]


def _flit_module_dirs(data: dict[str, Any]) -> list[str]:
    """``[tool.flit.module] name``, under ``src/`` or beside the manifest.

    flit names the import package in the TOML, which is the only reliable
    source when the distribution name does not spell it - a namespace package
    such as ``acme-providers-ftp`` shipping ``acme.providers.ftp`` has no
    top-level directory a guess from the name could find.
    """
    return _module_paths(toml_table(data, "tool", "flit", "module").get("name"), ("src", ""))


def _uv_build_module_dirs(data: dict[str, Any]) -> list[str]:
    """uv_build's ``module-name``, under its ``module-root``."""
    backend = toml_table(data, "tool", "uv", "build-backend")
    root = backend.get("module-root")
    roots = (root, "") if isinstance(root, str) else ("src", "")
    return _module_paths(backend.get("module-name"), roots)


def dunder_all(path: Path) -> frozenset[str] | None:
    """The string literals a module's ``__all__`` lists; None when it has none.

    Only plain string literals count; a computed ``__all__`` is refused
    whole rather than half-read, per the same rule the signature mapper follows.
    """
    import ast

    try:
        tree = parse_python(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None
    names: set[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            continue
        if not isinstance(node.value, ast.List | ast.Tuple):
            continue
        names = (names or set()) | {
            el.value
            for el in node.value.elts
            if isinstance(el, ast.Constant) and isinstance(el.value, str)
        }
    return None if names is None else frozenset(names)


def _dunder_all(repo_path: Path, entry_files: frozenset[str]) -> frozenset[str]:
    """Names an entry ``__init__.py`` re-exports via ``__all__``.

    Python's entry file usually *declares* nothing (it is a wall of
    ``from .x import Y``), so without this a distribution's surface reads as
    empty.
    """
    return frozenset(name for rel in entry_files for name in dunder_all(repo_path / rel) or ())


def _cargo(text: str, root: str, crate_dir: Path) -> tuple[str, frozenset[str]] | None:
    """``[package].name`` and the crate root, which is the whole public surface.

    A crate with no ``[lib]`` table and no ``src/lib.rs`` is bin-only: a
    program, not a library, so it publishes nothing.
    """
    import tomllib

    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    package = data.get("package")
    name = package.get("name") if isinstance(package, dict) else None
    if not isinstance(name, str) or not name:
        return None
    lib = data.get("lib")
    if not isinstance(lib, dict) and not (crate_dir / "src" / "lib.rs").is_file():
        return None
    path = lib.get("path") if isinstance(lib, dict) else None
    entry = path if isinstance(path, str) else "src/lib.rs"
    return name, frozenset({f"{root}/{entry}" if root else entry})


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_published_packages(
    alias: str, repo_path: Path, *, jvm: bool = True
) -> tuple[list[PublishedPackage], dict[str, int]]:
    """Every package *repo_path* declares it publishes, plus skip counters.

    *jvm* False leaves out the Maven and Gradle modules, which need the build
    units and the JVM workspace index of the repo.
    """
    from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
    from repowise.core.ingestion.resolvers.dotnet.msbuild import find_csproj_files, parse_csproj

    packages: list[PublishedPackage] = []
    counts: dict[str, int] = {}
    prune = PRUNED_DIRS | {"target", "dist", "build"}
    jvm_files = code_api_jvm.build_file_names()
    has_jvm_build = False

    for dirpath, dirnames, filenames in walk_repo(repo_path, prune_dirs=prune):
        if len(dirpath.relative_to(repo_path).parts) >= _MANIFEST_DEPTH:
            dirnames[:] = []
        rel_dir = repo_relative(dirpath, repo_path)
        if rel_dir is None:
            continue
        root = "" if rel_dir == "." else rel_dir
        has_jvm_build = has_jvm_build or any(n in jvm_files for n in filenames)
        for fname in filenames:
            if fname in _UNSUPPORTED_MANIFESTS:
                counts["code_unsupported_ecosystem"] = (
                    counts.get("code_unsupported_ecosystem", 0) + 1
                )
                continue
            ecosystem = _MANIFESTS.get(fname)
            if ecosystem is None:
                continue
            manifest = dirpath / fname
            rel_manifest = repo_relative(manifest, repo_path)
            if rel_manifest is None:
                continue
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = _read_manifest(ecosystem, text, root, dirpath)
            if parsed is None:
                counts["code_unpublished_manifest"] = (
                    counts.get("code_unpublished_manifest", 0) + 1
                )
                continue
            name, entries, reexported = parsed
            if ecosystem == "pypi":
                reexported = _dunder_all(repo_path, entries)
            packages.append(
                PublishedPackage(
                    name=name,
                    ecosystem=ecosystem,
                    repo=alias,
                    manifest=rel_manifest,
                    root=root,
                    entry_files=entries,
                    reexported=reexported,
                    import_names=_import_names(ecosystem, name, entries),
                )
            )

    for csproj in find_csproj_files(repo_path):
        project = parse_csproj(csproj)
        rel_manifest = repo_relative(csproj, repo_path) if project is not None else None
        if project is None or rel_manifest is None:
            continue
        root = rel_manifest.rsplit("/", 1)[0] if "/" in rel_manifest else ""
        package_id = project.published_id
        if package_id is None:
            counts["code_unpublished_manifest"] = counts.get("code_unpublished_manifest", 0) + 1
            continue
        packages.append(
            PublishedPackage(
                name=package_id,
                ecosystem="nuget",
                repo=alias,
                manifest=rel_manifest,
                root=root,
                entry_files=None,  # no entry file; the assembly's public types are the surface
                import_names=frozenset({package_id}),
            )
        )
    if jvm and has_jvm_build:
        packages.extend(code_api_jvm.find_packages(alias, repo_path, counts))
    return packages, counts


def _read_manifest(
    ecosystem: str, text: str, root: str, manifest_dir: Path
) -> tuple[str, frozenset[str], frozenset[str]] | None:
    if ecosystem == "npm":
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        npm = _npm(data, root)
        return (npm[0], npm[1], frozenset()) if npm else None
    if ecosystem == "pypi":
        return _pypi(text, root)
    cargo = _cargo(text, root, manifest_dir)
    return (cargo[0], cargo[1], frozenset()) if cargo else None


# ---------------------------------------------------------------------------
# Surface resolution
# ---------------------------------------------------------------------------


def _exported_name(symbol: IndexedSymbol) -> str:
    """The path an importer names this symbol by — ``Owner.member`` for a member.

    A bare ``name`` collides across classes, and a member is reachable through
    the type that owns it, which is what lets a consumer importing ``OrderService``
    be linked to a change in ``OrderService.PlaceOrder``.
    """
    qualified = symbol.qualified_name or symbol.name
    # qualified_name is module-scoped ("pkg.mod.Owner.member"); the exported
    # path is the tail, since a package importer never writes the file's path.
    tail = qualified.rsplit(".", 2)[-2:] if symbol.kind == "method" else [symbol.name]
    return ".".join(part for part in tail if part) or symbol.name


def _exports(
    package: PublishedPackage, index: RepoIndex, exclude: Callable[[str], bool]
) -> list[tuple[IndexedSymbol, str]]:
    """Each symbol *package* publishes, with the path an importer names it by."""
    symbols = _surface_symbols(package, index, exclude)
    if package.ecosystem == "maven":
        return code_api_jvm.exports(package, index, symbols)
    return [(s, _exported_name(s)) for s in symbols]


def _surface_symbols(
    package: PublishedPackage, index: RepoIndex, exclude: Callable[[str], bool]
) -> list[IndexedSymbol]:
    """The public symbols *package* publishes, per its entry-file rule."""
    prefix = f"{package.root}/" if package.root else ""
    candidates = [
        s
        for s in index.public_symbols()
        if s.kind in _EXPORTABLE_KINDS
        and not exclude(s.file_path)
        and s.file_path.startswith(prefix)
    ]
    if package.entry_files is None:
        # A .csproj at the repo root gives prefix "", so without this every
        # file in the repo — every language — would be an assembly symbol.
        return [s for s in candidates if s.language == _ECOSYSTEM_LANGUAGE[package.ecosystem]]

    # An `__all__` entry names a symbol its entry file does not declare, so it
    # resolves by name across the package — and only when that name is unique.
    # Two `Widget`s would otherwise bind the contract to whichever the index
    # happened to return first.
    by_name: dict[str, list[IndexedSymbol]] = {}
    for symbol in candidates:
        by_name.setdefault(symbol.name, []).append(symbol)
    return [
        s
        for s in candidates
        if s.file_path in package.entry_files
        or (s.name in package.reexported and len(by_name[s.name]) == 1)
    ]


# ---------------------------------------------------------------------------
# Contract emission
# ---------------------------------------------------------------------------


def _overloads(first: IndexedSymbol, second: IndexedSymbol) -> bool:
    """Whether two symbols with one exported path are overloads of one callable."""
    return (
        first.file_path == second.file_path
        and first.kind == second.kind
        and first.kind in _CALLABLE_KINDS
    )


def contract_id_for(package_name: str, exported: str) -> str:
    return f"{CODE_CONTRACT_TYPE}::{package_name}::{exported}"


@dataclass
class CodeSurface:
    """Every workspace repo's published packages and the contracts they imply."""

    by_repo: dict[str, list[Any]] = field(default_factory=dict)  # alias -> list[Contract]
    stats: dict[str, dict[str, int]] = field(default_factory=dict)  # alias -> counters
    #: package name -> the exported paths its provider contracts cover. What lets
    #: an import of a type expand to the members that type owns.
    members: dict[str, set[str]] = field(default_factory=dict)
    #: package name -> the Java class of each Kotlin file with top-level functions
    #: -> the exported paths of those functions.
    file_classes: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    #: package name -> the path of a method an exported JVM type inherits -> the
    #: module and the exported path of the supertype's method
    #: (:func:`.code_api_jvm.inherited`).
    inherited: dict[str, dict[str, tuple[PublishedPackage, str]]] = field(default_factory=dict)
    #: package name -> an exported path that several overloads share -> each of them.
    overloads: dict[str, dict[str, list[IndexedSymbol]]] = field(default_factory=dict)

    def for_repo(self, alias: str) -> list[Any]:
        return self.by_repo.get(alias, [])


def find_workspace_packages(
    repo_paths: dict[str, Path],
) -> tuple[list[PublishedPackage], dict[str, dict[str, int]]]:
    """Every package the workspace repos publish, plus per-repo skip counters.

    Read from the manifests, and for a Maven or Gradle module that deploys from
    the package declarations of its Java and Kotlin files, so it needs no
    repo's index open.
    """
    published: list[PublishedPackage] = []
    stats: dict[str, dict[str, int]] = {}
    for alias, repo_path in repo_paths.items():
        packages, counts = find_published_packages(alias, repo_path)
        published.extend(packages)
        if counts:
            stats.setdefault(alias, {}).update(counts)
    return published, stats


def build_code_surface(
    repo_paths: dict[str, Path],
    workspace_index: WorkspaceIndex | None,
    exclude: Callable[[str], bool],
    *,
    packages: tuple[list[PublishedPackage], dict[str, dict[str, int]]] | None = None,
    consumers: Collection[str] | None = None,
) -> CodeSurface:
    """Build every ``code`` contract in the workspace, providers before consumers.

    Workspace-wide by necessity, not by preference: a consumer contract only
    exists when the package it imports is published by *some* repo, so neither
    half can be decided inside a single repo's extraction.

    *packages* is :func:`find_workspace_packages` already run. *consumers*
    limits the consumer rows to those repos (every repo by default): a caller
    reusing a repo's stored rows needs none built for it.
    """
    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.extractors.from_index import EXTRACTION_LAYER_KEY, LAYER_INDEX

    surface = CodeSurface()
    if workspace_index is None:
        return surface

    published, manifest_stats = packages or find_workspace_packages(repo_paths)
    for alias, counts in manifest_stats.items():
        surface.stats.setdefault(alias, {}).update(counts)

    # -- providers ---------------------------------------------------------
    by_name: dict[str, PublishedPackage] = {}
    #: Every package by each name an importer writes: Java packages split
    #: across modules, so one name may lead to several.
    by_import: dict[str, list[PublishedPackage]] = {}
    #: repo -> the symbol id of each JVM export -> its module and path.
    jvm_exports: dict[str, dict[str, tuple[PublishedPackage, str]]] = {}
    for package in published:
        index = workspace_index.get(package.repo)
        if index is None:
            continue
        # Two repos publishing one name is a workspace misconfiguration, not a
        # contract; keeping the first makes the artifact deterministic.
        if package.name in by_name:
            surface.stats.setdefault(package.repo, {})["code_duplicate_package"] = (
                surface.stats.setdefault(package.repo, {}).get("code_duplicate_package", 0) + 1
            )
            continue
        by_name[package.name] = package
        for import_name in package.import_names:
            by_import.setdefault(import_name, []).append(package)
        rows = surface.by_repo.setdefault(package.repo, [])
        exported_here = surface.members.setdefault(package.name, set())
        seen: dict[str, IndexedSymbol] = {}
        for symbol, exported in _exports(package, index, exclude):
            first = seen.get(exported)
            if first is not None:
                # Two entry files exporting one name: which row survives
                # would otherwise be decided by index row order. Overloads of
                # one method share its contract, and are no ambiguity.
                if not _overloads(first, symbol):
                    stats = surface.stats.setdefault(package.repo, {})
                    stats["code_ambiguous_export"] = stats.get("code_ambiguous_export", 0) + 1
                else:
                    shared = surface.overloads.setdefault(package.name, {})
                    shared.setdefault(exported, [first]).append(symbol)
                continue
            seen[exported] = symbol
            exported_here.add(exported)
            if package.ecosystem == "maven" and (kt := code_api_jvm.file_class(package, symbol)):
                by_class = surface.file_classes.setdefault(package.name, {})
                by_class.setdefault(kt, set()).add(exported)
            if package.ecosystem == "maven":
                jvm_exports.setdefault(package.repo, {})[symbol.symbol_id] = (package, exported)
            rows.append(
                Contract(
                    repo=package.repo,
                    contract_id=contract_id_for(package.name, exported),
                    contract_type=CODE_CONTRACT_TYPE,
                    role="provider",
                    file_path=symbol.file_path,
                    symbol_name=exported,
                    confidence=origin_confidence(_ORIGIN),
                    origin=_ORIGIN,
                    line=symbol.start_line,
                    # Known at emission, so bind_symbol_ids is a no-op here and
                    # attach_signature_schemas reaches the parameter list.
                    symbol_id=symbol.symbol_id,
                    meta={
                        "package": package.name,
                        "ecosystem": package.ecosystem,
                        EXTRACTION_LAYER_KEY: LAYER_INDEX,
                    },
                )
            )
        stats = surface.stats.setdefault(package.repo, {})
        stats["code_published_packages"] = stats.get("code_published_packages", 0) + 1
        stats["code_provider_symbols"] = stats.get("code_provider_symbols", 0) + len(seen)
    for alias, exports in jvm_exports.items():
        index = workspace_index.get(alias)
        if index is not None:
            surface.inherited.update(code_api_jvm.inherited(index, exports))

    # -- consumers ---------------------------------------------------------
    consuming = [a for a in repo_paths if consumers is None or a in consumers]
    needs = code_api_jvm.ModuleNeeds(repo_paths, consuming)
    for alias in consuming:
        index = workspace_index.get(alias)
        if index is None:
            continue
        rows = surface.by_repo.setdefault(alias, [])
        stats = surface.stats.setdefault(alias, {})
        seen_pairs: set[tuple[str, str, str]] = set()
        importing: set[str] = set()
        for edge in index.external_import_edges():
            match = _match_import(edge.external_name, by_import)
            if match is None or exclude(edge.source_file):
                continue
            depended = partial(needs.of, alias, edge.source_file)
            found = _consumed(match, by_import, edge, surface, alias, stats, depended)
            for package, consumed in found:
                if package.repo == alias:
                    continue
                importing.add(edge.source_file)
                for exported in consumed:
                    key = (package.name, exported, edge.source_file)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    rows.append(_consumer(alias, package, exported, edge.source_file))
        stats["code_consumer_imports"] = len(seen_pairs)
        rows.extend(_call_consumers(alias, index, by_import, surface, exclude, importing, needs))

    _log.debug(
        "Code-API surface: %d package(s), %d contract(s)",
        len(by_name),
        sum(len(rows) for rows in surface.by_repo.values()),
    )
    return surface


def _consumer(
    repo: str,
    package: PublishedPackage,
    exported: str,
    file_path: str,
    *,
    line: int | None = None,
    symbol_id: str | None = None,
    overload: str | None = None,
) -> Any:
    """The consumer contract of *exported* in *file_path*, at a call's *line* and *symbol_id*.

    *overload* is the symbol id of the overload of *exported* the call reaches,
    kept in ``meta["overload"]``.
    """
    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.extractors.from_index import EXTRACTION_LAYER_KEY, LAYER_INDEX

    return Contract(
        repo=repo,
        contract_id=contract_id_for(package.name, exported),
        contract_type=CODE_CONTRACT_TYPE,
        role="consumer",
        file_path=file_path,
        # Display only. A Maven or Gradle name holds colons
        # and a Java name none, so the last colon splits it.
        symbol_name=f"{package.name}:{exported}",
        confidence=origin_confidence(_ORIGIN),
        origin=_ORIGIN,
        line=line,
        symbol_id=symbol_id,
        meta={
            "package": package.name,
            "ecosystem": package.ecosystem,
            EXTRACTION_LAYER_KEY: LAYER_INDEX,
            **({"overload": overload} if overload else {}),
        },
    )


def _overload(
    surface: CodeSurface, package: PublishedPackage, exported: str, argument_count: int | None
) -> str | None:
    """The symbol id of the overload of *exported* a call of *argument_count* arguments
    reaches, or None.

    Overloads of one name share one export contract, whose provider row holds
    the first of them, so the consumer names the one its call reaches, chosen
    by the phases the resolver reads for a call inside a repository: an
    overload of exactly that number of parameters, then one whose defaults
    admit it (Kotlin), then one whose varargs expand to it. The one overload of
    the first phase that has any is chosen. None where the number is not known,
    a signature is not read, or that phase holds several. Limit: the types of
    the arguments are not read, so a fixed-arity overload the types do not fit,
    where Java takes the varargs one, is named in its place.
    """
    from repowise.core.ingestion.call_overload_selection import _overload_phase
    from repowise.core.ingestion.return_types import signature_parameter_range

    candidates = surface.overloads.get(package.name, {}).get(exported)
    if not candidates or argument_count is None:
        return None
    phases: dict[int, list[str]] = {}
    for symbol in candidates:
        admitted = signature_parameter_range(symbol.signature, symbol.language)
        if admitted is None:
            return None
        phase = _overload_phase(admitted, argument_count)
        if phase is not None:
            phases.setdefault(phase, []).append(symbol.symbol_id)
    first = phases[min(phases)] if phases else []
    return first[0] if len(first) == 1 else None


def _call_consumers(
    repo: str,
    index: RepoIndex,
    by_import: Mapping[str, list[PublishedPackage]],
    surface: CodeSurface,
    exclude: Callable[[str], bool],
    importing: Collection[str],
    needs: code_api_jvm.ModuleNeeds,
) -> list[Any]:
    """One consumer for each call from *repo* into a published package.

    Next to the import's consumers, which still hold an export imported and
    never called. A repo whose calls are not all recorded is counted in
    ``code_call_sites_unknown``: unknown, never "not called". Its stored calls
    are not complete, or one of the files in *importing*, which import a
    published package, is of a language whose calls ingestion does not record.
    A call that reaches a package and no export of it is counted in
    ``code_unresolved_calls``.
    """
    stats = surface.stats.setdefault(repo, {})
    sites = index.external_call_sites()
    if _calls_unknown(sites, importing):
        stats["code_call_sites_unknown"] = 1
    # Two calls on one line of one function may reach one export (through an alias).
    rows: dict[tuple[str, str, str, int, str], Any] = {}
    for site in sites or ():
        if exclude(site.file_path):
            continue
        depended = partial(needs.of, repo, site.file_path)
        for package, exported in _counted(_called(site, by_import, surface, repo, depended), stats):
            key = (package.name, exported, site.file_path, site.line, site.caller_symbol_id)
            rows[key] = _consumer(
                repo, package, exported, site.file_path,
                line=site.line, symbol_id=site.caller_symbol_id,
                overload=_overload(surface, package, exported, site.argument_count),
            )  # fmt: skip
    stats["code_consumer_calls"] = len(rows)
    return list(rows.values())


def _counted(
    found: list[tuple[PublishedPackage, str]] | None, stats: dict[str, int]
) -> list[tuple[PublishedPackage, str]]:
    """*found*, with a call that reaches a package and no export of it counted."""
    if found == []:
        stats["code_unresolved_calls"] = stats.get("code_unresolved_calls", 0) + 1
    return found or []


def _calls_unknown(sites: list[ExternalCallSite] | None, importing: Collection[str]) -> bool:
    """Whether a repo's calls are not all recorded: its stored calls are not
    complete, or a file of *importing* is of a language whose calls are not
    recorded. The language is read from the suffix, so a script that only its
    first line names counts as unknown."""
    from repowise.core.ingestion.external_calls import EXTERNAL_CALL_ECOSYSTEMS
    from repowise.core.ingestion.languages.registry import REGISTRY

    if sites is None:
        return True
    return any(
        REGISTRY.from_extension(PurePosixPath(path).suffix.lower())
        not in EXTERNAL_CALL_ECOSYSTEMS
        for path in importing
    )


def _called(
    site: ExternalCallSite,
    by_import: Mapping[str, list[PublishedPackage]],
    surface: CodeSurface,
    repo: str,
    depended: Callable[[], Collection[str]],
) -> list[tuple[PublishedPackage, str]] | None:
    """The export *site* calls in each package of another repo, or None when it
    reaches no such package. *depended* gives the names of the packages the
    calling module depends on (:meth:`.code_api_jvm.ModuleNeeds.of`)."""
    if site.ecosystem == "maven":
        return _called_jvm(site, by_import, surface, repo, depended)
    return _called_by_path(site, by_import, surface, repo)


def _called_jvm(
    site: ExternalCallSite,
    by_import: Mapping[str, list[PublishedPackage]],
    surface: CodeSurface,
    repo: str,
    depended: Callable[[], Collection[str]],
) -> list[tuple[PublishedPackage, str]] | None:
    """A Java or Kotlin call, read by its full name as an import's target is."""
    match = _match_import(code_api_jvm.call_target(site.module, site.symbol_path), by_import)
    packages = [p for p in by_import[match[0]] if p.repo != repo] if match else []
    if not match or not packages:
        return None
    return code_api_jvm.called(*match, packages, surface, repo, depended)


def _called_by_path(
    site: ExternalCallSite,
    by_import: Mapping[str, list[PublishedPackage]],
    surface: CodeSurface,
    repo: str,
) -> list[tuple[PublishedPackage, str]] | None:
    """The export a call of another ecosystem names: its path as written, a name
    or ``Owner.member``. A call into a submodule or a subpath (``lib.sub.get()``,
    ``import { get } from "lib/sub"``) or on a default export names no export,
    since the surface does not say which module of the package declares a name.
    """
    found = _package_of(site, by_import, repo)
    if found is None:
        return None
    package, rest = found
    exported = surface.members[package.name]
    return [(package, site.symbol_path)] if not rest and site.symbol_path in exported else []


def _package_of(
    site: ExternalCallSite, by_import: Mapping[str, list[PublishedPackage]], repo: str
) -> tuple[PublishedPackage, str] | None:
    """The package of another repo, of the call's ecosystem, that the call's module
    names, and the submodule or subpath of it that follows the import name."""
    match = _match_import(site.module, by_import)
    if match is None:
        return None
    package = by_import[match[0]][0]
    if package.repo == repo or package.ecosystem != site.ecosystem:
        return None
    return package, match[1]


def _import_names(ecosystem: str, name: str, entry_files: frozenset[str] | None) -> frozenset[str]:
    """What an importing source writes for this package.

    Only npm's publish id and import specifier are the same string. A PyPI
    distribution ships a module named by its build backend (``repowise-core``
    ships ``repowise``), and Cargo rewrites ``-`` to ``_``.
    """
    names = {name}
    if ecosystem == "cargo":
        names.add(name.replace("-", "_"))
    elif ecosystem == "pypi" and entry_files:
        names.update(f.rsplit("/", 2)[-2] for f in entry_files if "/" in f)
    return frozenset(n for n in names if n)


def _without_ecosystem_prefix(external_name: str) -> str:
    """*external_name* without the token a resolver prefixes onto it, such as ``nuget:``."""
    for prefix in _EXTERNAL_PREFIXES:
        if external_name.startswith(prefix):
            return external_name[len(prefix) :]
    return external_name


def _match_import(external_name: str, import_names: Collection[str]) -> tuple[str, str] | None:
    """The import name an ``external:`` target reaches, and what follows it.

    The target carries whatever the importing source reached: a subpath
    (``@scope/ui/lib/format``), a child namespace (``nuget:Contoso.Orders.Models``)
    or a submodule (``mylib.sub``). The longest import name that prefixes it on a
    separator wins — a bare prefix would match ``@scope/ui`` for an unrelated
    ``@scope/ui-icons``. What follows is ``Api.run`` for ``com.acme.lib.Api.run``,
    ``*`` for ``com.acme.lib.*`` and ``""`` for the import name itself.
    """
    external_name = _without_ecosystem_prefix(external_name)
    if external_name in import_names:
        return external_name, ""
    # Each prefix that ends before a separator, longest first: a lookup per
    # separator, not a scan of every import name.
    end = len(external_name)
    while (end := max(external_name.rfind("/", 0, end), external_name.rfind(".", 0, end))) > 0:
        if external_name[:end] in import_names:
            return external_name[:end], external_name[end + 1 :]
    return None


def _package_for(
    external_name: str, by_import: dict[str, PublishedPackage]
) -> PublishedPackage | None:
    """The package an ``external:`` target resolves to, or None."""
    match = _match_import(external_name, by_import)
    return by_import[match[0]] if match else None


def _consumed(
    match: tuple[str, str],
    by_import: Mapping[str, list[PublishedPackage]],
    edge: ExternalImport,
    surface: CodeSurface,
    repo: str,
    stats: dict[str, int],
    depended: Callable[[], Collection[str]],
) -> list[tuple[PublishedPackage, list[str]]]:
    """Each package an import in *repo* reaches, with the exports it consumes there.

    *depended* gives the names of the packages the importing module depends on
    (:meth:`.code_api_jvm.ModuleNeeds.of`).
    """
    import_name, rest = match
    packages = by_import[import_name]
    if packages[0].ecosystem == "maven":
        return code_api_jvm.consumed(import_name, rest, packages, surface, repo, stats, depended)
    package = packages[0]
    return [
        (
            package,
            [
                e
                for name in edge.imported_names
                for e in _consumed_exports(name, surface.members[package.name])
            ],
        )
    ]


def _consumed_exports(imported: str, exported: set[str]) -> list[str]:
    """The provider exports an import of *imported* consumes.

    The name itself, plus every member it owns. Importing a type consumes its
    methods: a required parameter added to one breaks the importer whether or
    not this call site passes it, which is the same directness the module-level
    non-goal on call-site argument matching states.
    """
    out = [imported] if imported in exported else []
    out.extend(name for name in exported if name.startswith(f"{imported}."))
    return out
