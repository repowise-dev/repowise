"""Manifest-based extraction of external system dependencies.

Public API:
    extract_external_systems(repo_root, manifest_paths=None) -> list[ExternalSystemRecord]

Each ecosystem-specific parser lives in its own submodule
(:mod:`.npm`, :mod:`.pypi`, :mod:`.cargo`, :mod:`.go`, :mod:`.nuget`) and
exposes ``filenames``, ``ecosystem``, and ``parse(manifest_path, repo_root)``.

The extractor walks the repo (or accepts a pre-computed list of manifest
paths from the traverser) and dispatches to the matching parser. Parsers
never raise on malformed input.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from . import cargo, go, npm, nuget, pypi
from .base import ExternalSystemRecord, ManifestParser

__all__ = [
    "ExternalSystemRecord",
    "ManifestParser",
    "extract_external_systems",
]

_PARSERS: tuple[ModuleType, ...] = (npm, pypi, cargo, go, nuget)

# Map exact filename → parser module
_FILENAME_PARSERS: dict[str, ModuleType] = {
    fname: parser for parser in _PARSERS for fname in parser.filenames
}

# Filename patterns that match by predicate (pypi requirements*.txt, nuget *.csproj)
def _matches_pattern(filename: str) -> ModuleType | None:
    lower = filename.lower()
    if lower.startswith("requirements") and lower.endswith(".txt"):
        return pypi
    if lower.endswith(".csproj"):
        return nuget
    return None


def _parser_for(path: Path) -> ModuleType | None:
    parser = _FILENAME_PARSERS.get(path.name)
    if parser is not None:
        return parser
    return _matches_pattern(path.name)


def extract_external_systems(
    repo_root: Path,
    manifest_paths: Iterable[Path] | None = None,
) -> list[ExternalSystemRecord]:
    """Return the deduplicated list of external systems declared in ``repo_root``.

    If ``manifest_paths`` is provided, only those files are inspected (the
    traverser already walks the repo and can hand us the manifests it saw).
    Otherwise the function does its own bounded walk (depth ≤ 4) so it can be
    called standalone from tests.
    """
    candidates = list(manifest_paths) if manifest_paths is not None else _discover(repo_root)

    records: list[ExternalSystemRecord] = []
    for path in candidates:
        parser = _parser_for(path)
        if parser is None:
            continue
        try:
            records.extend(parser.parse(path, repo_root))
        except Exception:  # parser bugs must not break ingestion
            continue
    return _deduplicate(records)


def _discover(repo_root: Path) -> list[Path]:
    """Walk the repo (depth ≤ 4) collecting recognised manifest files."""
    seen: list[Path] = []
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build"}
    root = Path(repo_root)
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if len(rel_parts) > 4 or any(part in skip_dirs for part in rel_parts):
            continue
        if not path.is_file():
            continue
        if _parser_for(path) is not None:
            seen.append(path)
    return seen


def _deduplicate(records: list[ExternalSystemRecord]) -> list[ExternalSystemRecord]:
    """Drop duplicates keyed on (name, declared_in). Stable order."""
    out: list[ExternalSystemRecord] = []
    seen: set[tuple[str, str]] = set()
    for rec in records:
        key = (rec.name, rec.declared_in)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out
