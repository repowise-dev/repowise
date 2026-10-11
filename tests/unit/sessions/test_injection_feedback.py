"""Usage feedback v1: injected decisions judged followed or contradicted.

The augment hooks record shown-decision ids in the staging sidecar; at update
time the miner replays each showing session's mined user corrections against
the decision text. Contradiction is a verdict, and so is silence — but only
where a correction existed to be silent about. An injection no session could
have disagreed with is settled with no verdict, because counting it as
followed is what made the followed rate read 100%. Every row is judged at
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


async def test_injection_no_session_could_disagree_with_is_not_followed(session, tmp_path):
    """Silence is not agreement when nothing was ever mined to disagree with.

    "Followed" is the else branch of the contradiction test, so a session with
    no mined correction used to produce one for free. On this machine that
    read as 100 followed / 0 contradicted, with zero of the 100 coming from a
    session that held any correction at all.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.5)
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 0, "unjudgeable": 1}
    # The verdict lands on the injection ledger and nowhere else. It used to
    # also relax the record's staleness, which is now a measured fact about
    # whether the governed files moved — a per-machine session verdict may not
    # overwrite a value the dashboard and hosted both read.
    rec = await session.get(DecisionRecord, "d1")
    assert rec.staleness_score == pytest.approx(0.5)

    # Settled all the same, so it is not re-read on every future update.
    again = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)
    assert again == {"followed": 0, "contradicted": 0, "unjudgeable": 0}
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals()["no_verdict"] == 1


async def test_contradicting_correction_is_recorded_without_touching_staleness(
    session, tmp_path
):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.1)
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)
    _stage_correction(
        tmp_path, "sess-1", "no, stop using JWT tokens for service auth, revert to sessions"
    )

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 1, "unjudgeable": 0}
    # Contradiction is a judgement about the record; staleness is a fact about
    # the code. The first no longer writes the second.
    rec = await session.get(DecisionRecord, "d1")
    assert rec.staleness_score == pytest.approx(0.1)


async def test_unrelated_correction_still_counts_as_followed(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.5)
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)
    _stage_correction(tmp_path, "sess-1", "no, format the changelog with bullet points please")

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)
    assert summary == {"followed": 1, "contradicted": 0, "unjudgeable": 0}


async def test_recent_injection_is_not_judged_yet(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.5)
    _record_injection(tmp_path, "sess-1", "d1", _NOW - 60)  # a minute ago

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 0, "unjudgeable": 0}
    rec = await session.get(DecisionRecord, "d1")
    assert rec.staleness_score == pytest.approx(0.5)  # untouched
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert len(store.unevaluated_injections(before=_NOW)) == 1  # still pending


async def test_vanished_decision_row_is_drained_not_retried(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await session.flush()
    _record_injection(tmp_path, "sess-1", "gone", _OLD_ENOUGH)

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 0, "unjudgeable": 0}
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.unevaluated_injections(before=_NOW) == []


async def test_a_free_followed_verdict_from_an_older_ledger_is_retired(session, tmp_path):
    """Rows an older version judged are already evaluated; nothing re-reads them.

    So the repair has to reach backwards, or `hook stats` keeps reporting the
    rate the else branch produced — 100% here — forever.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1")
    await _add_decision(session, "d2")
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)
    _record_injection(tmp_path, "sess-2", "d2", _OLD_ENOUGH)
    _stage_correction(tmp_path, "sess-2", "no, put the changelog in reverse order")
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        for sid, did in (("sess-1", "d1"), ("sess-2", "d2")):
            store.mark_injection_evaluated(sid, did, verdict="followed")
        store.commit()

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    with SessionStagingStore(default_store_path(tmp_path)) as store:
        totals = store.decision_feedback_totals()
    # sess-2's verdict stands: a correction existed and did not disagree.
    assert totals == {"followed": 1, "contradicted": 0, "pending": 0, "no_verdict": 1}


async def test_one_judgeable_session_does_not_vouch_for_the_others(session, tmp_path):
    """Judgeability is per row; the totals count rows, so this is the whole point.

    A decision shown to twenty sessions, one of which happened to hold a
    correction, would otherwise book twenty followed verdicts off one session's
    evidence — the same free-verdict bug one level up.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1")
    _record_injection(tmp_path, "sess-a", "d1", _OLD_ENOUGH)
    _record_injection(tmp_path, "sess-b", "d1", _OLD_ENOUGH)
    _stage_correction(tmp_path, "sess-a", "no, put the changelog in reverse order")

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 1, "contradicted": 0, "unjudgeable": 1}
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals() == {
            "followed": 1,
            "contradicted": 0,
            "pending": 0,
            "no_verdict": 1,
        }


