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


async def _seed_repo_level_pages(db_dir: Path, repo_id: str, name: str) -> None:
    """The repo-level pages a generation run stores under *name*, plus a child.

    Each repo-level page is written twice so a ``wiki_page_versions`` row
    exists too — the re-key has to carry it, not just the live page.
    """
    from repowise.core.persistence.crud import upsert_page

    engine = create_engine(f"sqlite+aiosqlite:///{db_dir / '.repowise' / 'wiki.db'}")
    try:
        async with get_session(create_session_factory(engine)) as session:
            for page_type in ("repo_overview", "architecture_diagram"):
                page = dict(
                    page_id=f"{page_type}:{name}",
                    repository_id=repo_id,
                    page_type=page_type,
                    title=page_type,
                    content="# page",
                    target_path=name,
                    source_hash="abc123",
                    model_name="mock",
                    provider_name="mock",
                )
                await upsert_page(session, **page)
                # Different bytes: an identical rewrite is the idempotent
                # no-op branch and never spawns the version snapshot.
                await upsert_page(session, **{**page, "content": "# page, revised"})
            await upsert_page(
                session,
                page_id="module_page:src",
                repository_id=repo_id,
                page_type="module_page",
                title="src",
                content="# src",
                target_path="src",
                source_hash="abc123",
                model_name="mock",
                provider_name="mock",
                parent_page_id=f"repo_overview:{name}",
            )
    finally:
        await engine.dispose()


def _seed_fts(db_dir: Path, page_id: str, target: str) -> None:
    with closing(sqlite3.connect(db_dir / ".repowise" / "wiki.db")) as conn:
        conn.execute(
            "INSERT INTO page_fts(page_id, title, content, summary, target_path, vocabulary, digest)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (page_id, "Overview", "text", "summary", target, "", ""),
        )
        conn.commit()


def _pages(db_dir: Path) -> list[tuple[str, str, str | None]]:
    """``(id, target_path, parent_page_id)`` for every stored page."""
    with closing(sqlite3.connect(db_dir / ".repowise" / "wiki.db")) as conn:
        return conn.execute(
            "SELECT id, target_path, parent_page_id FROM wiki_pages ORDER BY id"
        ).fetchall()


def _version_page_ids(db_dir: Path) -> list[str]:
    with closing(sqlite3.connect(db_dir / ".repowise" / "wiki.db")) as conn:
        return sorted(r[0] for r in conn.execute("SELECT page_id FROM wiki_page_versions"))


def _fts_rows(db_dir: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(db_dir / ".repowise" / "wiki.db")) as conn:
        return conn.execute("SELECT page_id, target_path FROM page_fts").fetchall()


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


async def test_a_relocated_store_keeps_one_overview_under_the_new_name(tmp_path: Path) -> None:
    """#5366: the overview id carries the folder name; a rename moves the page.

    Generation looks the overview up as ``repo_overview:<folder>``. Left
    under the old folder's id, the stored page is never found: a second
    overview is generated under the new id while the old one lingers,
    and the notes or links on it are lost.
    """
    old = _git_repo(tmp_path / "repo2")
    repo_id = await _indexed(old)
    await _seed_repo_level_pages(old, repo_id, "repo2")
    _seed_fts(old, "repo_overview:repo2", "repo2")
    copy = tmp_path / "checkout"
    shutil.copytree(old, copy)

    assert await _upsert(copy, copy) == repo_id

    assert len(_rows(copy)) == 1
    pages = _pages(copy)
    overviews = [p for p in pages if p[0].startswith("repo_overview:")]
    assert overviews == [("repo_overview:checkout", "checkout", None)]
    assert ("architecture_diagram:checkout", "checkout", None) in pages
    # The child still hangs off the overview, under its new id.
    assert ("module_page:src", "src", "repo_overview:checkout") in pages
    # Versions and the FTS row moved with the page; nothing stays behind.
    assert _version_page_ids(copy) == [
        "architecture_diagram:checkout",
        "repo_overview:checkout",
    ]
    assert _fts_rows(copy) == [("repo_overview:checkout", "checkout")]
    assert not any(p[0].endswith(":repo2") for p in pages)


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
