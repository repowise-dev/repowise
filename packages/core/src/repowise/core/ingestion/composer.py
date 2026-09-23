"""The one ``composer.json`` reader.

Import resolution (PSR-4), TYPO3 extension discovery, PHP framework detection,
workspace service boundaries and cross-repo package edges all read the same few
fields of a ``composer.json`` through :class:`ComposerManifest`, and
:func:`find_composer_manifests` is the one answer to "which manifests does this
repo hold".
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
from repowise.core.test_paths import is_test_related_path

if TYPE_CHECKING:
    from .resolvers.context import ResolverContext

COMPOSER_JSON = "composer.json"

#: How deep below the repo root a nested manifest is looked for. Monorepos keep
#: packages at ``packages/<name>/`` or ``src/<Vendor>/<Package>/``; anything
#: deeper is almost always a fixture or a vendored copy.
_MAX_NESTED_DEPTH = 3

#: Never a first-party package root. ``vendor`` is read separately (installed
#: packages sit exactly two levels below it), ``var`` and ``Build`` are TYPO3
#: cache and tooling trees.
_SKIP_DIRS = PRUNED_DIRS | {"vendor", "var", "Build"}

#: Demo trees whose manifests describe something the repo does not ship. Test
#: and fixture trees are recognised by the shared test-path classifier.
_EXAMPLE_DIRS = frozenset({"example", "examples", "sample", "samples"})


def is_package_dir(rel_dir: str) -> bool:
    """Whether a ``composer.json`` in *rel_dir* (repo-relative posix) is first-party.

    Exposed so a caller already walking the repo (the workspace manifest
    index) applies the same rule without a second walk.
    """
    parts = rel_dir.split("/") if rel_dir else []
    return (
        len(parts) <= _MAX_NESTED_DEPTH
        and not any(
            p.startswith(".") or p in _SKIP_DIRS or p.lower() in _EXAMPLE_DIRS for p in parts
        )
        and not is_test_related_path(posixpath.join(rel_dir, COMPOSER_JSON))
    )


@dataclass(frozen=True, slots=True)
class ComposerManifest:
    """The fields of one ``composer.json`` that repowise reads.

    Autoload directories are repo-relative posix paths ('' for the repo root),
    already joined onto the manifest's own directory, so a nested package's
    ``"src/"`` reads as ``packages/billing/src``.
    """

    rel_dir: str  # directory holding the manifest, '' for the repo root
    name: str = ""
    type: str = ""
    require: Mapping[str, str] = field(default_factory=dict)
    require_dev: Mapping[str, str] = field(default_factory=dict)
    #: ``(namespace prefix, dirs)`` pairs, ``autoload`` before ``autoload-dev``.
    #: Prefixes keep composer's trailing ``\\``.
    psr4: tuple[tuple[str, tuple[str, ...]], ...] = ()
    psr0: tuple[tuple[str, tuple[str, ...]], ...] = ()
    classmap: tuple[str, ...] = ()
    #: ``url`` of every ``{"type": "path"}`` repository, relative to ``rel_dir``
    #: exactly as written (it is a filesystem path, often ``../shared``).
    path_repositories: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def requires(self) -> dict[str, str]:
        """``require`` and ``require-dev`` together, runtime entries winning."""
        return {**self.require_dev, **self.require}


def _dirs(value: object) -> list[str]:
    """An autoload value may be one path or a list of paths."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _rebase(rel_dir: str, path: str) -> str:
    joined = posixpath.normpath(posixpath.join(rel_dir, path.strip().rstrip("/")))
    return "" if joined == "." else joined


