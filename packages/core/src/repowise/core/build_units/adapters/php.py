"""PHP packages from Composer manifests with path repositories.

Only ``{"type": "path"}`` repositories are read (no ``vcs``, ``artifact`` or
Packagist resolution, so a package required by name without a path repository
links nothing); manifests are the ones :func:`find_composer_manifests` finds
(up to three levels deep, skipping vendor/test/example trees); glob braces
``{a,b}`` are not expanded; running ``composer show --tree`` / reading
``composer.lock`` is the upgrade path.
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

_COMPOSER_JSON = "composer.json"


def _unit_id(name: str, rel_dir: str) -> str:
    """Build a ``php:<name>`` or ``php:<dir>`` unit id."""
    if name:
        return f"php:{name}"
    return f"php:{rel_dir}" if rel_dir else "php:."


def _manifest_path(rel_dir: str) -> str:
    """The repo-relative manifest path for a given directory."""
    return f"{rel_dir}/{_COMPOSER_JSON}" if rel_dir else _COMPOSER_JSON


def _resolve_url(base: str, url: str) -> str | None:
    """Resolve a path-repository *url* against *base* (the manifest's directory).

    Returns the normalized repo-relative path, or ``None`` when it points
    outside the repository (``..`` or starting with ``../`` or absolute).
    """
    normalized = url.replace("\\", "/").rstrip("/")
    joined = posixpath.normpath(posixpath.join(base, normalized))
    if joined == ".":
        return ""
    if joined == ".." or joined.startswith("../") or posixpath.isabs(joined):
        return None
    return joined


def _matches(pattern: str, root: str) -> bool:
    """Whether in-repo *root* is what a path repository *pattern* names.

    ``*`` stays within one segment, as in PHP's ``glob()``, which lists a
    directory's children and never the repo root itself.
    """
    if not any(ch in pattern for ch in "*?["):
        return pattern == root
    if not root:
        return False
    parts, segments = pattern.split("/"), root.split("/")
    return len(parts) == len(segments) and all(
        fnmatch.fnmatchcase(s, p) for p, s in zip(parts, segments, strict=True)
    )


def units(ctx: ResolverContext) -> Found:
    """Collect PHP units from Composer manifests with path repositories."""
    if ctx.repo_path is None or not any(path.endswith(".php") for path in ctx.path_set):
        return Found([])

    manifests = repo_composer_manifests(ctx)
    by_root: dict[str, str] = {}
    name_by_root: dict[str, str] = {}
    taken: set[str] = set()
    kept: list[tuple[str, ComposerManifest]] = []
    for manifest in sorted(manifests, key=lambda m: m.rel_dir):
        uid = _unit_id(manifest.name, manifest.rel_dir)
        # Composer names are case-insensitive, so a case variant is a duplicate;
        # an unnamed package's id is its directory, which keeps its case.
        key = uid.lower() if manifest.name else uid
        if key in taken:
            # A later duplicate is no unit here, so the fallback keeps its root
            # and a path repository pointing at it links nothing.
            log.warning("build_units_duplicate_id", unit_id=uid, root=manifest.rel_dir)
            continue
        taken.add(key)
        by_root[manifest.rel_dir] = uid
        if manifest.name:
            # Composer compares package names case-insensitively.
            name_by_root[manifest.rel_dir] = manifest.name.lower()
        kept.append((uid, manifest))

    claims: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for uid, manifest in kept:
        requires = {name.lower() for name in manifest.requires}
        deps = {
            target_uid
            for url in manifest.path_repositories
            if (pattern := _resolve_url(manifest.rel_dir, url)) is not None
            for root, target_uid in by_root.items()
            if (
                target_uid != uid
                and _matches(pattern, root)
                and name_by_root.get(root) in requires
            )
        }

        unit = BuildUnit(
            id=uid,
            ecosystem="php",
            name=manifest.name or (manifest.rel_dir.split("/")[-1] if manifest.rel_dir else "."),
            root=manifest.rel_dir,
            manifest=_manifest_path(manifest.rel_dir),
            source="composer",
            deps=tuple(sorted(deps)),
        )
        claims.append((unit, ()))

    return Found(claims)
