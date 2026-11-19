"""PHP packages from the resolver's ``repo_composer_manifests``, linked by ``path`` repositories.

One unit per readable ``composer.json``: the id is ``php:<composer name>``
(``php:<dir>`` when the manifest names none). A dep is added when a
manifest's ``require``/``require-dev`` names a package that lives at one of
its ``path`` repositories and that directory holds an in-repo manifest, so
``php:acme/app`` with a path repository at ``../lib`` depends on
``php:acme/lib``. A glob url (``../packages/*``) matches every in-repo
package under it, one directory level per ``*``; a url outside the repo
links nothing. A manifest that fails to parse never reaches this reader, so
the manifest fallback claims its root. A repo with no PHP file and no root
manifest costs one cheap check over ``ctx.path_set``.

Ceiling: only ``path`` repositories link in-repo packages; a ``vcs`` or
``artifact`` repository naming a package of this repo is not followed, and a
path repository whose package has no ``name`` cannot be required by name.
Reading the installed ``vendor/`` tree is the upgrade path.
"""

from __future__ import annotations

import fnmatch
import posixpath

import structlog

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.composer import ComposerManifest, repo_composer_manifests
from repowise.core.ingestion.resolvers.context import ResolverContext

log = structlog.get_logger(__name__)

_MANIFEST = "composer.json"


def _unit_id(manifest: ComposerManifest) -> str:
    """``php:<composer name>``, or ``php:<dir>`` when the manifest names none."""
    return f"php:{manifest.name or manifest.rel_dir or '.'}"


def _manifest_path(rel_dir: str) -> str:
    return f"{rel_dir}/{_MANIFEST}" if rel_dir else _MANIFEST


def _resolve(url: str, rel_dir: str) -> str:
    """*url* (a path repository, relative to *rel_dir*) as a repo-relative dir."""
    joined = posixpath.normpath(posixpath.join(rel_dir, url.replace("\\", "/")))
    return "" if joined == "." else joined


def _glob_match(pattern: str, path: str) -> bool:
    """Whether *path* matches the ``*``/``?`` pattern, one directory level per ``*``."""
    pattern_parts, parts = pattern.split("/"), path.split("/")
    if len(pattern_parts) != len(parts):
        return False
    return all(
        fnmatch.fnmatchcase(part, pat) for part, pat in zip(parts, pattern_parts, strict=True)
    )


def _repository_dirs(manifest: ComposerManifest, by_dir: set[str]) -> set[str]:
    """The in-repo manifest dirs *manifest*'s path repositories point at.

    A glob url matches every in-repo manifest dir under it; a plain url
    matches only its own directory. A url outside the repo links nothing.
    """
    dirs: set[str] = set()
    for url in manifest.path_repositories:
        target = _resolve(url, manifest.rel_dir)
        if "*" in target or "?" in target:
            dirs.update(d for d in by_dir if _glob_match(target, d))
        elif target in by_dir:
            dirs.add(target)
    return dirs


def _dep_ids(manifest: ComposerManifest, by_name: dict[str, str], reachable: set[str]) -> set[str]:
    """``php:<name>`` for each required package living at a path repository."""
    ids: set[str] = set()
    for name in manifest.requires:
        if by_name.get(name) in reachable:
            ids.add(f"php:{name}")
    return ids


def _keep_first(manifests: list[ComposerManifest]) -> dict[str, ComposerManifest]:
    """*manifests* by root, the first root in sorted order per unit id.

    A later root sharing an id is no unit here, so the manifest fallback
    claims it instead.
    """
    kept: dict[str, ComposerManifest] = {}
    taken: set[str] = set()
    for manifest in sorted(manifests, key=lambda m: m.rel_dir):
        uid = _unit_id(manifest)
        if uid in taken:
            log.warning("build_units_duplicate_id", unit_id=uid, root=manifest.rel_dir)
            continue
        taken.add(uid)
        kept[manifest.rel_dir] = manifest
    return kept


def _claims(kept: dict[str, ComposerManifest]) -> list[tuple[BuildUnit, tuple[str, ...]]]:
    """One claim per kept manifest, with its path-repository deps."""
    by_name = {m.name: d for d, m in kept.items() if m.name}
    by_dir = set(kept)
    claims: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for rel_dir, manifest in kept.items():
        reachable = _repository_dirs(manifest, by_dir)
        claims.append(
            (
                BuildUnit(
                    id=_unit_id(manifest),
                    ecosystem="php",
                    name=manifest.name or rel_dir or ".",
                    root=rel_dir,
                    manifest=_manifest_path(rel_dir),
                    source="composer",
                    deps=tuple(sorted(_dep_ids(manifest, by_name, reachable))),
                ),
                (),
            )
        )
    return claims


def _has_php(ctx: ResolverContext) -> bool:
    """Whether the repo holds a PHP source file or a root composer manifest."""
    return "composer.json" in ctx.path_set or any(path.endswith(".php") for path in ctx.path_set)


def units(ctx: ResolverContext) -> Found:
    if not _has_php(ctx):
        return Found([])
    manifests = repo_composer_manifests(ctx)
    if not manifests:
        return Found([])
    return Found(_claims(_keep_first(manifests)))
