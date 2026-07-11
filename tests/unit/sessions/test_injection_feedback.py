"""Usage feedback v1: injected decisions judged followed or contradicted.

The augment hooks record shown-decision ids in the staging sidecar; at update
time the miner replays each showing session's mined user corrections against
the decision text. Contradiction bumps staleness (the evolution 'amended'
move), silence relaxes it (the 'reaffirmed' move), and every row is judged at
most once.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import DecisionRecord, Repository
from repowise.core.sessions.miners.decisions import (
    INJECTION_EVAL_MIN_AGE_SECONDS,
    apply_injection_feedback,
)
from repowise.core.sessions.staging import SessionStagingStore, default_store_path

_REPO_ID = "repo1"
_NOW = 1_000_000.0
_OLD_ENOUGH = _NOW - INJECTION_EVAL_MIN_AGE_SECONDS - 10


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.commit()


async def _add_decision(session, decision_id: str, *, staleness: float = 0.5) -> None:
    session.add(
        DecisionRecord(
            id=decision_id,
            repository_id=_REPO_ID,
            title="Use JWT tokens for service auth",
            decision="all service auth uses JWT tokens",
            status="active",
            staleness_score=staleness,
            evidence_file=decision_id,
        )
    )
    await session.flush()


def _record_injection(repo_root, session_id: str, decision_id: str, shown_at: float) -> None:
    """Insert an injection row the way the hook does (raw sqlite)."""
    with SessionStagingStore(default_store_path(repo_root)) as store:
        store._conn.execute(
            "INSERT OR IGNORE INTO injections (session_id, decision_id, shown_at) VALUES (?, ?, ?)",
            (session_id, decision_id, shown_at),
        )
        store.commit()


def _stage_correction(repo_root, session_id: str, quote: str) -> None:
    with SessionStagingStore(default_store_path(repo_root)) as store:
        store.add_raw(
            hash_=f"h-{session_id}-{abs(hash(quote)) % 10**8}",
            kind="user_correction",
            quotes=[quote],
            files=[],
            session_id=session_id,
            now=_OLD_ENOUGH,
        )
        store.commit()


async def test_uncontradicted_injection_counts_as_followed(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.5)
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 1, "contradicted": 0}
    rec = await session.get(DecisionRecord, "d1")
    assert rec.staleness_score == pytest.approx(0.2)

    # Judged once: a second pass finds nothing unevaluated.
    again = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)
    assert again == {"followed": 0, "contradicted": 0}


async def test_contradicting_correction_bumps_staleness(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.1)
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)
    _stage_correction(
        tmp_path, "sess-1", "no, stop using JWT tokens for service auth, revert to sessions"
    )

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 1}
    rec = await session.get(DecisionRecord, "d1")
    assert rec.staleness_score == pytest.approx(0.6)


async def test_unrelated_correction_still_counts_as_followed(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.5)
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)
    _stage_correction(tmp_path, "sess-1", "no, format the changelog with bullet points please")

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)
    assert summary == {"followed": 1, "contradicted": 0}


async def test_recent_injection_is_not_judged_yet(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.5)
    _record_injection(tmp_path, "sess-1", "d1", _NOW - 60)  # a minute ago

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 0}
    rec = await session.get(DecisionRecord, "d1")
    assert rec.staleness_score == pytest.approx(0.5)  # untouched
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert len(store.unevaluated_injections(before=_NOW)) == 1  # still pending


async def test_vanished_decision_row_is_drained_not_retried(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await session.flush()
    _record_injection(tmp_path, "sess-1", "gone", _OLD_ENOUGH)

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 0}
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.unevaluated_injections(before=_NOW) == []


def test_hook_written_injections_table_is_schema_compatible(tmp_path):
    """The hook's raw CREATE TABLE and the staging schema must agree."""
    from repowise.cli.commands.augment_cmd.decision_inject import _record_injections

    # Hook writes first (cold sidecar), store opens the same DB afterwards.
    _record_injections(tmp_path, "sess-1", ["d1"], node_id="src/a.py")
    db = tmp_path / ".repowise" / "sessions" / "sessions.db"
    assert db.exists()
    with SessionStagingStore(db) as store:
        rows = store.unevaluated_injections(before=9e12)
        assert [(r["session_id"], r["decision_id"], r["node_id"]) for r in rows] == [
            ("sess-1", "d1", "src/a.py")
        ]
        store.mark_injection_evaluated("sess-1", "d1")
        store.commit()
        assert store.unevaluated_injections(before=9e12) == []

    # And the reverse order: store-created schema accepts hook writes.
    repo2 = tmp_path / "second"
    (repo2 / ".repowise").mkdir(parents=True)
    with SessionStagingStore(default_store_path(repo2)):
        pass
    _record_injections(repo2, "sess-2", ["d2"], node_id="")
    conn = sqlite3.connect(default_store_path(repo2))
    assert conn.execute("SELECT COUNT(*) FROM injections").fetchone()[0] == 1
    conn.close()
