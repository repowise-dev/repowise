"""Sorting helpers for repository API responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class _RepositoryTimestamps(Protocol):
    updated_at: datetime | None
    created_at: datetime | None


def repository_sort_key(repo: _RepositoryTimestamps) -> datetime:
    """Return a UTC-aware timestamp for repositories merged across databases."""
    timestamp = repo.updated_at or repo.created_at
    if timestamp is None:
        return datetime.min.replace(tzinfo=UTC)
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
