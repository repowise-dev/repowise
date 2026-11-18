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


def units(ctx: ResolverContext) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    if ctx.repo_path is None:
        return []
    index = get_or_build_jvm_gradle_index(ctx)
    if not index.projects:
        return []
    repo = ctx.repo_path

    found: list[tuple[BuildUnit, list[str]]] = []
    by_src_dir: dict[str, list[int]] = {}
    for project in index.projects.values():
        root = project.root_dir
        if root.startswith(".."):
            continue  # a projectDir override pointing outside the repo
        manifest = next(
            (
                f"{root}/{name}" if root else name
                for name in _BUILD_SCRIPTS
                if (repo / root / name).is_file()
            ),
            next((name for name in _SETTINGS if (repo / name).is_file()), ""),
        )
        script = _read_build_script(repo / root)
        deps = {f"gradle:{path}" for path in _PROJECT_DEP_RE.findall(script)}
        unit = BuildUnit(
            id=f"gradle:{_project_path(project)}",
            ecosystem="gradle",
            name=project.id.rsplit(":", 1)[-1] if root else (repo.resolve().name or project.id),
            root=root,
            manifest=manifest,
            source="gradle",
            deps=tuple(deps),
        )
        for source_set in project.source_sets.values():
            for src in source_set.src_dirs:
                src_dir = posixpath.normpath(f"{root}/{src}" if root else src)
                if not src_dir.startswith(".."):
                    by_src_dir.setdefault(src_dir, []).append(len(found))
        found.append((unit, []))

    # One pass over the paths: each file looks up the directories above it.
    for path in ctx.sorted_paths:
        parts = path.split("/")
        hits = {i for depth in range(1, len(parts)) for i in by_src_dir.get("/".join(parts[:depth]), ())}
        for i in hits:
            found[i][1].append(path)
    return [(unit, tuple(files)) for unit, files in found]
