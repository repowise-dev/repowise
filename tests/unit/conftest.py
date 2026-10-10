"""Guards shared by every unit test."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def cursor_hooks_json(tmp_path_factory, monkeypatch):
    """Keep every test off the developer's real ``~/.cursor/hooks.json``.

    Rewrite-hook install paths run wherever ``~/.cursor`` exists, and not every
    suite isolates ``$HOME``. Tests that want Cursor present patch ``detect``.
    """
    from repowise.cli.agent_adapters import cursor

    hooks = tmp_path_factory.mktemp("cursor") / "hooks.json"
    monkeypatch.setattr(cursor, "user_hooks_path", lambda: hooks)
    monkeypatch.setattr(cursor.CursorAdapter, "detect", lambda self: False)
    return hooks


@pytest.fixture(autouse=True)
def fresh_test_map_cache():
    """In-memory stores reuse one URL and seed graph rows without moving the
    index stamp, so the process-wide test-map cache starts empty per test."""
    from repowise.core.analysis.test_reachability import clear_test_map_cache

    clear_test_map_cache()
    yield
    clear_test_map_cache()
