"""Swift packages from ``Package.swift`` manifests, one unit per package.

The name comes from ``Package(name: "...")``. A ``Package.swift`` that cannot
be read, names no package, or has a name an earlier root already took is left
to the manifest fallback (skipped with a warning for unreadable files).

Deps are the in-repo packages a manifest reaches through
``.package(path: "...")`` (or the older ``.package(name: "...", path: "...")``)
calls. A ``.package(url: ...)`` dependency in any form (``from:``,
``.upToNextMajor(from:)``, ``branch:``, ``exact:`` ...) is external and links
nothing. A local package reached by ``file://`` URL is not linked. A path
dependency pointing outside the repo (absolute, or ``..``/``../`` after
normalisation) links nothing, even if an in-repo package shares the same name.
A path dependency pointing at a directory that holds no Swift unit links
nothing.

Ceiling: this is a regex reader, not an evaluation of the manifest. It does
not see ``#if`` conditions, computed values, or variables holding paths.
Targets are not units. ``.package(url: ...)`` and registry deps
(``.package(id: ...)``) are external. Upgrade path is ``swift package
dump-package`` / ``Package.resolved``.
"""

from __future__ import annotations

import posixpath
import re

import structlog

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.adapters.manifest_roots import package_manifests
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.swift_spm import SwiftPackage, parse_package_manifest

log = structlog.get_logger(__name__)

_MANIFEST = "Package.swift"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _norm_dep_path(root: str, dep_path: str) -> str | None:
    """Normalise a path dependency to a repo-relative root, or None if outside.

    ``"."`` becomes ``""`` (repo root). Absolute paths, Windows drive paths,
    and paths that resolve to ``..`` or starting with ``../`` are outside the
    repo and return ``None``.
    """
    dep_path = dep_path.replace("\\", "/")
    if dep_path.startswith("/") or _DRIVE_RE.match(dep_path):
        return None
    joined = posixpath.normpath(posixpath.join(root, dep_path))
    if joined == ".":
        return ""
    if joined == ".." or joined.startswith("../"):
        return None
    return joined


def _read(ctx: ResolverContext) -> dict[str, SwiftPackage]:
    repo = ctx.repo_path
    read: dict[str, SwiftPackage] = {}
    for root, names in sorted(package_manifests(ctx).items()):
        if _MANIFEST in names:
            manifest_path = repo / root / _MANIFEST
            parsed = parse_package_manifest(manifest_path)
            if parsed is None:
                log.warning("build_units_package_swift_unreadable", manifest=str(manifest_path))
                continue
            read[root] = parsed
    return read


def _deps(root: str, pkg: SwiftPackage, by_root: dict[str, str]) -> tuple[str, ...]:
    dep_ids: set[str] = set()
    for dep_path in pkg.path_deps:
        norm = _norm_dep_path(root, dep_path)
        if norm is not None and norm in by_root:
            dep_ids.add(by_root[norm])
    return tuple(sorted(dep_ids))


def units(ctx: ResolverContext) -> Found:
    # Manifests alone do not wake an adapter: need a .swift source file.
    if ctx.repo_path is None or not any(
        p.endswith(".swift") and posixpath.basename(p) != _MANIFEST for p in ctx.path_set
    ):
        return Found([])

    read = _read(ctx)

    by_root: dict[str, str] = {}
    taken: set[str] = set()
    for root, pkg in read.items():
        uid = f"swift:{pkg.name}"
        if uid in taken:
            log.warning("build_units_duplicate_id", unit_id=uid, root=root)
            continue
        taken.add(uid)
        by_root[root] = uid

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for root, uid in by_root.items():
        pkg = read[root]
        unit = BuildUnit(
            id=uid,
            ecosystem="swift",
            name=pkg.name,
            root=root,
            manifest=f"{root}/{_MANIFEST}" if root else _MANIFEST,
            source="swiftpm",
            deps=_deps(root, pkg, by_root),
        )
        out.append((unit, ()))

    return Found(out)
