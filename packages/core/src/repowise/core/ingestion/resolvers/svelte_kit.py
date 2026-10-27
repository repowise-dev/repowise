"""SvelteKit ``$lib`` alias resolution.

SvelteKit projects import shared code as ``$lib/...``. The alias is not
declared in a tsconfig a repo checks in — it lives in the generated
``.svelte-kit/tsconfig.json``, which is a build artifact and is almost always
gitignored. Without this module every ``$lib`` import in a SvelteKit app
resolves to an ``external:`` node, so the whole ``src/lib`` tree looks
unreachable.

The alias is a fixed convention: ``$lib`` -> ``<project>/src/lib``, where
``<project>`` is the directory holding ``svelte.config.js``. A monorepo can
hold several SvelteKit apps, so projects are matched longest-prefix-first
against the importing file.

The other ``$`` specifiers (``$app/*``, ``$env/*``, ``$service-worker``) are
virtual modules the framework synthesises at build time — they have no file in
the repo, so they correctly stay external and are not handled here.

Ceiling: ``kit.files.lib`` can relocate the directory in ``svelte.config.js``.
Reading it back would mean evaluating JS, so the convention default is used.
A project that moves ``lib`` falls back to ``external:`` exactly as today.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import ResolverContext

_CONFIG_NAMES = ("svelte.config.js", "svelte.config.ts", "svelte.config.mjs")

_LIB_ALIAS = "$lib"
_DEFAULT_LIB_DIR = "src/lib"


def _project_lib_dirs(ctx: ResolverContext) -> tuple[tuple[str, str], ...]:
    """(project_dir, lib_dir) for every SvelteKit project, longest-first."""
    cached = getattr(ctx, "_svelte_lib_dirs", None)
    if cached is not None:
        return cached

    projects: list[tuple[str, str]] = []
    for path in ctx.sorted_paths:
        name = path.rsplit("/", 1)[-1]
        if name not in _CONFIG_NAMES:
            continue
        project_dir = path[: -(len(name) + 1)] if "/" in path else ""
        lib_dir = posixpath.join(project_dir, _DEFAULT_LIB_DIR) if project_dir else _DEFAULT_LIB_DIR
        projects.append((project_dir, lib_dir))

    # A repo can ship a SvelteKit app with no svelte.config.js at the root of
    # a plain ``src/lib`` layout; fall back to the repo root so single-app
    # checkouts still resolve.
    if not projects and any(p.startswith(_DEFAULT_LIB_DIR + "/") for p in ctx.path_set):
        projects.append(("", _DEFAULT_LIB_DIR))

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
