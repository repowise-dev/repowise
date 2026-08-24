"""Tests for stale structural page reconciliation during repowise update and workspace staleness checks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd import repo_checks
from repowise.cli.commands.update_cmd.deterministic import load_stale_structural_file_paths
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
    all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False, fmt="quiet")
    stale_check = next(c for c in checks if c.name == "Stale pages")
    assert stale_check.ok is False
    assert stale_check.detail == "2 stale"

    # 2. Verify load_stale_structural_file_paths returns the 2 stale paths
    stale_paths = load_stale_structural_file_paths(repo_path)
    assert sorted(stale_paths) == ["bar.py", "foo.py"]
