"""Durable retry ledger for post-transaction search-index cleanup."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.fsutils import atomic_write_text

_KINDS = ("fts", "vectors")


def _path(repo_path: Path) -> Path:
    return repo_path / ".repowise" / "cleanup-debt.json"


def load_cleanup_debt(repo_path: Path) -> dict[str, set[str]]:
    path = _path(repo_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError, TypeError):
        raw = {}
    return {kind: set(raw.get(kind) or []) for kind in _KINDS}


def record_cleanup_debt(repo_path: Path, kind: str, page_ids: set[str]) -> None:
    if not page_ids:
        return
    debt = load_cleanup_debt(repo_path)
    debt[kind].update(page_ids)
    path = _path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps({key: sorted(values) for key, values in debt.items()}, indent=2),
    )


def clear_cleanup_debt(repo_path: Path, kind: str, page_ids: set[str]) -> None:
    if not page_ids:
        return
    debt = load_cleanup_debt(repo_path)
    debt[kind].difference_update(page_ids)
    path = _path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps({key: sorted(values) for key, values in debt.items()}, indent=2),
    )
