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
already name the symbols they bring in.

**Published, not merely public.** The surface is what the *manifest* exposes,
never every public symbol under the package: the two differ by an order of
magnitude, and the larger number is also wrong, since a symbol no entry point
re-exports cannot be imported by package name. Where an ecosystem has no entry
file the whole project directory is the surface, allowed only when the manifest
carries an explicit publish opt-in to bound it — .NET has one, Go and Maven do
not.

**Package identity is the manifest's declared name, and only that.** It is the
one of this repo's several package notions that can be joined to an
``external:<pkg>`` node id, since that id is whatever the importing source
wrote. Nothing here reads or changes the others.

Reuse, not extension: ``_removed_endpoint`` already covers a deleted public
symbol and the three field rules cover its parameters, so this module writes no
breaking-change rule.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from repowise.core.workspace.repo_index import IndexedSymbol, RepoIndex, WorkspaceIndex

_log = logging.getLogger("repowise.workspace.code_api")

#: ``Contract.contract_type`` for everything this module produces.
CODE_CONTRACT_TYPE = "code"

#: Both sides are index-resolved, not regex-matched — the manifest and the
#: import edge each name their half outright.
_CONFIDENCE = 0.9

#: Manifest basenames handled here, mapped to the ecosystem they declare.
_MANIFESTS: dict[str, str] = {
    "package.json": "npm",
    "pyproject.toml": "pypi",
    "Cargo.toml": "cargo",
}

#: Not implemented, but counted, so the coverage figure names what it misses.
_UNSUPPORTED_MANIFESTS: dict[str, str] = {"go.mod": "go", "pom.xml": "maven"}

#: The bound ``external_systems._discover`` uses, so both see one manifest set.
_MANIFEST_DEPTH = 3

#: Kinds a consumer can import by name.
_EXPORTABLE_KINDS = frozenset({"function", "method", "class", "interface", "struct", "enum"})


@dataclass(frozen=True)
class PublishedPackage:
    """One package a repo ships, as its own manifest declares it."""

    name: str  # the manifest's declared name — what an importer writes
    ecosystem: str
    repo: str  # workspace alias
    manifest: str  # repo-relative POSIX path
    root: str  # repo-relative POSIX dir, "" at the repo root
    #: ``None`` where the ecosystem has no entry file and *root* is the surface.
    entry_files: frozenset[str] | None = None
    #: ``__all__`` names defined elsewhere in the package.
    reexported: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Manifest reading — one small function per ecosystem
# ---------------------------------------------------------------------------


def _rel(path: Path, repo_path: Path) -> str | None:
    try:
        return path.relative_to(repo_path).as_posix()
    except ValueError:
        return None


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
        if isinstance(value, str) and value.startswith("./"):
            targets.add(f"{root}/{value[2:]}" if root else value[2:])
        elif isinstance(value, dict):  # conditional exports: {"import": "./x.js"}
            for nested in value.values():
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
        poetry = data.get("tool", {}).get("poetry", {})
        name = poetry.get("name") if isinstance(poetry, dict) else None
    if not isinstance(name, str) or not name:
        return None
    prefix = f"{root}/" if root else ""
    dirs = _pypi_package_dirs(data)
    if not dirs:
        # PEP 503 allows `-`/`.` in a distribution name where the module needs
        # an identifier, and the two often differ beyond that. A guess, used
        # only when the build backend declares nothing.
        module = name.replace("-", "_").replace(".", "_")
        dirs = [f"src/{module}", module]
    return name, frozenset(f"{prefix}{d}/__init__.py" for d in dirs), frozenset()


def _pypi_package_dirs(data: dict[str, Any]) -> list[str]:
    """Package directories the build backend declares, newest layouts first."""
    dirs: list[str] = []
    hatch = data.get("tool", {}).get("hatch", {})
    wheel = hatch.get("build", {}).get("targets", {}).get("wheel", {}) if hatch else {}
    dirs.extend(p for p in wheel.get("packages", []) if isinstance(p, str))

    setuptools = data.get("tool", {}).get("setuptools", {})
    dirs.extend(p.replace(".", "/") for p in setuptools.get("packages", []) if isinstance(p, str))

    for entry in data.get("tool", {}).get("poetry", {}).get("packages", []):
        include = entry.get("include") if isinstance(entry, dict) else None
        if isinstance(include, str):
            source = entry.get("from")
            dirs.append(f"{source}/{include}" if isinstance(source, str) else include)
    return [d.strip("/") for d in dirs if d.strip("/")]


def _dunder_all(repo_path: Path, entry_files: frozenset[str]) -> frozenset[str]:
    """Names an entry ``__init__.py`` re-exports via ``__all__``.

    Python's entry file usually *declares* nothing — it is a wall of
    ``from .x import Y`` — so without this a distribution's surface reads as
    empty. Only plain string literals count; a computed ``__all__`` is refused
    whole rather than half-read, per the same rule the signature mapper follows.
    """
    import ast

    names: set[str] = set()
    for rel in entry_files:
        path = repo_path / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                continue
            if not isinstance(node.value, ast.List | ast.Tuple):
                continue
            names.update(
                el.value
                for el in node.value.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            )
    return frozenset(names)


def _cargo(text: str, root: str) -> tuple[str, frozenset[str]] | None:
    """``[package].name`` and the crate root, which is the whole public surface."""
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
    path = lib.get("path") if isinstance(lib, dict) else None
    entry = path if isinstance(path, str) else "src/lib.rs"
    return name, frozenset({f"{root}/{entry}" if root else entry})


