"""Parse npm/yarn/pnpm ``package.json`` manifests.

Captures ``dependencies``, ``devDependencies``, ``peerDependencies``, and
``optionalDependencies``. Workspace metadata is ignored — workspace packages
are containers, not external systems.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import ExternalSystemRecord
from .classifier import classify, display_name_for
from .io_kind import classify_io_kind

filenames: tuple[str, ...] = ("package.json",)
ecosystem: str = "npm"

_DEP_FIELDS: tuple[tuple[str, bool], ...] = (
    ("dependencies", False),
    ("devDependencies", True),
    ("peerDependencies", False),
    ("optionalDependencies", False),
)


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    declared_in = manifest_path.relative_to(repo_root).as_posix()
    workspace_names = _collect_workspace_names(data, manifest_path, repo_root)

    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()
    for field_name, is_dev in _DEP_FIELDS:
        block = data.get(field_name)
        if not isinstance(block, dict):
            continue
        for raw_name, raw_version in block.items():
            name = str(raw_name).strip()
            if not name or name in seen or name in workspace_names:
                continue
            seen.add(name)
            version = _normalize_version(raw_version)
            records.append(
                ExternalSystemRecord(
                    name=name,
                    ecosystem=ecosystem,
                    declared_in=declared_in,
                    version=version,
                    display_name=display_name_for(name),
                    category=classify(name),
                    io_kind=classify_io_kind(name),
                    is_dev_dep=is_dev,
                )
            )
    return records


def _normalize_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v or None


def _collect_workspace_names(
    data: dict[str, object], manifest_path: Path, repo_root: Path
) -> set[str]:
    """Read ``workspaces`` globs and return the names of each sibling package.

    We rely on this set to skip workspace deps that appear as version ``*``
    or ``workspace:*`` in a parent package.json — those are first-party
    containers, not external systems.
    """
    workspaces = data.get("workspaces")
    patterns: list[str] = []
    if isinstance(workspaces, list):
        patterns = [str(p) for p in workspaces if isinstance(p, str)]
    elif isinstance(workspaces, dict):
        packages = workspaces.get("packages")
        if isinstance(packages, list):
            patterns = [str(p) for p in packages if isinstance(p, str)]
    if not patterns:
        return set()

    root = manifest_path.parent
    names: set[str] = set()
    for pat in patterns:
        for match in root.glob(pat + "/package.json"):
            try:
                inner = json.loads(match.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            inner_name = inner.get("name") if isinstance(inner, dict) else None
            if isinstance(inner_name, str) and inner_name:
                names.add(inner_name)
    return names
