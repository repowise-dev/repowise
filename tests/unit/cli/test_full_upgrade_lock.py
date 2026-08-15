"""``repowise update --full`` must respect the single-flight lock.

Regression anchor for #1485: the ``--full`` fast-to-full upgrade ran before the
incremental update's single-flight lock and called ``save_state`` (plus the DB
writes inside ``_run_upgrade``) without ever acquiring it — a concurrent
``repowise update`` could race the full upgrade's state writes exactly the way
the lock exists to prevent.

The full upgrade must acquire the lock itself; when another update holds it,
the run defers (raises a clear ClickException and leaves a pending marker)
instead of writing state from outside the lock.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from repowise.cli.commands import upgrade_flow as up_mod


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()


def _repo_with_state(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("0")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c0")
    (repo / ".repowise").mkdir()
    from repowise.cli.helpers import save_state

    save_state(repo, {"last_sync_commit": _git(repo, "rev-parse", "HEAD")})
    return repo


def _stub_provider_and_upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SimpleNamespace(provider_name="openai", model_name="gpt-4o")
    monkeypatch.setattr(up_mod, "resolve_provider", lambda *a, **k: provider)

    async def _stub_upgrade(*a: object, **k: object) -> tuple[list[str], int]:
        return (["p1", "p2"], 2)

    monkeypatch.setattr(up_mod, "_run_upgrade", _stub_upgrade)


def _call_upgrade(repo: Path, yes: bool = True) -> None:
    up_mod.upgrade_to_full(
        repo,
        provider_name=None,
        model=None,
        reasoning=None,
        concurrency=1,
        yes=yes,
    )


def test_full_upgrade_acquires_and_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_state(tmp_path)
    _stub_provider_and_upgrade(monkeypatch)

    acquired: list[bool] = []

    def _fake_acquire(repo_path: Path, target: str | None) -> dict | None:
        acquired.append(True)
        return None

    monkeypatch.setattr(up_mod, "try_acquire_update_lock", _fake_acquire)
    released: list[Path] = []
    monkeypatch.setattr(up_mod, "release_update_lock", lambda p: released.append(p))

    _call_upgrade(repo)

    assert acquired == [True], "the full upgrade must acquire the single-flight lock"
    assert released == [repo], "the full upgrade must release the lock when it returns"


def test_full_upgrade_defers_when_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo_with_state(tmp_path)
    _stub_provider_and_upgrade(monkeypatch)

    held = {"pid": 4242, "target_commit": "x", "started_at": 0.0}

    def _held_lock(repo_path: Path, target: str | None) -> dict:
        return held

    monkeypatch.setattr(up_mod, "try_acquire_update_lock", _held_lock)
    ran_upgrade: list[bool] = []

    async def _held_stub_upgrade(*a: object, **k: object) -> tuple[list[str], int]:
        ran_upgrade.append(True)
        return ([], 0)

    monkeypatch.setattr(up_mod, "_run_upgrade", _held_stub_upgrade)
    from repowise.cli.helpers import read_update_pending

    with pytest.raises(click.ClickException, match="Another `repowise update` is already running"):
        _call_upgrade(repo)

    assert ran_upgrade == [], "the upgrade must not run while another update holds the lock"
    assert read_update_pending(repo) is not None, "the deferred run must leave a pending marker"
