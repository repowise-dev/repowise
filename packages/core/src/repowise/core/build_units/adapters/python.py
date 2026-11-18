"""Python distributions, one per ``pyproject.toml`` that names its distribution.

The name is ``[project] name``, else ``[tool.poetry] name``. A
``pyproject.toml`` naming none, and a ``setup.py`` or ``setup.cfg`` project,
is left to the manifest fallback, as is one that cannot be read (skipped with
a warning) and one whose name an earlier root already took. The id is the
PEP 503 name, so ``My_Pkg`` and ``my-pkg`` are one unit.

Deps are the in-repo distributions a project names: a ``[project]``
dependency or optional dependency (a ``name @ file:...`` entry included), a
Poetry dependency (``dev-dependencies`` included) or a uv ``workspace = true``
source, matched by name. A uv or Poetry ``path`` source is matched only by
the directory it points at, so one pointing outside the repo, or at a
directory holding no unit, links nothing; a uv source pinned to git, a URL or
an index names an outside distribution and links nothing either, whatever
the dependency list says, unless another of that name's marker-split sources
is ``workspace = true``. Ceiling: PEP 735 ``[dependency-groups]`` entries are
seen only through their uv source; reading the groups is the upgrade path.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from pathlib import Path

import structlog

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.adapters.manifest_roots import package_manifests
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.external_systems import _dist_key, pypi
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.workspace_members import manifest_package_name, read_toml_or_none

log = structlog.get_logger(__name__)

_MANIFEST = "pyproject.toml"


def _table_sources(data: dict) -> Iterator[tuple[str, dict]]:
    """``(name, spec)`` of every table-form uv source and Poetry dependency."""
    tool = data.get("tool")
    tool = tool if isinstance(tool, dict) else {}
    uv, poetry = tool.get("uv"), tool.get("poetry")
    tables = [uv.get("sources") if isinstance(uv, dict) else None]
    if isinstance(poetry, dict):
        # ``dev-dependencies`` is the pre-1.2 spelling of the dev group.
        tables.extend((poetry.get("dependencies"), poetry.get("dev-dependencies")))
        groups = poetry.get("group")
        if isinstance(groups, dict):
            tables.extend(g.get("dependencies") for g in groups.values() if isinstance(g, dict))
    for table in tables:
        if not isinstance(table, dict):
            continue
        for name, spec in table.items():
            # uv takes a list of sources split by markers.
            for one in spec if isinstance(spec, list) else [spec]:
                if isinstance(one, dict):
                    yield str(name), one


def _deps(manifest: Path, data: dict, repo: Path, root: str) -> tuple[set[str], set[str]]:
    """``python:<name>`` of each dependency *manifest* names, and the root each ``path`` source points at.

    *data* is the parsed *manifest*.
    """
    named = {_dist_key(record.name) for record in pypi.parse(manifest, repo)}
    # A name stays name-matched unless every source item for it (uv splits
    # one name's sources by marker) points somewhere a name cannot reach.
    workspace: set[str] = set()
    elsewhere: set[str] = set()
    paths: set[str] = set()
    for name, spec in _table_sources(data):
        key = _dist_key(name)
        path = spec.get("path")
        if isinstance(path, str):
            rel = posixpath.normpath(posixpath.join(root, path.replace("\\", "/")))
            paths.add("" if rel == "." else rel)
            elsewhere.add(key)
        elif spec.get("workspace") is True:
            workspace.add(key)
        elif any(field in spec for field in ("git", "url", "index")):
            elsewhere.add(key)
    keys = (named | workspace) - (elsewhere - workspace)
    return {f"python:{key}" for key in keys}, paths


def _read(manifest: Path, repo: Path, root: str) -> tuple[str, set[str], set[str]] | None:
    """*manifest*'s name and :func:`_deps`; None when it names nothing or cannot be read."""
    try:
        data = read_toml_or_none(manifest)
        if data is None:
            raise ValueError("not readable as TOML")
        name = manifest_package_name(manifest)
        return (name, *_deps(manifest, data, repo, root)) if name else None
    except Exception as exc:  # one unreadable pyproject must not cost the other units
        log.warning("build_units_pyproject_unreadable", manifest=str(manifest), error=str(exc))
        return None


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None or not any(path.endswith((".py", ".pyi")) for path in ctx.path_set):
        return Found([])
    repo = ctx.repo_path
    read: dict[str, tuple[str, set[str], set[str]]] = {}
    for root, names in sorted(package_manifests(ctx).items()):
        if _MANIFEST in names and (found := _read(repo / root / _MANIFEST, repo, root)):
            read[root] = found
    # A name taken twice keeps its first root in sorted order; the later
    # root is no unit here, so the fallback claims it, and a path source
    # pointing at it links nothing rather than the other package.
    by_root: dict[str, str] = {}
    taken: set[str] = set()
    for root, (name, _named, _paths) in read.items():
        uid = f"python:{_dist_key(name)}"
        if uid in taken:
            log.warning("build_units_duplicate_id", unit_id=uid, root=root)
            continue
        taken.add(uid)
        by_root[root] = uid

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for root, uid in by_root.items():
        name, named, paths = read[root]
        unit = BuildUnit(
            id=uid,
            ecosystem="python",
            name=name,
            root=root,
            manifest=f"{root}/{_MANIFEST}" if root else _MANIFEST,
            source="pyproject",
            deps=tuple(named | {by_root[path] for path in paths if path in by_root}),
        )
        out.append((unit, ()))
    return Found(out)
