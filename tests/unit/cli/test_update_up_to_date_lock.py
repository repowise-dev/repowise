"""The "already up to date" self-heal must respect the single-flight lock.

Regression anchor for #1486: on the up-to-date shortcut, ``repowise update``
backfilled state fingerprints and stamped the DB head commit *before* acquiring
the single-flight lock, so two updates racing on the up-to-date path could stomp
each other's ``save_state`` — the exact race the lock exists to prevent. The heal
writes must run only while holding the lock, and must be skipped when another
update already holds it.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd import command as upd_cmd
from repowise.cli.helpers import save_state, write_update_pending


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()


def _repo_with_three_commits(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("0")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c0")
    c0 = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("1")
    _git(repo, "commit", "-am", "c1")
    c1 = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-am", "c2")
    c2 = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise").mkdir()
    return repo, c0, c1, c2


def _invoke_update(repo: Path) -> str:
    from click.testing import CliRunner

    from repowise.cli.main import cli

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    return result.output


def _indexed_repo(tmp_path: Path) -> tuple[Path, str]:
    """An up-to-date repo: state at HEAD, so update takes the no-op path."""
    from repowise.core.pipeline.full_index import index_repo_full

    repo, _c0, _c1, c2 = _repo_with_three_commits(tmp_path)
    asyncio.run(index_repo_full(repo))
    # index_repo_full does not persist state.json in the test harness; record
    # the sync/docs pointers at HEAD so the update resolves to "already current".
    save_state(repo, {"last_sync_commit": c2, "last_docs_commit": c2, "docs_enabled": False})
    return repo, c2


def _install_recorders(monkeypatch: pytest.MonkeyPatch, calls: dict[str, int]) -> None:
    def _make(name: str):
        def _recorder(*a: object, **k: object) -> None:
            calls[name] = calls.get(name, 0) + 1

        return _recorder

    monkeypatch.setattr(upd_cmd, "stamp_head_commit", _make("stamp_head_commit"))
    monkeypatch.setattr(upd_cmd, "heal_commit_offsets", _make("heal_commit_offsets"))
    monkeypatch.setattr(upd_cmd, "consume_update_pending", _make("consume_update_pending"))
    monkeypatch.setattr(upd_cmd, "save_state", _make("save_state"))
    monkeypatch.setattr(upd_cmd, "release_update_lock", _make("release_update_lock"))


def test_up_to_date_self_heal_skipped_when_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another update owns the lock -> the heal must not write anything."""
    repo, c2 = _indexed_repo(tmp_path)
    write_update_pending(repo, c2)

    calls: dict[str, int] = {}

    def _held_lock(repo_path: Path, target: str | None) -> dict:
        calls["acquire"] = calls.get("acquire", 0) + 1
        return {"pid": 9999, "target_commit": "x", "held_since": 0.0}  # not None = held

    _install_recorders(monkeypatch, calls)
    monkeypatch.setattr(upd_cmd, "try_acquire_update_lock", _held_lock)

    _invoke_update(repo)

    assert calls.get("acquire", 0) > 0, "update must try the single-flight lock"
    assert calls.get("stamp_head_commit", 0) == 0
    assert calls.get("heal_commit_offsets", 0) == 0
    assert calls.get("consume_update_pending", 0) == 0
    assert calls.get("save_state", 0) == 0
    assert calls.get("release_update_lock", 0) == 0


def test_up_to_date_self_heal_runs_and_releases_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock free -> the heal runs once, and the lock is released afterwards."""
    repo, _c2 = _indexed_repo(tmp_path)

    calls: dict[str, int] = {}

    def _free_lock(repo_path: Path, target: str | None) -> dict | None:
        calls["acquire"] = calls.get("acquire", 0) + 1
        return None  # acquired

    _install_recorders(monkeypatch, calls)
    monkeypatch.setattr(upd_cmd, "try_acquire_update_lock", _free_lock)

    _invoke_update(repo)

    assert calls.get("acquire", 0) > 0
    assert calls.get("stamp_head_commit", 0) == 1
    assert calls.get("heal_commit_offsets", 0) == 1
    assert calls.get("consume_update_pending", 0) == 1
    assert calls.get("release_update_lock", 0) == 1
