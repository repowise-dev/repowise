"""The ``package.json`` fields the JS convention rules read."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from .nuxt import NUXT_I18N, _nuxt_lang_dirs

#: The ``package.json`` fields that name dependencies.
NPM_DEP_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


@dataclass(frozen=True, slots=True)
class NpmManifest:
    """The fields of one ``package.json`` the convention rules read."""

    rel_dir: str  # directory holding the manifest, '' for the repo root
    dependencies: frozenset[str] = frozenset()
    scripts: Mapping[str, str] = field(default_factory=dict)
    #: ``i18n.langDir`` values written in a ``nuxt.config.*`` beside the manifest.
    nuxt_lang_dirs: tuple[str, ...] = ()


def npm_dependency_names(data: object) -> frozenset[str]:
    """Every package a decoded ``package.json`` depends on, in any dependency field."""
    if not isinstance(data, dict):
        return frozenset()
    return frozenset(
        name
        for key in NPM_DEP_FIELDS
        if isinstance(block := data.get(key), dict)
        for name in block
        if isinstance(name, str)
    )


def parse_npm_manifest(data: object, rel_dir: str = "") -> NpmManifest | None:
    """A manifest from decoded ``package.json`` JSON, or None when it is not an object."""
    if not isinstance(data, dict):
        return None
    scripts = data.get("scripts")
    return NpmManifest(
        rel_dir=rel_dir,
        dependencies=npm_dependency_names(data),
        scripts={k: v for k, v in scripts.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(scripts, dict)
        else {},
    )


def npm_manifests(repo_root: str | Path, paths: Iterable[str]) -> list[NpmManifest]:
    """The readable ``package.json`` in the repo root and every directory above *paths*.

    Only directories holding an indexed file are asked, so the cost follows
    the paths, and a ``node_modules`` tree the walk pruned is never read.
    """
    root = Path(repo_root)
    dirs = {""}
    for path in paths:
        parts = path.split("/")[:-1]
        dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    found: list[NpmManifest] = []
    for rel in sorted(dirs):
        try:
            data = json.loads((root / rel / "package.json").read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        if (manifest := parse_npm_manifest(data, rel)) is not None:
            if NUXT_I18N in manifest.dependencies:
                manifest = replace(manifest, nuxt_lang_dirs=_nuxt_lang_dirs(root / rel))
            found.append(manifest)
    return found
