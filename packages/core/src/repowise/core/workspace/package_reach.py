"""Which files of a workspace package another repo's imports actually reach.

A manifest dependency says one repo uses another's package; it names no file.
This places the dependent's own ``imports`` edges on the provider's files (a
Python module path, an npm ``exports`` subpath), then follows the provider's
re-exports (``export *``, ``from .x import Y`` in an ``__init__``) to the file
that declares each imported name. A file outside that set is not reachable by
package import, which is what get_risk's ``affected_repos`` and the dead-code
cross-repo cut both decide on through :func:`package_link_reaches`.
"""

from __future__ import annotations

import json
import logging
import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from .code_api import PublishedPackage
    from .manifests import CrossRepoPackageDep
    from .repo_index import ExternalImport, RepoIndex

_log = logging.getLogger("repowise.workspace.package_reach")

#: The imported-name marker an ``export *`` / star-import edge carries.
_STAR = "*"


def package_link_reaches(link: Mapping[str, Any], file_path: str) -> bool:
    """Whether a package dependency *link* reaches *file_path* in the provider repo.

    The one rule for a file-level cross-repo claim from a package link. With
    ``reached_files`` recorded, only those files count. Without it (an overlay
    written before reach was recorded, or an import this module cannot place)
    every file inside the package's manifest directory counts.
    """
    reached = link.get("reached_files")
    if reached is not None:
        return file_path in reached
    manifest = link.get("target_manifest")
    if not manifest:
        return True
    root = posixpath.dirname(manifest)
    return not root or file_path.startswith(root + "/")


@dataclass
class ProviderGraph:
    """The provider repo's files, internal import edges and declared names."""

    files: frozenset[str]
    #: file -> [(imported file, names the edge brings in)]
    imports: Mapping[str, list[tuple[str, frozenset[str]]]] = field(default_factory=dict)
    declared: Mapping[str, frozenset[str]] = field(default_factory=dict)
    _py_modules: dict[str, list[str]] | None = None

    def py_module_files(self, dotted: str) -> list[str]:
        """Files a Python module path names (``a/b.py`` or ``a/b/__init__.py``)."""
        if self._py_modules is None:
            self._py_modules = _py_module_index(self.files)
        return self._py_modules.get(dotted.replace(".", "/"), [])


def _py_module_index(files: Iterable[str]) -> dict[str, list[str]]:
    """Every path-boundary suffix of each ``.py`` file's module path, to its files."""
    index: dict[str, list[str]] = defaultdict(list)
    for path in files:
        if not path.endswith(".py"):
            continue
        stem = path[:-3]
        if stem.endswith("/__init__"):
            stem = stem[: -len("/__init__")]
        parts = stem.split("/")
        for i in range(len(parts)):
            index["/".join(parts[i:])].append(path)
    return index


def _inside(path: str, root: str) -> bool:
    return not root or path.startswith(root + "/")


def _python_targets(
    edge: ExternalImport, package: PublishedPackage, graph: ProviderGraph
) -> list[tuple[str, frozenset[str] | None]] | None:
    """The module the edge imports, plus each imported name that is a submodule."""
    out: list[tuple[str, frozenset[str] | None]] = []
    # `import a.b as b` records the module's own name: it is the module, not a member.
    names = set(edge.imported_names) - {_STAR, edge.external_name.rsplit(".", 1)[-1]}
    for name in edge.imported_names:
        subs = [
            f for f in graph.py_module_files(f"{edge.external_name}.{name}")
            if _inside(f, package.root)
        ]
        out.extend((sub, None) for sub in subs)
        if subs:
            names.discard(name)  # the submodule, not a name the parent must route
    files = [f for f in graph.py_module_files(edge.external_name) if _inside(f, package.root)]
    out.extend((f, frozenset(names) or None) for f in files)
    return out or None


def _npm_subpath(specifier: str, package: PublishedPackage) -> str | None:
    """The subpath after the package name (``""`` for the bare name), or None."""
    for name in package.import_names:
        if specifier == name or specifier.startswith(f"{name}/"):
            return specifier[len(name) :].lstrip("/")
    return None


def _first_file(targets: Iterable[str], root: str, files: frozenset[str]) -> str | None:
    """The first ``exports`` target that is an indexed provider file."""
    for target in targets:
        rel = target[2:] if target.startswith("./") else target
        path = posixpath.normpath(f"{root}/{rel}" if root else rel)
        if path in files:
            return path
    return None


