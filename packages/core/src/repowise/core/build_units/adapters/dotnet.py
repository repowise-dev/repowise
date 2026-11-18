""".NET projects from the resolver's :class:`DotNetProjectIndex`."""

from __future__ import annotations

from pathlib import Path

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.dotnet import get_or_build_index

# The index buckets only these files into projects; without one there is no
# .NET code to own, and building the index would walk the repo for nothing.
_SOURCE_SUFFIXES = (".cs", ".vb")


def _rel(path: Path, repo: Path) -> str | None:
    """*path* as a repo-relative POSIX path (``""`` for the root), or None outside the repo."""
    try:
        rel = path.relative_to(repo).as_posix()
    except ValueError:
        return None
    return "" if rel == "." else rel


def _listed_files(
    file_to_project: dict[Path, Path], repo: Path, path_set: set[str]
) -> dict[Path, list[str]]:
    """Each project's indexed source files, repo-relative."""
    listed: dict[Path, list[str]] = {}
    for file, project in file_to_project.items():
        rel = _rel(file, repo)
        if rel is not None and rel in path_set:
            listed.setdefault(project, []).append(rel)
    return listed


def units(ctx: ResolverContext) -> Found:
    if not any(path.endswith(_SOURCE_SUFFIXES) for path in ctx.path_set):
        return Found([])
    index = get_or_build_index(ctx)
    if index is None:
        return Found([])
    repo = index.repo_path  # resolved, like every path the index holds
    listed = _listed_files(index.file_to_project, repo, ctx.path_set)
    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for path, project in index.projects.items():
        manifest = _rel(path, repo)
        root = _rel(project.project_dir, repo)
        if manifest is None or root is None:
            continue  # a solution entry outside the repo
        refs = (_rel(ref, repo) for ref in index.project_refs_by_proj.get(path, ()))
        unit = BuildUnit(
            id=f"dotnet:{manifest}",
            ecosystem="dotnet",
            name=project.assembly_name or path.stem,
            root=root,
            manifest=manifest,
            source="msbuild",
            deps=tuple(f"dotnet:{ref}" for ref in refs if ref is not None),
        )
        out.append((unit, tuple(sorted(listed.get(path, ())))))
    return Found(out)
