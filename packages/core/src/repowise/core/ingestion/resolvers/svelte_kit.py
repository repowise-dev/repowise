"""SvelteKit ``$lib`` alias resolution.

SvelteKit projects import shared code as ``$lib/...``. The alias is not
declared in a tsconfig a repo checks in — it lives in the generated
``.svelte-kit/tsconfig.json``, which is a build artifact and is almost always
gitignored. Without this module every ``$lib`` import in a SvelteKit app
resolves to an ``external:`` node, so the whole ``src/lib`` tree looks
unreachable.

A project is a ``package.json`` that :data:`~..framework_facts.SVELTEKIT`
matches, and ``$lib`` is the directory :func:`~..framework_facts.sveltekit_lib_dir`
reads from its ``svelte.config`` (``kit.files.lib``, default ``src/lib``). A
monorepo can hold several SvelteKit apps, so projects are matched
longest-prefix-first against the importing file.

The other ``$`` specifiers (``$app/*``, ``$env/*``, ``$service-worker``) are
virtual modules the framework synthesises at build time — they have no file in
the repo, so they correctly stay external and are not handled here.

Ceiling: a ``kit.files.lib`` that is computed rather than written as a literal
keeps the default directory.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from ..framework_facts import SVELTEKIT, npm_manifests, sveltekit_lib_dir

if TYPE_CHECKING:
    from .context import ResolverContext

_LIB_ALIAS = "$lib"
# Files a SvelteKit app holds, whose directories are asked for a package.json.
_APP_SUFFIXES = (".svelte", ".ts", ".js", ".mjs", ".mts", ".cjs", ".cts", ".tsx", ".jsx")


def _project_lib_dirs(ctx: ResolverContext) -> tuple[tuple[str, str], ...]:
    """(project_dir, lib_dir) for every SvelteKit project, longest-first."""
    cached = getattr(ctx, "_svelte_lib_dirs", None)
    if cached is not None:
        return cached

    projects: list[tuple[str, str]] = []
    if ctx.repo_path is not None:
        app_paths = [p for p in ctx.sorted_paths if p.endswith(_APP_SUFFIXES)]
        projects = [
            (manifest.rel_dir, sveltekit_lib_dir(manifest))
            for manifest in npm_manifests(ctx.repo_path, app_paths)
            if SVELTEKIT.matches_npm(manifest)
        ]

    projects.sort(key=lambda pair: len(pair[0]), reverse=True)
    result = tuple(projects)
    ctx._svelte_lib_dirs = result  # type: ignore[attr-defined]
    return result


def resolve_lib_alias(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve ``$lib/foo/bar`` to a repo-relative file, or None."""
    if module_path != _LIB_ALIAS and not module_path.startswith(_LIB_ALIAS + "/"):
        return None

    subpath = module_path[len(_LIB_ALIAS) :].lstrip("/")

    for project_dir, lib_dir in _project_lib_dirs(ctx):
        if project_dir and not importer_path.startswith(project_dir + "/"):
            continue
        base = posixpath.join(lib_dir, subpath) if subpath else lib_dir
        if base in ctx.path_set:
            return base
        for ext in (
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mts",
            ".mjs",
            ".svelte",
            "/index.ts",
            "/index.js",
            "/index.svelte",
        ):
            candidate = base + ext
            if candidate in ctx.path_set:
                return candidate
    return None