def _npm_targets(
    edge: ExternalImport,
    package: PublishedPackage,
    graph: ProviderGraph,
    exports_map: dict[str, tuple[str, ...]],
) -> list[tuple[str, frozenset[str] | None]] | None:
    """The file an ``exports`` key (or an entry file, for the bare name) maps the specifier to."""
    from repowise.core.ingestion.resolvers.ts_workspace.exports import _match_export_key

    sub = _npm_subpath(edge.external_name, package)
    if sub is None:
        return None
    targets = _match_export_key(sub, exports_map) if exports_map else None
    if not targets and not sub:
        targets = tuple(package.entry_files or ())
    path = _first_file(targets or (), package.root, graph.files)
    if path is None:
        return None
    return [(path, frozenset(edge.imported_names) - {_STAR} or None)]


def _forward(
    path: str, pending: set[str], graph: ProviderGraph
) -> tuple[list[tuple[str, frozenset[str], bool]], bool]:
    """Where *path* sends each pending name, and whether some name has no route.

    Each hop says whether an edge named its names (True) or only an
    ``export *`` passed them on, where most targets lack any one name. A name
    with no declaration and no edge (a renamed re-export, a lazy
    ``__getattr__`` map) has no known route.
    """
    hops = []
    routed: set[str] = set()
    for target, edge_names in graph.imports.get(path, []):
        star = _STAR in edge_names
        passed = pending if star else pending & edge_names
        if passed:
            hops.append((target, frozenset(passed), not star))
            routed |= passed
    return hops, bool(pending - routed)


def _follow(starts: Iterable[tuple[str, frozenset[str] | None]], graph: ProviderGraph) -> set[str]:
    """Each start file plus the files its re-exports forward the imported names to.

    A name the file declares stops there. ``None`` names (a whole-module import)
    forward every name the file imports, one hop, then follow as usual. In a
    package entry (``__init__.py``, ``index.*``) a name an import or edge named,
    with no known route onward (a lazy ``__getattr__`` map, a renamed
    re-export), reaches every file in its directory, so a missed route
    over-reaches instead of reading as unused. In any other module such a name
    is one the index keeps no symbol for (a module-level variable), so the
    module itself is the answer.
    """
    reached: set[str] = set()
    seen: set[tuple[str, str]] = set()
    queue: list[tuple[str, frozenset[str] | None, bool]] = [(p, n, True) for p, n in starts]
    while queue:
        path, names, named = queue.pop()
        reached.add(path)
        if names is None:
            queue.extend(
                (target, (edge_names - {_STAR}) or None, True)
                for target, edge_names in graph.imports.get(path, [])
                if target not in reached
            )
            continue
        pending = {n for n in names - graph.declared.get(path, frozenset()) if (path, n) not in seen}
        seen.update((path, n) for n in pending)
        hops, unrouted = _forward(path, pending, graph)
        queue.extend(hops)
        if unrouted and named and _is_package_entry(path):
            reached |= _same_directory(path, graph.files)
    return reached


def _is_package_entry(path: str) -> bool:
    name = posixpath.basename(path)
    return name == "__init__.py" or name.split(".", 1)[0] == "index"


def _same_directory(path: str, files: frozenset[str]) -> set[str]:
    """Every file under *path*'s directory."""
    prefix = posixpath.dirname(path)
    return {f for f in files if _inside(f, prefix)}


def reached_files(
    imports: Iterable[ExternalImport],
    package: PublishedPackage,
    graph: ProviderGraph,
    exports_map: dict[str, tuple[str, ...]] | None = None,
) -> list[str] | None:
    """The provider files *imports* reach in *package*, or None when that is unknown.

    Unknown is an import that cannot be placed, or no import of the package at
    all (a dependency used another way). None is the safe answer: the caller
    then falls back to the whole package.
    """
    from .code_api import _package_for

    by_import = dict.fromkeys(package.import_names, package)
    starts: list[tuple[str, frozenset[str] | None]] = []
    for edge in imports:
        if _package_for(edge.external_name, by_import) is None:
            continue
        if package.ecosystem == "pypi":
            placed = _python_targets(edge, package, graph)
        elif package.ecosystem == "npm":
            placed = _npm_targets(edge, package, graph, exports_map or {})
        else:
            placed = None
        if placed is None:
            return None
        starts.extend(placed)
    return sorted(_follow(starts, graph)) if starts else None


