"""Parse npm/yarn/pnpm ``package.json`` manifests.

Captures ``dependencies``, ``devDependencies``, ``peerDependencies``, and
``optionalDependencies``. A dep pinned by a local protocol (``workspace:``,
``link:``, ``file:``) is first-party; named workspace members are dropped
repo-wide by the extractor.
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

    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()
    for field_name, is_dev in _DEP_FIELDS:
        block = data.get(field_name)
        if not isinstance(block, dict):
            continue
        for raw_name, raw_version in block.items():
            name = str(raw_name).strip()
            if not name or name in seen or _is_local(raw_version):
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


_LOCAL_PROTOCOLS = ("workspace:", "link:", "file:")


def _is_local(version: object) -> bool:
    return isinstance(version, str) and version.strip().startswith(_LOCAL_PROTOCOLS)


def _normalize_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v or None