def _nuget(project: Any, repo_path: Path, root: str) -> str | None:
    """The id a packable project publishes under, or None when it is not packable.

    Packability is opt-in, never assumed: an SDK-style project is packable by
    default, so treating silence as yes would make every internal project in a
    solution a published library.
    """
    if project.is_packable is False:
        return None
    if not (
        project.is_packable
        or project.generate_package_on_build
        or project.package_id is not None
    ):
        return None
    return project.package_id or project.assembly_name or project.path.stem


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def find_published_packages(
    alias: str, repo_path: Path
) -> tuple[list[PublishedPackage], dict[str, int]]:
    """Every package *repo_path* declares it publishes, plus skip counters."""
    from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
    from repowise.core.ingestion.resolvers.dotnet.msbuild import find_csproj_files, parse_csproj

    packages: list[PublishedPackage] = []
    counts: dict[str, int] = {}
    prune = PRUNED_DIRS | {"target", "dist", "build"}

    for dirpath, dirnames, filenames in walk_repo(repo_path, prune_dirs=prune):
        if len(dirpath.relative_to(repo_path).parts) >= _MANIFEST_DEPTH:
            dirnames[:] = []
        rel_dir = _rel(dirpath, repo_path)
        if rel_dir is None:
            continue
        root = "" if rel_dir == "." else rel_dir
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
            rel_manifest = _rel(manifest, repo_path)
            if rel_manifest is None:
                continue
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parsed = _read_manifest(ecosystem, text, root)
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
                )
            )

    for csproj in find_csproj_files(repo_path):
        project = parse_csproj(csproj)
        rel_manifest = _rel(csproj, repo_path) if project is not None else None
        if project is None or rel_manifest is None:
            continue
        root = rel_manifest.rsplit("/", 1)[0] if "/" in rel_manifest else ""
        package_id = _nuget(project, repo_path, root)
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
            )
        )
    return packages, counts


def _read_manifest(
    ecosystem: str, text: str, root: str
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
    cargo = _cargo(text, root)
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
        return candidates

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

    def for_repo(self, alias: str) -> list[Any]:
        return self.by_repo.get(alias, [])


def build_code_surface(
    repo_paths: dict[str, Path],
    workspace_index: WorkspaceIndex | None,
    exclude: Callable[[str], bool],
) -> CodeSurface:
    """Build every ``code`` contract in the workspace, providers before consumers.

    Workspace-wide by necessity, not by preference: a consumer contract only
    exists when the package it imports is published by *some* repo, so neither
    half can be decided inside a single repo's extraction.
    """
    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.extractors.from_index import EXTRACTION_LAYER_KEY, LAYER_INDEX

    surface = CodeSurface()
    if workspace_index is None:
        return surface

    published: list[PublishedPackage] = []
    for alias, repo_path in repo_paths.items():
        packages, counts = find_published_packages(alias, repo_path)
        published.extend(packages)
        if counts:
            surface.stats.setdefault(alias, {}).update(counts)

    # -- providers ---------------------------------------------------------
    by_name: dict[str, PublishedPackage] = {}
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
        rows = surface.by_repo.setdefault(package.repo, [])
        exported_here = surface.members.setdefault(package.name, set())
        seen: set[str] = set()
        for symbol in _surface_symbols(package, index, exclude):
            exported = _exported_name(symbol)
            if exported in seen:
                continue
            seen.add(exported)
            exported_here.add(exported)
            rows.append(
                Contract(
                    repo=package.repo,
                    contract_id=contract_id_for(package.name, exported),
                    contract_type=CODE_CONTRACT_TYPE,
                    role="provider",
                    file_path=symbol.file_path,
                    symbol_name=exported,
                    confidence=_CONFIDENCE,
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

    # -- consumers ---------------------------------------------------------
    for alias in repo_paths:
        index = workspace_index.get(alias)
        if index is None:
            continue
        rows = surface.by_repo.setdefault(alias, [])
        stats = surface.stats.setdefault(alias, {})
        seen_pairs: set[tuple[str, str, str]] = set()
        for edge in index.external_import_edges():
            package = _package_for(edge.external_name, by_name)
            if package is None:
                continue
            if package.repo == alias or exclude(edge.source_file):
                continue
            for name in edge.imported_names:
                for exported in _consumed_exports(name, surface.members[package.name]):
                    key = (package.name, exported, edge.source_file)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    rows.append(
                        Contract(
                            repo=alias,
                            contract_id=contract_id_for(package.name, exported),
                            contract_type=CODE_CONTRACT_TYPE,
                            role="consumer",
                            file_path=edge.source_file,
                            symbol_name=f"{package.name}:{exported}",
                            confidence=_CONFIDENCE,
                            meta={
                        "package": package.name,
                        "ecosystem": package.ecosystem,
                        EXTRACTION_LAYER_KEY: LAYER_INDEX,
                    },
                        )
                    )
        stats["code_consumer_imports"] = len(seen_pairs)

    _log.debug(
        "Code-API surface: %d package(s), %d contract(s)",
        len(by_name),
        sum(len(rows) for rows in surface.by_repo.values()),
    )
    return surface


def _package_for(
    external_name: str, by_name: dict[str, PublishedPackage]
) -> PublishedPackage | None:
    """Resolve ``external:<name>`` to a published package.

    An import target carries the subpath it reached (``@scope/ui/lib/format``),
    so the longest declared name that prefixes it wins — a shorter one would
    match ``@scope/ui`` for an import of an unrelated ``@scope/ui-icons``.
    """
    if external_name in by_name:
        return by_name[external_name]
    best: PublishedPackage | None = None
    for name, package in by_name.items():
        if external_name.startswith(f"{name}/") and (best is None or len(name) > len(best.name)):
            best = package
    return best


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
