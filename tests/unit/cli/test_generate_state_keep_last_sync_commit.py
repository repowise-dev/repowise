"""``generate`` must not clobber ``last_sync_commit`` with ``None``.

Regression anchor for #1507: ``_write_state`` unconditionally set
``state["last_sync_commit"] = get_head_commit(repo_path)``. ``get_head_commit``
returns ``None`` on any git failure, which wiped the previously recorded base
commit; the next ``repowise update`` then saw ``last_sync_commit`` as ``None``
and hard-failed as if the repo had never been indexed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.generate_cmd import command as gen_mod
from repowise.cli.commands.generate_cmd.command import _write_state


def _outcome() -> SimpleNamespace:
    return SimpleNamespace(total_pages=5, remaining_template_pages=0)


def _provider() -> SimpleNamespace:
    return SimpleNamespace(provider_name="openai", model_name="gpt-4o")


def _state(**overrides: object) -> dict:
    state: dict = {
        "last_sync_commit": "original-sha",
        "total_pages": 1,
        "docs_mode": "llm",
        "provider": "openai",
        "model": "gpt-4o",
    }
    state.update(overrides)
    return state


def test_git_failure_preserves_existing_last_sync_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state()
    monkeypatch.setattr(gen_mod, "get_head_commit", lambda _p: None)

    _write_state(tmp_path, state, _provider(), _outcome())

    assert state["last_sync_commit"] == "original-sha"
    assert state["total_pages"] == 5


def test_git_success_stamps_new_last_sync_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state()
    monkeypatch.setattr(gen_mod, "get_head_commit", lambda _p: "new-sha")

    _write_state(tmp_path, state, _provider(), _outcome())

    assert state["last_sync_commit"] == "new-sha"
