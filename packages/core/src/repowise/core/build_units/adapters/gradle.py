"""Gradle projects from the resolver's :class:`JvmGradleIndex`.

A project's files are the indexed files under its source-set directories,
which may sit outside the project (``srcDirs("../shared/src")``). Deps are
the ``project(":x")`` arguments of configuration calls in the project's build
script (``implementation(project(":core"))``, ``api project(':api')``).
Ceiling: type-safe accessors (``projects.core``) and dependencies added by a
convention plugin are not read; reading Gradle's own model export is the
upgrade path.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.jvm_gradle import (
    JvmGradleProject,
    _read_build_script,
    get_or_build_jvm_gradle_index,
)

# ``<configuration>(project(":x"))`` or ``<configuration> project(path: ':x')``.
# A bare ``project(":x") { ... }`` configures a project and is no dependency.
_PROJECT_DEP_RE = re.compile(
    r"""(?<![\w.])[a-z]\w*\s*(?:\(\s*|\s+)project\s*\(\s*(?:path\s*[:=]\s*)?["'](:[^"']*)["']"""
)
_BUILD_SCRIPTS = ("build.gradle.kts", "build.gradle")
_SETTINGS = ("settings.gradle.kts", "settings.gradle")


def _project_path(project: JvmGradleProject) -> str:
    """Gradle's own project path: ``:`` for the root, ``:app:core`` for a module."""
    return ":" if project.id == "<root>" else f":{project.id}"


def _manifest(repo: Path, root: str) -> str:
    """The project's build script, else the settings script that declares it, else ``""``."""
    for name in _BUILD_SCRIPTS:
        if (repo / root / name).is_file():
            return f"{root}/{name}" if root else name
    return next((name for name in _SETTINGS if (repo / name).is_file()), "")


def _src_dirs(project: JvmGradleProject) -> set[str]:
    """Repo-relative source-set directories of *project* that sit inside the repo."""
    root = project.root_dir
    dirs = {
        posixpath.normpath(f"{root}/{src}" if root else src)
        for source_set in project.source_sets.values()
        for src in source_set.src_dirs
    }
    return {d for d in dirs if d != ".." and not d.startswith("../")}


def _unit(project: JvmGradleProject, repo: Path) -> BuildUnit:
    root = project.root_dir
    script = _read_build_script(repo / root)
    return BuildUnit(
        id=f"gradle:{_project_path(project)}",
        ecosystem="gradle",
        name=project.id.rsplit(":", 1)[-1] if root else ":",
        root=root,
        manifest=_manifest(repo, root),
        source="gradle",
        deps=tuple({f"gradle:{path}" for path in _PROJECT_DEP_RE.findall(script)}),
    )


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None:
        return Found([])
    index = get_or_build_jvm_gradle_index(ctx)
    # A projectDir override can point outside the repo; such a project is no unit here.
    projects = [p for p in index.projects.values() if not p.root_dir.startswith("../")]
    if not projects:
        return Found([])

    by_src_dir: dict[str, list[int]] = {}
    for i, project in enumerate(projects):
        for src_dir in _src_dirs(project):
            by_src_dir.setdefault(src_dir, []).append(i)

    # One pass over the paths: each file looks up the directories above it.
    listed: list[list[str]] = [[] for _ in projects]
    for path in ctx.sorted_paths:
        parts = path.split("/")
        dirs = ("/".join(parts[:depth]) for depth in range(1, len(parts)))
        for i in {i for d in dirs for i in by_src_dir.get(d, ())}:
            listed[i].append(path)
    claims = [
        (_unit(project, ctx.repo_path), tuple(files))
        for project, files in zip(projects, listed, strict=True)
    ]
    # The settings script, and a root build script beside it that configures
    # the listed projects, were read and are no unit of their own.
    root_scripts = (*_SETTINGS, *_BUILD_SCRIPTS)
    ruled_out = frozenset(name for name in root_scripts if (ctx.repo_path / name).is_file())
    return Found(claims, ruled_out)