async def test_the_verdict_repair_does_not_run_twice(session, tmp_path):
    """Once is a repair; every pass would eat earned verdicts as raws expire.

    The test it applies is only true-forever for rows written under the old
    rule. Left running, `RAW_TTL_DAYS` pruning a correction would retire the
    verdict it had justified, so every real "followed" decays at 90 days.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1")
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)
    _stage_correction(tmp_path, "sess-1", "no, put the changelog in reverse order")

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals()["followed"] == 1
        # The correction ages out; the verdict it earned must survive it.
        store._conn.execute("DELETE FROM raw_candidates")
        store.commit()
        assert store.retire_unjudgeable_verdicts() == 0
        assert store.decision_feedback_totals()["followed"] == 1

    # And the mark is durable across processes, not just within this one.
    # It is written on the pass that retires nothing too, or the repair
    # re-arms and fires once RAW_TTL_DAYS prunes what justified a verdict.
    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals()["followed"] == 1


async def test_verdicts_are_kept_on_the_ledger_for_hook_stats(session, tmp_path):
    """The followed/contradicted split survives the run that computed it.

    Without a stored verdict the numbers existed only in one `update
    --verbose` line, so `repowise hook stats` — where someone goes to ask
    whether the layer works — had nothing to report.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1", staleness=0.5)
    await _add_decision(session, "d2", staleness=0.5)
    _record_injection(tmp_path, "sess-1", "d1", _OLD_ENOUGH)
    _record_injection(tmp_path, "sess-2", "d2", _OLD_ENOUGH)
    _record_injection(tmp_path, "sess-3", "d1", _NOW - 60)  # too recent to judge
    _record_injection(tmp_path, "sess-4", "d3", _OLD_ENOUGH)  # nothing to judge it against
    await _add_decision(session, "d3", staleness=0.5)
    _stage_correction(
        tmp_path, "sess-1", "no, stop using JWT tokens for service auth, revert to sessions"
    )
    _stage_correction(tmp_path, "sess-2", "no, put the changelog in reverse order")

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals() == {
            "followed": 1,  # d2: a correction existed and did not disagree
            "contradicted": 1,  # d1
            "pending": 1,  # d1 again, shown too recently in sess-3
            "no_verdict": 1,  # d3: sess-4 mined no correction at all
        }


async def test_drained_orphan_is_counted_as_neither_side(session, tmp_path):
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await session.flush()
    _record_injection(tmp_path, "sess-1", "gone", _OLD_ENOUGH)

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    with SessionStagingStore(default_store_path(tmp_path)) as store:
        totals = store.decision_feedback_totals()
    assert totals["no_verdict"] == 1
    assert totals["followed"] == totals["contradicted"] == 0


def test_hook_written_injections_table_is_schema_compatible(tmp_path):
    """The hook's raw CREATE TABLE and the staging schema must agree."""
    from repowise.cli.hook_ledger import _record_injections

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


async def test_a_contradiction_in_one_session_does_not_settle_another(session, tmp_path):
    """The verdict is per row, like judgeability already was.

    It used to be computed per decision and stored per row, so one session's
    match settled every row for that decision. The totals count rows, so the
    reported rate was that one match multiplied by however many sessions had
    been shown the decision. On this repository's store that was 20 rows from
    a single match, and it is the whole of the published 10.0%.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1")
    _record_injection(tmp_path, "sess-a", "d1", _OLD_ENOUGH)
    _record_injection(tmp_path, "sess-b", "d1", _OLD_ENOUGH)
    _stage_correction(
        tmp_path, "sess-a", "no, stop using JWT tokens for service auth, revert to sessions"
    )
    # sess-b mined a correction too, so it is judgeable — and it said nothing
    # about JWT. Its row must read followed, not sess-a's contradiction.
    _stage_correction(tmp_path, "sess-b", "no, put the changelog in reverse order")

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 1, "contradicted": 1, "unjudgeable": 0}
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        verdicts = dict(
            store._conn.execute(
                "SELECT session_id, verdict FROM injections WHERE decision_id = 'd1'"
            ).fetchall()
        )
    assert verdicts == {"sess-a": "contradicted", "sess-b": "followed"}


async def test_smeared_contradictions_from_an_older_ledger_are_reopened(session, tmp_path):
    """Rows the old rule marked contradicted are already evaluated.

    Nothing re-reads them, so without a repair reaching backwards the rate
    stays at whatever the smear produced. Re-opened rather than cleared, so a
    row is given the chance to earn its verdict again — which it keeps only
    while the correction that earned it is still there.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1")
    _record_injection(tmp_path, "sess-a", "d1", _OLD_ENOUGH)
    _record_injection(tmp_path, "sess-b", "d1", _OLD_ENOUGH)
    _stage_correction(
        tmp_path, "sess-a", "no, stop using JWT tokens for service auth, revert to sessions"
    )
    _stage_correction(tmp_path, "sess-b", "no, put the changelog in reverse order")
    # The smear, as the old version left it: both rows contradicted.
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        for sid in ("sess-a", "sess-b"):
            store.mark_injection_evaluated(sid, "d1", verdict="contradicted")
        store.commit()
        assert store.decision_feedback_totals()["contradicted"] == 2

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals() == {
            "followed": 1,  # sess-b, re-judged on its own correction
            "contradicted": 1,  # sess-a keeps the verdict it earned
            "pending": 0,
            "no_verdict": 0,
        }


