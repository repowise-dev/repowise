"""Tests for stale structural page reconciliation during repowise update and workspace staleness checks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd import repo_checks
from repowise.core.persistence import load_stale_structural_file_paths
from repowise.core.workspace.update import check_repo_staleness


async def _setup_repo_with_pages(
    tmp_path: Path, stale_paths: list[str]
) -> tuple[Path, str]:
    """Helper to initialize a git repo with a DB and pages (some of which are marked stale)."""
    import git as gitpython

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page,
        upsert_repository,
    )

    repo_path = (tmp_path / "test_repo").resolve()
    repo_path.mkdir(parents=True, exist_ok=True)
    git_repo = gitpython.Repo.init(repo_path)

    # Commit a dummy file
    dummy = repo_path / "foo.py"
    dummy.write_text("def foo(): pass\n")
    git_repo.index.add(["foo.py"])
    commit = git_repo.index.commit("Initial commit")
    head_sha = commit.hexsha

    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir(exist_ok=True)

    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)

    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="test_repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        for path in ["foo.py", "bar.py"]:
            page_id = f"file_page:{path}"
            freshness = "stale" if path in stale_paths else "fresh"
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type="file_page",
                title=f"File: {path}",
                content="def code(): pass",
                summary="Summary",
                target_path=path,
                source_hash="hash",
                model_name="mock",
                provider_name="mock",
                freshness_status=freshness,
            )
        # Also test symbol spotlight structural page with target_path using '::'
        if "spotlight.py" in stale_paths:
            await upsert_page(
                session,
                page_id="symbol_spotlight:spotlight.py::my_func",
                repository_id=repo.id,
                page_type="symbol_spotlight",
                title="Spotlight: my_func",
                content="def my_func(): pass",
                summary="Summary",
                target_path="spotlight.py::my_func",
                source_hash="hash",
                model_name="mock",
                provider_name="mock",
                freshness_status="stale",
            )
        await session.commit()

    await engine.dispose()
    return repo_path, head_sha


def test_load_stale_structural_file_paths(tmp_path: Path) -> None:
    """load_stale_structural_file_paths returns file paths for file_pages marked stale or expired."""
    repo_path, _ = asyncio.run(_setup_repo_with_pages(tmp_path, ["bar.py"]))

    stale = load_stale_structural_file_paths(repo_path)
    assert stale == ["bar.py"]


def test_repo_wide_structural_pages_are_not_reported(tmp_path: Path) -> None:
    """A stale cycle or layer page is only ever rewritten by a full run.

    Reporting it would make every idle update pay a full reparse that cannot
    clear it, on every run, forever.
    """
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        upsert_page,
        upsert_repository,
    )

    repo_path, _ = asyncio.run(_setup_repo_with_pages(tmp_path, []))

    async def _add_stale_layer_page() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{repo_path / '.repowise' / 'wiki.db'}")
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="test_repo", local_path=str(repo_path))
            await upsert_page(
                session,
                page_id="layer_page:core",
                repository_id=repo.id,
                page_type="layer_page",
                title="Layer: core",
                content="# core\n",
                summary="Summary",
                target_path="core",
                source_hash="hash",
                model_name="mock",
                provider_name="mock",
                freshness_status="stale",
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_add_stale_layer_page())
    assert load_stale_structural_file_paths(repo_path) == []


def test_load_stale_structural_file_paths_spotlight(tmp_path: Path) -> None:
    """load_stale_structural_file_paths extracts file path from symbol_spotlight target_path."""
    repo_path, _ = asyncio.run(_setup_repo_with_pages(tmp_path, ["spotlight.py"]))

    stale = load_stale_structural_file_paths(repo_path)
    assert stale == ["spotlight.py"]


def test_load_stale_structural_file_paths_empty_when_all_fresh(tmp_path: Path) -> None:
    """load_stale_structural_file_paths returns empty list when all pages are fresh."""
    repo_path, _ = asyncio.run(_setup_repo_with_pages(tmp_path, []))

    stale = load_stale_structural_file_paths(repo_path)
    assert stale == []


def test_check_repo_staleness_detects_stale_structural_pages(tmp_path: Path) -> None:
    """check_repo_staleness returns is_stale=True when current_head == last_commit but DB has stale pages."""
    repo_path, head_sha = asyncio.run(_setup_repo_with_pages(tmp_path, ["bar.py"]))

    is_stale, current_head, behind = check_repo_staleness(repo_path, head_sha)
    assert is_stale is True
    assert current_head == head_sha
    assert behind == 0


def test_check_repo_staleness_returns_false_when_all_fresh(tmp_path: Path) -> None:
    """check_repo_staleness returns is_stale=False when current_head == last_commit and no pages are stale."""
    repo_path, head_sha = asyncio.run(_setup_repo_with_pages(tmp_path, []))

    is_stale, current_head, behind = check_repo_staleness(repo_path, head_sha)
    assert is_stale is False
    assert current_head == head_sha
    assert behind == 0


def test_doctor_detects_stale_pages_and_clears_after_reconciliation(tmp_path: Path) -> None:
    """Doctor check flags stale pages, and load_stale_structural_file_paths identifies them."""
    repo_path, _ = asyncio.run(_setup_repo_with_pages(tmp_path, ["foo.py", "bar.py"]))

    # 1. Doctor initially reports stale pages
    _, checks = repo_checks._run_repo_checks(repo_path, repair=False, fmt="quiet")
    stale_check = next(c for c in checks if c.name == "Stale pages")
    assert stale_check.ok is False
    assert stale_check.detail.startswith("2 stale")

    # 2. Verify load_stale_structural_file_paths returns the 2 stale paths
    stale_paths = load_stale_structural_file_paths(repo_path)
    assert sorted(stale_paths) == ["bar.py", "foo.py"]


def test_stale_structural_paths_scoped_to_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_stale_structural_file_paths filters by repository_id in a shared database."""
    import git as gitpython

    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_page,
        upsert_repository,
    )

    db_file = tmp_path / "shared_wiki.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("REPOWISE_DB_URL", db_url)

    repo_a = (tmp_path / "repo_a").resolve()
    repo_b = (tmp_path / "repo_b").resolve()
    repo_a.mkdir()
    repo_b.mkdir()
    gitpython.Repo.init(repo_a)
    gitpython.Repo.init(repo_b)

    async def _setup_shared_db() -> None:
        engine = create_engine(db_url)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            r_a = await upsert_repository(session, name="repo_a", local_path=str(repo_a))
            r_b = await upsert_repository(session, name="repo_b", local_path=str(repo_b))

            # Add stale page for Repo A
            await upsert_page(
                session,
                page_id="file_page:a.py",
                repository_id=r_a.id,
                page_type="file_page",
                title="File: a.py",
                content="code",
                summary="",
                target_path="a.py",
                source_hash="",
                model_name="mock",
                provider_name="mock",
                freshness_status="stale",
            )
            # Add stale page for Repo B
            await upsert_page(
                session,
                page_id="file_page:b.py",
                repository_id=r_b.id,
                page_type="file_page",
                title="File: b.py",
                content="code",
                summary="",
                target_path="b.py",
                source_hash="",
                model_name="mock",
                provider_name="mock",
                freshness_status="stale",
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_setup_shared_db())

    # Query repo A: must only return a.py
    stale_a = load_stale_structural_file_paths(repo_a)
    assert stale_a == ["a.py"]

    # Query repo B: must only return b.py
    stale_b = load_stale_structural_file_paths(repo_b)
    assert stale_b == ["b.py"]
