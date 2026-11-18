"""Gradle projects from the resolver's :class:`JvmGradleIndex`.

A project's files are the indexed files under its source-set directories,
which may sit outside the project (``srcDirs("../shared/src")``). Deps are
the ``project(":x")`` arguments of configuration calls in the project's build
script (``implementation(project(":core"))``, ``api project(':api')``,
``"kspTest"(project(":codegen"))``). The root project is a unit whenever it
has a build script, also when settings lists modules, which is when the
resolver's index leaves it out: that index feeds source-set and publishing
stamps, and a root aggregator's script configures its subprojects
(``allprojects { ... }``), so its guesses must not become the root's own.
An edge declared inside a property-gated branch
(``when (testMode) { KSP -> "kspTest"(project(":x")) }``) is kept: an edge
any build mode declares counts, and a static reader does not evaluate
properties.
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

# ``<configuration>(project(":x"))``, ``<configuration> project(path: ':x')``,
# or a configuration named by a string (``"kspTest"(project(":x"))``). A bare
# ``project(":x") { ... }`` configures a project and is no dependency.
_PROJECT_DEP_RE = re.compile(
    r"""(?<![\w.])(?:[a-z]\w*|"[a-z]\w*"|'[a-z]\w*')\s*(?:\(\s*|\s+)"""
    r"""project\s*\(\s*(?:path\s*[:=]\s*)?["'](:[^"']*)["']"""
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


def _unit(project_path: str, root: str, repo: Path) -> BuildUnit:
    script = _read_build_script(repo / root)
    return BuildUnit(
        id=f"gradle:{project_path}",
        ecosystem="gradle",
        name=project_path.rsplit(":", 1)[-1] or ":",
        root=root,
        manifest=_manifest(repo, root),
        source="gradle",
        deps=tuple({f"gradle:{path}" for path in _PROJECT_DEP_RE.findall(script)}),
    )


def _listed_files(projects: list[JvmGradleProject], paths: tuple[str, ...]) -> list[list[str]]:
    """Each project's indexed files under its source-set directories, in *projects* order."""
    by_src_dir: dict[str, list[int]] = {}
    for i, project in enumerate(projects):
        for src_dir in _src_dirs(project):
            by_src_dir.setdefault(src_dir, []).append(i)
    # One pass over the paths: each file looks up the directories above it.
    listed: list[list[str]] = [[] for _ in projects]
    for path in paths:
        parts = path.split("/")
        dirs = ("/".join(parts[:depth]) for depth in range(1, len(parts)))
        for i in {i for d in dirs for i in by_src_dir.get(d, ())}:
            listed[i].append(path)
    return listed


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None:
        return Found([])
    index = get_or_build_jvm_gradle_index(ctx)
    # A projectDir override can point outside the repo; such a project is no unit here.
    projects = [p for p in index.projects.values() if not p.root_dir.startswith("../")]
    if not projects:
        return Found([])

    listed = _listed_files(projects, ctx.sorted_paths)
    claims = [
        (_unit(_project_path(project), project.root_dir, ctx.repo_path), tuple(files))
        for project, files in zip(projects, listed, strict=True)
    ]
    has_root_script = any((ctx.repo_path / name).is_file() for name in _BUILD_SCRIPTS)
    if has_root_script and all(project.root_dir for project in projects):
        claims.append((_unit(":", "", ctx.repo_path), ()))
    # The settings script was read and is no unit of its own.
    ruled_out = frozenset(name for name in _SETTINGS if (ctx.repo_path / name).is_file())
    return Found(claims, ruled_out)
