"""Tests for repository API sorting helpers."""

from datetime import UTC, datetime
from types import SimpleNamespace

from repowise.server.routers._sorting import repository_sort_key


def test_repository_sort_key_handles_mixed_tz_awareness() -> None:
    """Workspace databases can return a mix of naive and aware timestamps."""
    aware = SimpleNamespace(
        updated_at=datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
        created_at=None,
    )
    naive = SimpleNamespace(
        updated_at=datetime(2026, 7, 24, 8, 0),
        created_at=None,
    )

    assert sorted([naive, aware], key=repository_sort_key, reverse=True) == [aware, naive]
