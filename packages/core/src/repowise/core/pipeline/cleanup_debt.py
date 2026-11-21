"""Durable retry ledger for post-transaction search-index cleanup."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.fsutils import atomic_write_text

CLEANUP_DEBT_FILENAME = "cleanup-debt.json"
_KINDS = ("fts", "vectors")


def _path(repo_path: Path) -> Path:
    return repo_path / ".repowise" / CLEANUP_DEBT_FILENAME


def _read_cleanup_debt(repo_path: Path) -> dict:
    path = _path(repo_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError, TypeError):
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _write_cleanup_debt(repo_path: Path, raw: dict) -> None:
    path = _path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(raw, indent=2))


def load_cleanup_debt(repo_path: Path) -> dict[str, set[str]]:
    raw = _read_cleanup_debt(repo_path)
    return {kind: set(raw.get(kind) or []) for kind in _KINDS}


def tombstone_cleanup_seeded(repo_path: Path) -> bool:
    """Whether pre-existing tombstones have been queued for search cleanup."""
    return _read_cleanup_debt(repo_path).get("tombstones_seeded") is True


def seed_tombstone_cleanup_debt(repo_path: Path, page_ids: set[str]) -> None:
    """Queue legacy tombstones once while preserving outstanding cleanup debt."""
    raw = _read_cleanup_debt(repo_path)
    for kind in _KINDS:
        raw[kind] = sorted(set(raw.get(kind) or []) | page_ids)
    raw["tombstones_seeded"] = True
    _write_cleanup_debt(repo_path, raw)


def record_cleanup_debt(repo_path: Path, kind: str, page_ids: set[str]) -> None:
    if not page_ids:
        return
    raw = _read_cleanup_debt(repo_path)
    raw[kind] = sorted(set(raw.get(kind) or []) | page_ids)
    _write_cleanup_debt(repo_path, raw)


def clear_cleanup_debt(repo_path: Path, kind: str, page_ids: set[str]) -> None:
    if not page_ids:
        return
    raw = _read_cleanup_debt(repo_path)
    raw[kind] = sorted(set(raw.get(kind) or []) - page_ids)
    _write_cleanup_debt(repo_path, raw)
