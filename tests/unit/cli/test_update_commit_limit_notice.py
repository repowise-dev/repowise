"""The one-shot notice for a ``commit_limit`` pinned below the default window."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd import commit_limit_notice
from repowise.cli.helpers import load_config, save_config_partial, save_state
from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT


class _FakeTerminal:
    is_terminal = True

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **_kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))


@pytest.fixture
def console(monkeypatch) -> _FakeTerminal:
    fake = _FakeTerminal()
    monkeypatch.setattr(commit_limit_notice, "console", fake)
    return fake


def _repo(tmp_path: Path, limit: int | None) -> Path:
    save_state(tmp_path, {})
    if limit is not None:
        save_config_partial(tmp_path, commit_limit=limit)
    return tmp_path


def _surface(repo: Path, *, dry_run: bool = False) -> None:
    commit_limit_notice.surface_pinned_commit_limit(repo, emitter=None, dry_run=dry_run)


def test_a_pinned_lower_limit_is_named_once(tmp_path: Path, console: _FakeTerminal) -> None:
    repo = _repo(tmp_path, 500)
    _surface(repo)
    _surface(repo)
    assert console.lines == [
        f"[dim]commit_limit is pinned to 500 in config.yaml; "
        f"the default is now {_DEFAULT_COMMIT_LIMIT}.[/dim]"
    ]
    # The user's choice is never changed for them.
    assert load_config(repo)["commit_limit"] == 500


@pytest.mark.parametrize("limit", [None, _DEFAULT_COMMIT_LIMIT, _DEFAULT_COMMIT_LIMIT + 1])
def test_silent_without_a_lower_pin(
    tmp_path: Path, console: _FakeTerminal, limit: int | None
) -> None:
    _surface(_repo(tmp_path, limit))
    assert console.lines == []


def test_a_dry_run_does_not_spend_the_notice(tmp_path: Path, console: _FakeTerminal) -> None:
    repo = _repo(tmp_path, 200)
    _surface(repo, dry_run=True)
    _surface(repo)
    assert len(console.lines) == 2