def _prefix_map(
    data: Mapping[str, Any], standard: str, rel_dir: str
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    merged: dict[str, list[str]] = {}
    for section in ("autoload", "autoload-dev"):
        block = data.get(section)
        mapping = block.get(standard) if isinstance(block, dict) else None
        if not isinstance(mapping, dict):
            continue
        for prefix, value in mapping.items():
            if isinstance(prefix, str):
                merged.setdefault(prefix, []).extend(_rebase(rel_dir, d) for d in _dirs(value))
    return tuple((prefix, tuple(dirs)) for prefix, dirs in merged.items())


def _str_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def parse_composer(data: object, rel_dir: str = "") -> ComposerManifest | None:
    """Build a manifest from already-decoded JSON, or None when it is not an object."""
    if not isinstance(data, dict):
        return None
    classmap: list[str] = []
    for section in ("autoload", "autoload-dev"):
        block = data.get(section)
        if isinstance(block, dict):
            classmap.extend(_rebase(rel_dir, d) for d in _dirs(block.get("classmap")))
    repositories = data.get("repositories")
    if isinstance(repositories, dict):  # the keyed form composer also accepts
        repositories = list(repositories.values())
    path_repos = tuple(
        repo["url"]
        for repo in (repositories if isinstance(repositories, list) else [])
        if isinstance(repo, dict) and repo.get("type") == "path" and isinstance(repo.get("url"), str)
    )
    name, kind, extra = data.get("name"), data.get("type"), data.get("extra")
    return ComposerManifest(
        rel_dir=rel_dir,
        name=name if isinstance(name, str) else "",
        type=kind if isinstance(kind, str) else "",
        require=_str_map(data.get("require")),
        require_dev=_str_map(data.get("require-dev")),
        psr4=_prefix_map(data, "psr-4", rel_dir),
        psr0=_prefix_map(data, "psr-0", rel_dir),
        classmap=tuple(classmap),
        path_repositories=path_repos,
        extra=extra if isinstance(extra, dict) else {},
    )


def read_composer(path: Path, rel_dir: str = "") -> ComposerManifest | None:
    """Read one ``composer.json``; None when it is missing or not valid JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return None
    return parse_composer(data, rel_dir)


def _vendor_manifest_paths(vendor_root: Path) -> list[Path]:
    """``vendor/<vendor>/<package>/composer.json``: composer's layout is flat."""
    found: list[Path] = []
    try:
        vendors = sorted(vendor_root.iterdir())
    except OSError:
        return found
    for vendor_dir in vendors:
        if not vendor_dir.is_dir() or vendor_dir.name.startswith("."):
            continue
        try:
            packages = sorted(vendor_dir.iterdir())
        except OSError:
            continue
        for pkg_dir in packages:
            candidate = pkg_dir / COMPOSER_JSON
            if not pkg_dir.name.startswith(".") and candidate.is_file():
                found.append(candidate)
    return found


def find_composer_manifests(
    repo_root: Path, *, prune_nested_git: bool = True
) -> list[ComposerManifest]:
    """Every first-party ``composer.json`` in *repo_root*, root first.

    Nested manifests are found up to three directories deep, skipping junk,
    hidden directories, ``vendor``, test, fixture and example trees, and (by
    default) nested git repos; :func:`is_package_dir` is the rule.
    """
    root = Path(repo_root)
    manifests: list[ComposerManifest] = []
    for dirpath, dirnames, filenames in walk_repo(
        root, prune_dirs=_SKIP_DIRS, prune_nested_git=prune_nested_git
    ):
        rel = dirpath.relative_to(root).as_posix()
        rel = "" if rel == "." else rel
        dirnames[:] = sorted(
            d for d in dirnames if is_package_dir(f"{rel}/{d}" if rel else d)
        )
        if COMPOSER_JSON in filenames and is_package_dir(rel):
            manifest = read_composer(dirpath / COMPOSER_JSON, rel)
            if manifest is not None:
                manifests.append(manifest)
    return manifests


def find_vendor_manifests(repo_root: Path) -> list[ComposerManifest]:
    """Installed packages under ``vendor/``.

    Only a TYPO3 project reads these: it keeps its extensions there.
    """
    root = Path(repo_root)
    manifests = (
        read_composer(path, path.parent.relative_to(root).as_posix())
        for path in _vendor_manifest_paths(root / "vendor")
    )
    return [m for m in manifests if m is not None]


def repo_composer_manifests(ctx: ResolverContext) -> list[ComposerManifest]:
    """:func:`find_composer_manifests` for a resolver context, cached on it.

    Import resolution and the PHP framework handlers all ask within one build.
    A repo with no PHP file and no root manifest is never walked.
    """
    cached = getattr(ctx, "_composer_manifests", None)
    if cached is None:
        repo_path = ctx.repo_path
        has_php = repo_path is not None and (
            (repo_path / COMPOSER_JSON).is_file()
            or any(p.endswith(".php") for p in ctx.path_set)
        )
        cached = (
            find_composer_manifests(repo_path, prune_nested_git=ctx.prune_nested_git)
            if has_php and repo_path is not None
            else []
        )
        ctx._composer_manifests = cached  # type: ignore[attr-defined]
    return cached
