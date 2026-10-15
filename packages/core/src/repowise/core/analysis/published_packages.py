"""Which project of a repository ships a package, read from its build files.

Two readers share this: the dead-code pass, which keeps an unused export of a
published package below the review floor, and the per-symbol ``public_api``
flag (:mod:`repowise.core.analysis.public_api`), which marks that same surface
on the graph. They read one answer, so they cannot disagree about what ships.

Only a build that declares the package counts, never a guess from layout. The
evidence is read per language from :data:`PUBLISHED_ROOTS`, each entry
returning ``{project dir: package name, or None when that project publishes
nothing}``. A file belongs to its innermost project, and only that project's
own build decides: a sample or test project nested in a packaged library's
folder is not part of the package. Today that is .NET: a project that opts into
packing (``IsPackable``, ``PackageId``, ``GeneratePackageOnBuild``), or one that
keeps a public-API baseline (``PublicAPI.Shipped.txt``, the analyzer file a
library commits so every change to its shipped surface is reviewed).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

#: The public-API analyzer's baseline file, beside the project or in a folder
#: of its own (``.PublicAPI/``).
API_BASELINE = "PublicAPI.Shipped.txt"


@dataclass(frozen=True)
class BuildFacts:
    """What the per-language readers may consult; a new language adds a field."""

    repo_root: Path | None
    dotnet_index: Any | None


def _relative(path: Path, root: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return "" if rel == "." else rel


def keeps_api_baseline(project_dir: Path) -> bool:
    try:
        folders = [project_dir, *(p for p in project_dir.iterdir() if p.is_dir())]
        return any((folder / API_BASELINE).is_file() for folder in folders)
    except OSError:
        return False


def dotnet_published(facts: BuildFacts) -> dict[str, str | None]:
    """Every .NET project dir, mapped to the package it ships or None."""
    index = facts.dotnet_index
    if index is None:
        return {}
    root = Path(index.repo_path)
    out: dict[str, str | None] = {}
    for project in index.projects.values():
        rel = _relative(project.project_dir, root)
        if rel is None:
            continue
        name = project.published_id
        if name is None and keeps_api_baseline(project.project_dir):
            name = project.assembly_name or project.path.stem
        out[rel] = name
    return out


#: Language -> reader of the build files that declare a published package.
#: A language without an entry has no published-surface rule yet.
PUBLISHED_ROOTS: dict[str, Callable[[BuildFacts], Mapping[str, str | None]]] = {
    "csharp": dotnet_published,
    "vbnet": dotnet_published,
}


def package_of(path: str, roots: Mapping[str, str | None]) -> str | None:
    """The package of the innermost project enclosing *path*, if it ships one."""
    for parent in PurePosixPath(path).parents:
        key = "" if str(parent) == "." else parent.as_posix()
        if key in roots:
            return roots[key]
    return None
