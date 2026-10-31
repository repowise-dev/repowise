"""A repo moved or copied with its ``.repowise`` keeps one repository row.

The store's single row still names the old directory. ``upsert_repository``
from the new directory must re-point it, not add a second row that servers
then have to choose between.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _git_repo(path: Path, message: str = "init") -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "a.txt").write_text(message)
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", message)
    return path


async def _upsert(db_dir: Path, local_path: Path) -> str:
    engine = create_engine(f"sqlite+aiosqlite:///{db_dir / '.repowise' / 'wiki.db'}")
    await init_db(engine)
    try:
        async with get_session(create_session_factory(engine)) as session:
            row = await upsert_repository(session, name=local_path.name, local_path=str(local_path))
            return row.id
    finally:
        await engine.dispose()


def _rows(db_dir: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(db_dir / ".repowise" / "wiki.db")) as conn:
        return conn.execute("SELECT id, local_path FROM repositories").fetchall()


async def _indexed(repo: Path) -> str:
    (repo / ".repowise").mkdir()
    return await _upsert(repo, repo)


async def test_moved_repo_repoints_its_row(tmp_path: Path) -> None:
    old = _git_repo(tmp_path / "old")
    repo_id = await _indexed(old)
    new = tmp_path / "new"
    old.rename(new)

    assert await _upsert(new, new) == repo_id
    assert _rows(new) == [(repo_id, str(new))]


async def test_copied_repo_repoints_its_row(tmp_path: Path) -> None:
    old = _git_repo(tmp_path / "old")
    repo_id = await _indexed(old)
    copy = tmp_path / "copy"
    shutil.copytree(old, copy)

    assert await _upsert(copy, copy) == repo_id
    assert _rows(copy) == [(repo_id, str(copy))]
    # The original keeps its own store untouched.
    assert _rows(old) == [(repo_id, str(old))]


async def test_copy_that_moved_on_still_repoints(tmp_path: Path) -> None:
    old = _git_repo(tmp_path / "old")
    repo_id = await _indexed(old)
    copy = tmp_path / "copy"
    shutil.copytree(old, copy)
    (copy / "a.txt").write_text("next")
    _git(copy, "commit", "-qam", "next")

    assert await _upsert(copy, copy) == repo_id
    assert len(_rows(copy)) == 1


async def test_unrelated_checkout_at_old_path_keeps_rows_apart(tmp_path: Path) -> None:
    other = _git_repo(tmp_path / "other", message="other history")
    target = _git_repo(tmp_path / "target", message="target history")
    (target / ".repowise").mkdir()
    # A store whose row names a live, unrelated checkout.
    await _upsert(target, other)

    await _upsert(target, target)
    assert sorted(p for _, p in _rows(target)) == sorted([str(other), str(target)])


async def test_store_outside_the_repo_keeps_one_row_per_path(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    (shared / ".repowise").mkdir(parents=True)
    first = _git_repo(tmp_path / "first")
    await _upsert(shared, first)
    first.rename(tmp_path / "gone")

    await _upsert(shared, tmp_path / "second")
    assert len(_rows(shared)) == 2


def _record_saving(repo: Path, index: int) -> None:
    from repowise.core.savings import recorder
    from repowise.core.savings.correlation import scoped_idempotency_key

    payload = {
        "idempotency_key": scoped_idempotency_key(str(repo), "mcp", index),
        "occurred_at": f"2026-10-01T00:00:0{index}Z",
        "surface": "mcp",
        "integration": "claude_code",
        "agent": "claude_code",
        "operation": "get_context",
        "evidence_kind": "inferred",
        "estimator": "chars_per_token_floor_v1",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": 1_000,
        "pre_budget_input_tokens": 1_000,
        "delivered_input_tokens": 100,
    }
    assert recorder.record_event(repo, payload) is True


async def test_moved_repo_keeps_its_savings_history(tmp_path: Path) -> None:
    from repowise.core.distill.store import OmissionStore
    from repowise.core.savings import recorder
    from repowise.core.savings.service import load_report

    old = _git_repo(tmp_path / "old")
    await _indexed(old)
    OmissionStore(recorder.sidecar_path(old)).close()
    _record_saving(old, 1)
    _record_saving(old, 2)
    new = tmp_path / "new"
    old.rename(new)
    assert load_report(new).unique_events == 0

    await _upsert(new, new)

    report = load_report(new)
    assert report.unique_events == 2
    assert report.saved_input_tokens == 1_800


async def test_copied_repo_leaves_the_original_ledger_alone(tmp_path: Path) -> None:
    from repowise.core.distill.store import OmissionStore
    from repowise.core.savings import recorder
    from repowise.core.savings.service import load_report

    old = _git_repo(tmp_path / "old")
    await _indexed(old)
    OmissionStore(recorder.sidecar_path(old)).close()
    _record_saving(old, 1)
    copy = tmp_path / "copy"
    shutil.copytree(old, copy)

    await _upsert(copy, copy)

    assert load_report(copy).unique_events == 1
    assert load_report(old).unique_events == 1


async def test_a_relocation_that_failed_is_retried_on_the_next_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.distill.store import OmissionStore
    from repowise.core.savings import recorder
    from repowise.core.savings.service import load_report

    old = _git_repo(tmp_path / "old")
    await _indexed(old)
    OmissionStore(recorder.sidecar_path(old)).close()
    _record_saving(old, 1)
    new = tmp_path / "new"
    old.rename(new)

    real = recorder.relocate_ledger
    monkeypatch.setattr(recorder, "relocate_ledger", lambda root: 0)  # a busy sidecar
    await _upsert(new, new)
    assert load_report(new).unique_events == 0

    monkeypatch.setattr(recorder, "relocate_ledger", real)
    engine = create_engine(f"sqlite+aiosqlite:///{new / '.repowise' / 'wiki.db'}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            await upsert_repository(session, name="new", local_path=str(new), stamp_head=True)
    finally:
        await engine.dispose()
    assert load_report(new).unique_events == 1


async def test_an_adopted_row_takes_its_savings_history_with_it(tmp_path: Path) -> None:
    """``update`` adopts a moved store's row before syncing; the ledger follows it."""
    from repowise.core.distill.store import OmissionStore
    from repowise.core.persistence.crud import adopt_moved_repository
    from repowise.core.savings import recorder
    from repowise.core.savings.service import load_report

    old = _git_repo(tmp_path / "old")
    await _indexed(old)
    OmissionStore(recorder.sidecar_path(old)).close()
    _record_saving(old, 1)
    new = tmp_path / "new"
    old.rename(new)

    engine = create_engine(f"sqlite+aiosqlite:///{new / '.repowise' / 'wiki.db'}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            assert await adopt_moved_repository(session, str(new)) is not None
    finally:
        await engine.dispose()
    assert load_report(new).unique_events == 1