# ---------------------------------------------------------------------------
# Loading from the repo indexes
# ---------------------------------------------------------------------------


async def _provider_graph(index: RepoIndex) -> ProviderGraph:
    from sqlalchemy import select

    from repowise.core.persistence.models import GraphEdge, GraphNode

    from .repo_index import EXTERNAL_PREFIX

    session = index.session
    files = frozenset(
        (
            await session.execute(
                select(GraphNode.node_id).where(
                    GraphNode.repository_id == index.repo_id, GraphNode.node_type == "file"
                )
            )
        ).scalars()
    )
    rows = await session.execute(
        select(
            GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.imported_names_json
        ).where(
            GraphEdge.repository_id == index.repo_id,
            GraphEdge.edge_type == "imports",
            GraphEdge.target_node_id.not_like(f"{EXTERNAL_PREFIX}%"),
        )
    )
    imports: dict[str, list[tuple[str, frozenset[str]]]] = defaultdict(list)
    for source, target, names_json in rows:
        try:
            names = json.loads(names_json or "[]")
        except ValueError:
            names = []
        if not isinstance(names, list):
            names = []
        imports[source].append((target, frozenset(n for n in names if isinstance(n, str))))
    declared = {path: frozenset(s.name for s in index.symbols_for_file(path)) for path in imports}
    return ProviderGraph(files=files, imports=imports, declared=declared)


@dataclass
class _Provider:
    """One provider repo: its graph, the packages it publishes, and where it lives."""

    repo_path: Path
    graph: ProviderGraph
    packages: Mapping[str, PublishedPackage]  # by manifest path

    def reach(self, manifest: str, imports: Iterable[ExternalImport]) -> list[str] | None:
        package = self.packages.get(manifest)
        if package is None:
            return None
        exports = _exports_map(self.repo_path, manifest) if package.ecosystem == "npm" else None
        return reached_files(imports, package, self.graph, exports)


def _exports_map(repo_path: Path, manifest: str) -> dict[str, tuple[str, ...]]:
    from repowise.core.ingestion.resolvers.ts_workspace.exports import _build_exports_map

    try:
        data = json.loads((repo_path / manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return _build_exports_map(data) if isinstance(data, dict) else {}


async def _open_indexes(
    aliases: Iterable[str], repo_paths: dict[str, Path], into: dict[str, RepoIndex]
) -> None:
    """Open each alias's index into *into*, so the caller closes what opened before a failure."""
    from .repo_index import open_repo_index

    for alias in sorted(aliases):
        if alias in repo_paths and (index := await open_repo_index(alias, repo_paths[alias])):
            into[alias] = index


async def attach_reached_files(
    package_deps: list[CrossRepoPackageDep], repo_paths: dict[str, Path]
) -> None:
    """Set ``reached_files`` on each dependency whose imports can all be placed.

    Opens only the repos the dependencies name; a repo without an index leaves
    its dependencies unset, which reads as the whole package.
    """
    from .code_api import find_published_packages

    if not package_deps:
        return
    providers_named = {d.target_repo for d in package_deps}
    indexes: dict[str, RepoIndex] = {}
    try:
        await _open_indexes(
            providers_named | {d.source_repo for d in package_deps}, repo_paths, indexes
        )
        providers = {
            alias: _Provider(
                repo_paths[alias],
                await _provider_graph(indexes[alias]),
                {p.manifest: p for p in find_published_packages(alias, repo_paths[alias])[0]},
            )
            for alias in sorted(providers_named & indexes.keys())
        }
        for dep in package_deps:
            consumer, provider = indexes.get(dep.source_repo), providers.get(dep.target_repo)
            if consumer is not None and provider is not None:
                dep.reached_files = provider.reach(
                    dep.target_manifest, consumer.external_import_edges()
                )
    except Exception:
        # Reach only sharpens package links; failing here must not lose the
        # co-change and manifest results the overlay already holds.
        _log.warning("Package reach skipped; package links read as whole packages", exc_info=True)
    finally:
        for index in indexes.values():
            await index.close()