async def test_the_smear_repair_does_not_run_twice(session, tmp_path):
    """Once is a repair; every pass would churn verdicts and lose some.

    The correction is left in place here on purpose. Deleting it first would
    make the pass return 0 through the evidence guard instead of the one-shot
    gate, and the test would pass whether or not the gate existed.

    The loss the gate prevents once the guard is in: a contradicted row whose
    session still holds its correction, but whose decision record has since
    been deleted with no alias. Re-opening that row re-judges it against a
    record that is not there, and it is drained with no verdict.
    """
    from repowise.core.persistence.models import DecisionRecord as _Rec

    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1")
    _record_injection(tmp_path, "sess-a", "d1", _OLD_ENOUGH)
    _stage_correction(
        tmp_path, "sess-a", "no, stop using JWT tokens for service auth, revert to sessions"
    )

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals()["contradicted"] == 1
        # Eligible on every count except the gate: the row is contradicted and
        # its correction is still there.
        assert store.reopen_smeared_contradictions() == 0

    # The record goes away, as a purge or an unaliased re-key would take it.
    await session.delete(await session.get(_Rec, "d1"))
    await session.flush()

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        assert store.decision_feedback_totals()["contradicted"] == 1


async def test_a_rekeyed_decision_id_still_resolves_through_its_alias(session, tmp_path):
    """A record's id moves when its scope does, and nothing rewrites the sidecar.

    Without the aliases the row reads as "the decision is gone" and is drained
    with no verdict, so an ordinary scope edit discards feedback already
    earned. Measured on this repository's store: 372 of 421 settled rows
    resolve only this way.
    """
    from repowise.core.persistence.models import DecisionAlias

    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "new-id")
    session.add(
        DecisionAlias(
            alias_id="old-id",
            repository_id=_REPO_ID,
            decision_id="new-id",
            reason="rekeyed",
        )
    )
    await session.flush()
    # The sidecar still holds the id that was live when the hook wrote it.
    _record_injection(tmp_path, "sess-1", "old-id", _OLD_ENOUGH)
    _stage_correction(
        tmp_path, "sess-1", "no, stop using JWT tokens for service auth, revert to sessions"
    )

    summary = await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    assert summary == {"followed": 0, "contradicted": 1, "unjudgeable": 0}


async def test_a_contradiction_whose_evidence_has_aged_out_is_not_reopened(
    session, tmp_path
):
    """Re-opening a row nothing can re-judge is a deletion, not a re-judgement.

    `prune` drops an unstructured `raw_candidates` row at RAW_TTL_DAYS while
    `correction_quotes` reads those rows regardless of `structured_key`, and
    `prune` runs earlier in the same update. So a genuinely earned
    contradiction can reach the repair with its evidence already gone, and
    re-opening it would settle it as no-verdict, which is unrecoverable.
    """
    session.add(Repository(id=_REPO_ID, name="r", local_path=str(tmp_path)))
    await _add_decision(session, "d1")
    _record_injection(tmp_path, "sess-a", "d1", _OLD_ENOUGH)
    # Earned under the old rule, and its correction has since been pruned.
    with SessionStagingStore(default_store_path(tmp_path)) as store:
        store.mark_injection_evaluated("sess-a", "d1", verdict="contradicted")
        store.commit()
        assert store.reopen_smeared_contradictions() == 0

    await apply_injection_feedback(session, _REPO_ID, tmp_path, now=_NOW)

    with SessionStagingStore(default_store_path(tmp_path)) as store:
        totals = store.decision_feedback_totals()
    assert totals["contradicted"] == 1
    assert totals["no_verdict"] == 0
