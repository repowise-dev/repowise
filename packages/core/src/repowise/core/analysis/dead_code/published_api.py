"""Public API a repository ships as a package.

A public symbol of a library that is packaged for others is used by code
outside the repository, so no importer inside it says nothing about whether it
is dead. Such a finding is not removed: it is kept below the default review
floor with a line naming the package, so a reader asking for every candidate
still sees it, labelled as published surface rather than as a cleanup item.

Only a build that declares the package counts, never a guess from layout. The
evidence is read per language from :data:`_PUBLISHED_ROOTS`, each entry
returning ``{project dir: package name}``; a file belongs to its innermost
project. Today that is .NET: a project that opts into packing (``IsPackable``,
``PackageId``, ``GeneratePackageOnBuild``), or one that keeps a public-API
baseline (``PublicAPI.Shipped.txt``, the analyzer file a library commits so
every change to its shipped surface is reviewed).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .models import DeadCodeFindingData, DeadCodeKind

#: Where a published symbol lands: under ``RISK_CAP_CONFIDENCE``, so the
#: default report and the stored index leave it out.
PUBLISHED_API_CONFIDENCE = 0.3

#: The public-API analyzer's baseline file, beside the project or in a folder
#: of its own (``.PublicAPI/``).
_API_BASELINE = "PublicAPI.Shipped.txt"


@dataclass(frozen=True)
class _BuildFacts:
    """What the per-language readers may consult."""

    repo_root: Path | None
    dotnet_index: Any | None


def _relative(path: Path, root: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return "" if rel == "." else rel


def _keeps_api_baseline(project_dir: Path) -> bool:
    try:
        folders = [project_dir, *(p for p in project_dir.iterdir() if p.is_dir())]
        return any((folder / _API_BASELINE).is_file() for folder in folders)
    except OSError:
        return False


def _dotnet_published(facts: _BuildFacts) -> dict[str, str]:
    """Project dirs of the .NET projects that ship a package."""
    index = facts.dotnet_index
    if index is None:
        return {}
    root = Path(index.repo_path)
    out: dict[str, str] = {}
    for project in index.projects.values():
        rel = _relative(project.project_dir, root)
        if rel is None:
            continue
        name = project.published_id
        if name is None and _keeps_api_baseline(project.project_dir):
            name = project.assembly_name or project.path.stem
        if name is not None:
            out[rel] = name
    return out


#: Language -> reader of the build files that declare a published package.
#: A language without an entry has no published-surface rule yet.
_PUBLISHED_ROOTS: dict[str, Callable[[_BuildFacts], Mapping[str, str]]] = {
    "csharp": _dotnet_published,
    "vbnet": _dotnet_published,
}


def _package_of(path: str, roots: Mapping[str, str]) -> str | None:
    """The package of the innermost published project enclosing *path*."""
    for parent in PurePosixPath(path).parents:
        name = roots.get("" if str(parent) == "." else parent.as_posix())
        if name is not None:
            return name
    return None


def demote_published_api(
    findings: list[DeadCodeFindingData],
    languages: Mapping[str, str],
    *,
    repo_root: Path | None = None,
    dotnet_index: Any | None = None,
) -> list[DeadCodeFindingData]:
    """Cap unused exports and unreachable files of a published package.

    *languages* maps each file that may hold published API to its language;
    the caller leaves out an unreachable file that declares no public type.
    Mutates in place and returns the same list.
    """
    facts = _BuildFacts(repo_root=repo_root, dotnet_index=dotnet_index)
    roots: dict[str, Mapping[str, str]] = {}
    for finding in _published_candidates(findings):
        language = languages.get(finding.file_path)
        reader = _PUBLISHED_ROOTS.get(language or "")
        if reader is None:
            continue
        if language not in roots:
            roots[language] = reader(facts)
        package = _package_of(finding.file_path, roots[language])
        if package is None:
            continue
        finding.confidence = min(finding.confidence, PUBLISHED_API_CONFIDENCE)
        finding.safe_to_delete = False
        finding.evidence.append(
            f"Public API of the published package '{package}': "
            "code outside this repository can use it"
        )
    return findings


def _published_candidates(findings: Iterable[DeadCodeFindingData]) -> Iterable[DeadCodeFindingData]:
    return (
        f
        for f in findings
        if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
    )
