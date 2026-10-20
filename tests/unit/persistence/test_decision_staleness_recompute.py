"""Staleness is scored against the repo, not against one run's change set.

``recompute_decision_staleness`` is handed the git metadata the *current* run
produced. On an incremental update that is only the files that changed in it,
and the scorer reads a file with no entry as a file that is gone (1.00). So
every decision over a file that simply wasn't touched went maximally stale for
not having been touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.crud import recompute_decision_staleness
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    Repository,
)

_REPO_ID = "repo1"


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes for a tz-aware column."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(Repository(id=_REPO_ID, name="r", local_path="/tmp/r"))
        await s.flush()
        yield s
    await engine.dispose()


async def _add_decision(session, *, files: list[str], staleness: float) -> DecisionRecord:
    import json

    rec = DecisionRecord(
        id="d1",
        repository_id=_REPO_ID,
        title="Use JWT tokens",
        decision="all service auth uses JWT tokens",
        status="active",
        staleness_score=staleness,
        affected_files_json=json.dumps(files),
        created_at=datetime.now(UTC) - timedelta(days=30),
    )
    session.add(rec)
    await session.flush()
    return rec


async def _add_git_metadata(session, path: str, *, days_ago: int, commits: int) -> None:
    session.add(
        GitMetadata(
            repository_id=_REPO_ID,
            file_path=path,
            commit_count_90d=commits,
            last_commit_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    await session.flush()


async def test_untouched_file_is_scored_from_persisted_metadata(session):
    """The file is absent from this run's map but present in the index."""
    await _add_decision(session, files=["src/untouched.py"], staleness=0.0)
    # Last committed *before* the decision was created, so nothing has moved
    # under it: the honest score is 0.0, not 1.00.
    await _add_git_metadata(session, "src/untouched.py", days_ago=60, commits=1)

    updated = await recompute_decision_staleness(
        session, _REPO_ID, {"src/changed.py": {"last_commit_at": None}}
    )

    rec = await session.get(DecisionRecord, "d1")
    assert updated == 0
    assert rec.staleness_score == pytest.approx(0.0)


async def test_this_runs_numbers_win_over_the_persisted_ones(session):
    """A file in both maps is scored from the run's fresher entry."""
    await _add_decision(session, files=["src/hot.py"], staleness=0.0)
    await _add_git_metadata(session, "src/hot.py", days_ago=60, commits=0)

    await recompute_decision_staleness(
        session,
        _REPO_ID,
        {
            "src/hot.py": {
                "last_commit_at": datetime.now(UTC),  # committed just now
                "commit_count_90d": 30,
            }
        },
    )

    rec = await session.get(DecisionRecord, "d1")
    assert rec.staleness_score > 0.5


async def test_file_the_index_has_never_seen_still_scores_stale(session):
    """Absent from both maps is the case the 1.00 was written for."""
    await _add_decision(session, files=["src/deleted.py"], staleness=0.0)

    updated = await recompute_decision_staleness(
        session, _REPO_ID, {"src/changed.py": {"last_commit_at": None}}
    )

    rec = await session.get(DecisionRecord, "d1")
    assert updated == 1
    assert rec.staleness_score == pytest.approx(1.0)


# -- module-linkage backfill --------------------------------------------------
#
# The rows a user already has carry the first-path-segment scope, which in a
# packages/ layout is `packages` or `tests` for nearly every record. This is
# the named migration for them: re-derived in the repair pass that already
# runs on every update, not in a data migration that would have to agree with
# the runtime path forever.


async def test_existing_rows_have_their_module_scope_re_derived(session):
    import json

    rec = await _add_decision(session, files=["packages/core/src/pipeline/persist.py"], staleness=0.0)
    rec.affected_modules_json = json.dumps(["packages"])  # the pre-change shape
    await session.flush()
    await _add_git_metadata(session, "packages/core/src/pipeline/persist.py", days_ago=60, commits=1)

    await recompute_decision_staleness(session, _REPO_ID, {})

    rec = await session.get(DecisionRecord, "d1")
    assert json.loads(rec.affected_modules_json) == ["packages/core/src/pipeline"]


