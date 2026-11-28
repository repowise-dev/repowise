"""Which frameworks a repo's ``package.json`` files declare: the presence answer.

:func:`~repowise.core.ingestion.tech_stack.detect_tech_stack` reads it, and the
framework-edge handlers ask that stack through ``DetectionContext.uses``, so a
framework is present by one rule everywhere. Every production manifest counts,
nested ones included, so a Next.js or Nuxt app in a monorepo folder is present
the same as one at the root.
"""

from __future__ import annotations

from pathlib import Path

from .js import js_frameworks
from .npm import NpmManifest, npm_manifests

#: ``package.json`` dependency -> (stack display name, category).
NPM_STACK_NAMES: dict[str, tuple[str, str]] = {
    "next": ("Next.js", "framework"),
    "react": ("React", "framework"),
    "vue": ("Vue.js", "framework"),
    "svelte": ("Svelte", "framework"),
    "astro": ("Astro", "framework"),
    "@angular/core": ("Angular", "framework"),
    "express": ("Express", "framework"),
    "fastify": ("Fastify", "framework"),
    "hono": ("Hono", "framework"),
    "nestjs": ("NestJS", "framework"),
    "@nestjs/core": ("NestJS", "framework"),
    "prisma": ("Prisma", "database"),
    "@prisma/client": ("Prisma", "database"),
    "drizzle-orm": ("Drizzle ORM", "database"),
    "typeorm": ("TypeORM", "database"),
    "mongoose": ("Mongoose", "database"),
    "sequelize": ("Sequelize", "database"),
    "tailwindcss": ("Tailwind CSS", "framework"),
    "vite": ("Vite", "infra"),
    "webpack": ("Webpack", "infra"),
    "turbo": ("Turborepo", "infra"),
}


def convention_framework_names(manifest: NpmManifest) -> list[str]:
    """The :data:`~.js.JS_FRAMEWORKS` *manifest* declares (Nuxt, Remix, ...), by name."""
    return [facts.name for facts, _pkg in js_frameworks(manifest)]


def declared_frameworks(manifest: NpmManifest) -> list[str]:
    """Every framework *manifest* declares, by stack name, in a stable order.

    Frameworks only: a package's build tools and ORM (Vite, Prisma) are its
    own, not the repo's frameworks.
    """
    names = [
        display
        for dep, (display, category) in NPM_STACK_NAMES.items()
        if category == "framework" and dep in manifest.dependencies
    ]
    return list(dict.fromkeys([*names, *convention_framework_names(manifest)]))


def production_npm_manifests(repo_root: str | Path) -> list[NpmManifest]:
    """Every production ``package.json`` below the repo root.

    The package-root scan skips vendored trees, nested checkouts and ignored
    directories, and the repo's ``exclude_patterns`` drop what the index
    leaves out. A manifest in a test fixture, an example or a tooling folder
    is not the repo's, so only production origins (:mod:`~repowise.core.code_origin`)
    count. The root manifest is not included.
    """
    # Local imports: the package-root scan loads the traverser, which the
    # rest of this package never needs.
    from repowise.core.code_origin import path_origin
    from repowise.core.exclusion import build_exclude_spec, is_excluded

    from ..package_roots import scan_package_manifests

    spec = build_exclude_spec(repo_root)
    dirs = {
        rel
        for rel, names in scan_package_manifests(repo_root).items()
        if "package.json" in names
        and path_origin(f"{rel}/package.json") == "production"
        and not is_excluded(f"{rel}/package.json", spec)
    }
    if not dirs:
        return []
    # npm_manifests also reads every ancestor directory; keep only the chosen ones.
    paths = [f"{rel}/package.json" for rel in sorted(dirs)]
    return [m for m in npm_manifests(repo_root, paths) if m.rel_dir in dirs]


def nested_frameworks(repo_root: str | Path) -> list[str]:
    """Every framework a production ``package.json`` below the root declares."""
    names: dict[str, None] = {}
    for manifest in production_npm_manifests(repo_root):
        names.update(dict.fromkeys(declared_frameworks(manifest)))
    return list(names)
