"""Parse Rust ``Cargo.toml`` manifests.

Captures ``[dependencies]``, ``[dev-dependencies]``, ``[build-dependencies]``.
Workspace members and path/git dependencies are skipped — those are
first-party containers, not external systems.
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover — Python <3.11
    import tomli as tomllib  # type: ignore[no-redef]

from .base import ExternalSystemRecord
from .classifier import classify, display_name_for
from .io_kind import classify_io_kind

filenames: tuple[str, ...] = ("Cargo.toml",)
ecosystem: str = "cargo"

_SECTIONS: tuple[tuple[str, bool], ...] = (
    ("dependencies", False),
    ("dev-dependencies", True),
    ("build-dependencies", False),
)


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    declared_in = manifest_path.relative_to(repo_root).as_posix()
    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()

    for section, is_dev in _SECTIONS:
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for raw_name, spec in block.items():
            if _is_path_or_git(spec):
                continue
            name = str(raw_name).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            version = _spec_version(spec)
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


def _spec_version(spec: object) -> str | None:
    if isinstance(spec, str):
        s = spec.strip()
        return s or None
    if isinstance(spec, dict):
        v = spec.get("version")
        if isinstance(v, str):
            return v.strip() or None
    return None


def _is_path_or_git(spec: object) -> bool:
    return isinstance(spec, dict) and ("path" in spec or "git" in spec)
