"""Unit tests for dead-code findings persistence (repo-wide replace).

The update path used to write findings for the changed files only, which meant
an unchanged file kept the verdict the last full index gave it even when the
change had just made it dead (or brought it back to life). The write is now
repo-wide, so the tests that bite here are the ones pinning (a) that a stale
verdict on an *unchanged* file is corrected, and (b) that a finding the user
dismissed is not resurrected by the wider write. The rest are regression
guards.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from repowise.core.persistence.crud import (
    replace_dead_code_findings,
    save_dead_code_findings,
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
        (
            await session.execute(
                select(DeadCodeFinding).where(DeadCodeFinding.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    return sorted((r.file_path, r.symbol_name) for r in rows)


async def _rows(session, repo_id: str) -> list[DeadCodeFinding]:
    return list(
        (
            await session.execute(
                select(DeadCodeFinding).where(DeadCodeFinding.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------
# The two that bite
# --------------------------------------------------------------------------


async def test_stale_verdict_on_an_unchanged_file_is_corrected(async_session):
    """The whole point of the change: a file nobody touched loses a verdict
    that is no longer true, and gains one that has become true.

    ``b.py`` is not in any change set here — under the old file-scoped write
    its stale finding survived every update until a full re-index.
    """
    repo = await insert_repo(async_session)
    await save_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "fa"), _finding("b.py", "stale")]
    )
    await async_session.commit()

    # A later analysis says b.py is clean and c.py has gone dead.
    await replace_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "fa"), _finding("c.py", "newly_dead")]
    )
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [
        ("a.py", "fa"),
        ("c.py", "newly_dead"),
    ]


async def test_dismissed_finding_is_not_resurrected(async_session):
    """A finding the user acted on stays acted on.

    The delete is scoped to ``status == "open"``, so the dismissed row
    survives it; without the second half of the guard the incoming finding
    would be re-inserted next to it as a fresh ``open`` duplicate, and the
    dismissal would be undone on every single update.
    """
    repo = await insert_repo(async_session)
    await save_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "fa"), _finding("b.py", "fb")]
    )
    await async_session.commit()

    dismissed = next(r for r in await _rows(async_session, repo.id) if r.file_path == "b.py")
    dismissed.status = "ignored"
    await async_session.commit()

    # The analyzer still reports it — it is still statically unreachable.
    await replace_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "fa"), _finding("b.py", "fb")]
    )
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    assert sorted((r.file_path, r.status) for r in rows) == [
        ("a.py", "open"),
        ("b.py", "ignored"),
    ]


# --------------------------------------------------------------------------
# Regression guards
# --------------------------------------------------------------------------


async def test_replace_clears_a_file_that_is_now_clean(async_session):
    repo = await insert_repo(async_session)
    await save_dead_code_findings(async_session, repo.id, [_finding("a.py", "fa")])
    await async_session.commit()

    await replace_dead_code_findings(async_session, repo.id, [])
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == []


async def test_replace_does_not_touch_another_repository(async_session):
    repo = await insert_repo(async_session)
    other = await insert_repo(async_session, name="other", local_path="/tmp/other")
    await save_dead_code_findings(async_session, other.id, [_finding("x.py", "fx")])
    await async_session.commit()

    await replace_dead_code_findings(async_session, repo.id, [_finding("a.py", "fa")])
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [("a.py", "fa")]
    assert await _findings_by_file(async_session, other.id) == [("x.py", "fx")]


async def test_dismissal_is_keyed_on_file_kind_and_symbol(async_session):
    """A dismissal covers one finding, not the whole file: a *different*
    symbol in the same file, and the same symbol under a different kind, are
    both still written."""
    repo = await insert_repo(async_session)
    await save_dead_code_findings(async_session, repo.id, [_finding("a.py", "one")])
    await async_session.commit()

    row = (await _rows(async_session, repo.id))[0]
    row.status = "ignored"
    await async_session.commit()

    await replace_dead_code_findings(
        async_session,
        repo.id,
        [
            _finding("a.py", "one"),
            _finding("a.py", "two"),
            _finding("a.py", "one", kind="unused_internal"),
        ],
    )
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    assert sorted((r.file_path, r.symbol_name, r.kind, r.status) for r in rows) == [
        ("a.py", "one", "unused_export", "ignored"),
        ("a.py", "one", "unused_internal", "open"),
        ("a.py", "two", "unused_export", "open"),
    ]


async def test_replace_accepts_dataclass_findings(async_session):
    """The workspace path hands over ``DeadCodeFindingData`` objects while the
    CLI path hands over ``dataclasses.asdict`` output, so both shapes have to
    key identically — including the ``DeadCodeKind`` member, which ``asdict``
    leaves as an enum rather than converting to its value."""
    from repowise.core.analysis.dead_code.models import DeadCodeKind

    @dataclass
    class _Data:
        kind: DeadCodeKind
        file_path: str
        symbol_name: str | None
        symbol_kind: str | None = "function"
        confidence: float = 1.0
        reason: str = "test"
        last_commit_at: object = None
        commit_count_90d: int = 0
        lines: int = 1
        start_line: int | None = None
        end_line: int | None = None
        package: str | None = None
        evidence: list = None  # type: ignore[assignment]
        safe_to_delete: bool = True
        primary_owner: str | None = None
        age_days: int | None = None

    repo = await insert_repo(async_session)
    await replace_dead_code_findings(
        async_session,
        repo.id,
        [_Data(kind=DeadCodeKind.UNUSED_EXPORT, file_path="a.py", symbol_name="fa", evidence=[])],
    )
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    assert [(r.file_path, r.symbol_name, r.kind) for r in rows] == [
        ("a.py", "fa", "unused_export")
    ]

    # ...and that row is now dismissible, i.e. the kind it stored is the value
    # the identity check compares against.
    rows[0].status = "ignored"
    await async_session.commit()
    await replace_dead_code_findings(
        async_session,
        repo.id,
        [_Data(kind=DeadCodeKind.UNUSED_EXPORT, file_path="a.py", symbol_name="fa", evidence=[])],
    )
    await async_session.commit()
    assert [r.status for r in await _rows(async_session, repo.id)] == ["ignored"]
