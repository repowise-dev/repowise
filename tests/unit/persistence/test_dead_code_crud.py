"""Unit tests for dead-code findings persistence (file-scoped upsert)."""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.persistence.crud import (
    save_dead_code_findings,
    upsert_dead_code_findings,
)
from repowise.core.persistence.models import DeadCodeFinding
from tests.unit.persistence.helpers import insert_repo


def _finding(file_path: str, symbol: str, kind: str = "unused_export") -> dict:
    return {
        "kind": kind,
        "file_path": file_path,
        "symbol_name": symbol,
        "symbol_kind": "function",
        "confidence": 1.0,
        "reason": "test",
        "last_commit_at": None,
        "commit_count_90d": 0,
        "lines": 1,
        "package": None,
        "evidence": [],
        "safe_to_delete": True,
        "primary_owner": None,
        "age_days": None,
    }


async def _findings_by_file(session, repo_id: str) -> list[tuple[str, str]]:
    rows = (
        await session.execute(
            select(DeadCodeFinding).where(DeadCodeFinding.repository_id == repo_id)
        )
    ).scalars().all()
    return sorted((r.file_path, r.symbol_name) for r in rows)


async def test_upsert_dead_code_findings_scopes_by_file(async_session):
    """Re-indexing one file replaces only that file's findings; others survive."""
    repo = await insert_repo(async_session)
    await save_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "fa"), _finding("b.py", "fb")]
    )
    await async_session.commit()

    await upsert_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "fa2")], file_paths=["a.py"]
    )
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [
        ("a.py", "fa2"),
        ("b.py", "fb"),
    ]


async def test_upsert_dead_code_findings_clears_changed_file_with_no_findings(async_session):
    """A changed file that is now clean must have its old findings removed."""
    repo = await insert_repo(async_session)
    await save_dead_code_findings(async_session, repo.id, [_finding("a.py", "fa")])
    await async_session.commit()

    await upsert_dead_code_findings(async_session, repo.id, [], file_paths=["a.py"])
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == []


async def test_upsert_dead_code_findings_ignores_out_of_scope_findings(async_session):
    """Findings whose file_path is outside file_paths are not inserted."""
    repo = await insert_repo(async_session)

    await upsert_dead_code_findings(
        async_session,
        repo.id,
        [_finding("a.py", "fa"), _finding("b.py", "fb")],
        file_paths=["a.py"],
    )
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [("a.py", "fa")]


async def test_upsert_dead_code_findings_noop_without_file_paths(async_session):
    """Empty file_paths is a no-op (nothing deleted, nothing inserted)."""
    repo = await insert_repo(async_session)
    await save_dead_code_findings(async_session, repo.id, [_finding("a.py", "fa")])
    await async_session.commit()

    await upsert_dead_code_findings(async_session, repo.id, [_finding("a.py", "x")], file_paths=[])
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [("a.py", "fa")]
