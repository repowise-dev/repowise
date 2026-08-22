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


async def test_scope_holds_back_files_the_caller_cannot_speak_for(async_session):
    """A caller with partial git metadata must not overwrite the rest of the
    repo with guesses.

    A file the analyzer has no git metadata for scores 0.7 /
    ``safe_to_delete=True`` however actively it is committed to, so writing
    that verdict is worse than writing nothing. Rows outside the scope are
    neither deleted nor inserted, and the file keeps what it had.
    """
    repo = await insert_repo(async_session)
    await save_dead_code_findings(
        async_session, repo.id, [_finding("in.py", "keep"), _finding("out.py", "untouched")]
    )
    await async_session.commit()

    await replace_dead_code_findings(
        async_session,
        repo.id,
        # The analyzer reports on both files, but only in.py was scorable.
        [_finding("in.py", "fresh"), _finding("out.py", "guess")],
        scope=frozenset({"in.py"}),
    )
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [
        ("in.py", "fresh"),
        ("out.py", "untouched"),
    ]


async def test_an_empty_scope_writes_nothing_at_all(async_session):
    """The floor: a run that could score no file leaves the index exactly as
    it found it rather than clearing it."""
    repo = await insert_repo(async_session)
    await save_dead_code_findings(async_session, repo.id, [_finding("a.py", "fa")])
    await async_session.commit()

    await replace_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "different")], scope=frozenset()
    )
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [("a.py", "fa")]


async def test_scope_none_still_means_the_whole_repository(async_session):
    repo = await insert_repo(async_session)
    await save_dead_code_findings(
        async_session, repo.id, [_finding("a.py", "fa"), _finding("b.py", "stale")]
    )
    await async_session.commit()

    await replace_dead_code_findings(async_session, repo.id, [_finding("a.py", "fa")], scope=None)
    await async_session.commit()

    assert await _findings_by_file(async_session, repo.id) == [("a.py", "fa")]


async def test_a_dismissal_outside_the_scope_is_left_alone(async_session):
    repo = await insert_repo(async_session)
    await save_dead_code_findings(async_session, repo.id, [_finding("out.py", "fo")])
    await async_session.commit()
    row = (await _rows(async_session, repo.id))[0]
    row.status = "ignored"
    await async_session.commit()

    await replace_dead_code_findings(
        async_session, repo.id, [_finding("out.py", "fo")], scope=frozenset({"in.py"})
    )
    await async_session.commit()

    assert [(r.file_path, r.status) for r in await _rows(async_session, repo.id)] == [
        ("out.py", "ignored")
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
    assert [(r.file_path, r.symbol_name, r.kind) for r in rows] == [("a.py", "fa", "unused_export")]

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


async def test_get_dead_code_git_fields_returns_the_scoring_columns(async_session):
    """The four columns dead-code confidence is scored from, per file.

    If this ever comes back empty the analyzer scores every file as "no
    commits" and marks it safe to delete, so the read itself needs a test and
    not only its callers.
    """
    from repowise.core.persistence.crud import get_dead_code_git_fields
    from repowise.core.persistence.models import GitMetadata

    repo = await insert_repo(async_session)
    other = await insert_repo(async_session, name="other", local_path="/tmp/other")
    async_session.add(
        GitMetadata(
            repository_id=repo.id,
            file_path="a.py",
            commit_count_90d=7,
            age_days=400,
            primary_owner_name="ada",
        )
    )
    async_session.add(GitMetadata(repository_id=other.id, file_path="elsewhere.py"))
    await async_session.commit()

    fields = await get_dead_code_git_fields(async_session, repo.id)

    assert set(fields) == {"a.py"}, "must not leak another repository's rows"
    assert fields["a.py"]["commit_count_90d"] == 7
    assert fields["a.py"]["age_days"] == 400
    assert fields["a.py"]["primary_owner_name"] == "ada"
    assert "last_commit_at" in fields["a.py"]


async def test_get_dead_code_git_fields_is_empty_for_an_unknown_repo(async_session):
    from repowise.core.persistence.crud import get_dead_code_git_fields

    assert await get_dead_code_git_fields(async_session, "nope") == {}


async def test_summary_uses_shared_confidence_thresholds(async_session, monkeypatch):
    from repowise.core.persistence.crud.analysis import dead_code as dead_code_crud

    monkeypatch.setattr(dead_code_crud, "SAFE_CONFIDENCE_THRESHOLD", 0.8)
    monkeypatch.setattr(dead_code_crud, "RISK_CAP_CONFIDENCE", 0.3)

    repo = await insert_repo(async_session)

    high_boundary = _finding("high_boundary.py", "high_boundary")
    high_boundary["confidence"] = 0.8
    medium_top_boundary = _finding("medium_top_boundary.py", "medium_top_boundary")
    medium_top_boundary["confidence"] = 0.75
    medium_bottom_boundary = _finding("medium_bottom_boundary.py", "medium_bottom_boundary")
    medium_bottom_boundary["confidence"] = 0.3
    low_boundary = _finding("low_boundary.py", "low_boundary")
    low_boundary["confidence"] = 0.25

    findings = [
        high_boundary,
        medium_top_boundary,
        medium_bottom_boundary,
        low_boundary,
    ]

    await save_dead_code_findings(async_session, repo.id, findings)

    summary = await dead_code_crud.get_dead_code_summary(async_session, repo.id)

    assert summary["confidence_summary"] == {
        "high": 1,
        "medium": 2,
        "low": 1,
    }


async def test_get_dead_code_git_fields_returns_last_commit_at_aware(async_session):
    """A stored timestamp must come back comparable to fresh git metadata.

    SQLite drops the offset from ``DateTime(timezone=True)``, so this read used
    to hand the analyzer a naive value. It merges those rows with freshly read
    git metadata, which is aware, and then does ``datetime.now(UTC) - value``
    when ageing a zombie package and ``>`` when picking the package's newest
    commit. Either raises ``TypeError: can't subtract offset-naive and
    offset-aware datetimes``, which the dead-code phase catches broadly — the
    whole analysis is skipped and every finding silently keeps its previous
    verdict on every incremental update.
    """
    from datetime import UTC, datetime

    from repowise.core.persistence.crud import get_dead_code_git_fields
    from repowise.core.persistence.models import GitMetadata

    repo = await insert_repo(async_session)
    async_session.add(
        GitMetadata(
            repository_id=repo.id,
            file_path="a.py",
            last_commit_at=datetime(2025, 12, 21, 0, 6, 49, tzinfo=UTC),
        )
    )
    await async_session.commit()

    stored = (await get_dead_code_git_fields(async_session, repo.id))["a.py"]["last_commit_at"]

    assert stored.tzinfo is not None, "a naive value here skips the whole dead-code analysis"
    # The two operations that raised, exactly as the analyzer performs them.
    assert (datetime.now(UTC) - stored).days >= 0
    assert stored > datetime(2020, 1, 1, tzinfo=UTC)
    assert stored == datetime(2025, 12, 21, 0, 6, 49, tzinfo=UTC), "must not shift the instant"