async def test_backfill_is_not_reported_as_a_rescore(session):
    """The count callers print is "N decisions rescored"; a silent repair is not one."""
    import json

    rec = await _add_decision(session, files=["a/b/c.py"], staleness=0.0)
    rec.affected_modules_json = json.dumps(["a"])
    await session.flush()
    await _add_git_metadata(session, "a/b/c.py", days_ago=60, commits=1)

    updated = await recompute_decision_staleness(session, _REPO_ID, {})

    assert updated == 0
    rec = await session.get(DecisionRecord, "d1")
    assert json.loads(rec.affected_modules_json) == ["a/b"]


async def test_a_record_naming_no_file_keeps_its_scope_rather_than_inventing_one(session):
    import json

    rec = await _add_decision(session, files=[], staleness=0.0)
    rec.affected_modules_json = json.dumps(["packages"])
    await session.flush()

    await recompute_decision_staleness(session, _REPO_ID, {})

    rec = await session.get(DecisionRecord, "d1")
    assert json.loads(rec.affected_modules_json) == ["packages"]


# --- last_code_change rides the same pass ----------------------------------


async def test_the_date_is_the_latest_commit_across_the_files_it_governs(session):
    await _add_decision(session, files=["src/a.py", "src/b.py"], staleness=0.0)
    await _add_git_metadata(session, "src/a.py", days_ago=60, commits=1)
    await _add_git_metadata(session, "src/b.py", days_ago=5, commits=1)

    await recompute_decision_staleness(session, _REPO_ID, {})

    rec = await session.get(DecisionRecord, "d1")
    expected = datetime.now(UTC) - timedelta(days=5)
    assert rec.last_code_change is not None
    assert abs(_aware(rec.last_code_change) - expected) < timedelta(minutes=1)


async def test_a_record_naming_nothing_tracked_keeps_no_date(session):
    """It scores 1.00 stale on the same evidence, so a date would be invented."""
    await _add_decision(session, files=["src/untracked.py"], staleness=0.0)

    await recompute_decision_staleness(session, _REPO_ID, {})

    rec = await session.get(DecisionRecord, "d1")
    assert rec.last_code_change is None
    assert rec.staleness_score == pytest.approx(1.0)


async def test_the_date_does_not_churn_updated_at_on_a_second_pass(session):
    """SQLite drops tzinfo, so a naive round trip would differ from the aware
    value that wrote it and re-stamp every record on every run."""
    await _add_decision(session, files=["src/a.py"], staleness=0.0)
    await _add_git_metadata(session, "src/a.py", days_ago=60, commits=1)

    await recompute_decision_staleness(session, _REPO_ID, {})
    rec = await session.get(DecisionRecord, "d1")
    first_date, first_stamp = rec.last_code_change, rec.updated_at

    # Force the round trip the guard exists for: the second pass has to read
    # the naive value back out of SQLite, not the aware one still in memory.
    session.expire_all()
    await recompute_decision_staleness(session, _REPO_ID, {})
    await session.refresh(rec)

    assert rec.last_code_change == first_date
    assert rec.updated_at == first_stamp


async def test_a_conventions_record_still_gets_a_date(session):
    """The conventions skip protects its own staleness, which is a score. A
    date is a fact about the code and is filled for every scoped record."""
    rec = await _add_decision(session, files=["src/a.py"], staleness=0.42)
    rec.source = "conventions"
    await session.flush()
    await _add_git_metadata(session, "src/a.py", days_ago=3, commits=1)

    await recompute_decision_staleness(session, _REPO_ID, {})
    await session.refresh(rec)

    assert rec.last_code_change is not None
    assert rec.staleness_score == pytest.approx(0.42), "its own score is untouched"
