"""The lanes ``get_decision_health_summary`` counted and then discarded.

``counts`` reported them as a number and named none of the records, although
each is in hand at the ``continue`` that drops it. ``active`` stays count-only
on purpose, so that has a test too.
"""

from __future__ import annotations

import json

from repowise.core.persistence.crud import get_decision_health_summary
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import insert_repo


async def _add(
    session,
    repo_id: str,
    *,
    rec_id: str,
    status: str,
    confidence: float = 1.0,
    files: list[str] | None = None,
) -> DecisionRecord:
    record = DecisionRecord(
        id=rec_id,
        repository_id=repo_id,
        title=f"Decision {rec_id}",
        decision=f"{rec_id} because reasons",
        rationale=f"the alternative to {rec_id} was measured worse",
        status=status,
        confidence=confidence,
        affected_files_json=json.dumps(files or []),
        source="changelog",
    )
    session.add(record)
    await session.flush()
    return record


async def test_retired_records_are_named_not_only_counted(async_session):
    repo = await insert_repo(async_session)
    for rec_id, status in (("s1", "superseded"), ("d1", "deprecated"), ("x1", "dismissed")):
        await _add(async_session, repo.id, rec_id=rec_id, status=status)

    health = await get_decision_health_summary(async_session, repo.id)

    assert health["summary"]["superseded"] == 1
    assert health["summary"]["deprecated"] == 1
    assert health["summary"]["dismissed"] == 1
    # Ranked by lane, history before tombstone, per DECISION_STATUS_ORDER.
    assert [(lane, d.id) for lane, d in health["retired_decisions"]] == [
        ("superseded", "s1"),
        ("deprecated", "d1"),
        ("dismissed", "x1"),
    ]


async def test_every_counted_retired_record_is_named(async_session):
    """A list naming only some of what it counted is the same defect in a list."""
    repo = await insert_repo(async_session)
    for index in range(4):
        await _add(async_session, repo.id, rec_id=f"s{index}", status="superseded")
    for index in range(3):
        await _add(async_session, repo.id, rec_id=f"x{index}", status="dismissed")

    health = await get_decision_health_summary(async_session, repo.id)
    counts = health["summary"]

    assert counts["superseded"] + counts["deprecated"] + counts["dismissed"] == len(
        health["retired_decisions"]
    )


async def test_an_accepted_record_naming_no_file_is_named_as_unscoped(async_session):
    """Accepted, so not in the proposed queue; names no file, so path mode
    cannot reach it either.
    """
    from repowise.core.persistence.crud.authority import accept_decision

    repo = await insert_repo(async_session)
    record = await _add(async_session, repo.id, rec_id="u1", status="active", confidence=0.4)
    # An acceptance must name a scope, so accept with one then strip it:
    # ``uncheckable`` is derived from the record, not the acceptance.
    await accept_decision(
        async_session, record, accepter="test", evidence=["seed:u1"], scope=["src/u1.py"]
    )
    record.affected_files_json = "[]"
    record.affected_modules_json = "[]"
    await async_session.flush()

    health = await get_decision_health_summary(async_session, repo.id)

    assert health["summary"]["unscoped"] == 1
    assert [d.id for d in health["unscoped_decisions"]] == ["u1"]


async def test_unscoped_records_come_back_most_confident_first(async_session):
    """Insertion order and id order both disagree with the answer."""
    from repowise.core.persistence.crud.authority import accept_decision

    repo = await insert_repo(async_session)
    for rec_id, confidence in (("a1", 0.30), ("b2", 0.55), ("c3", 0.80), ("d4", 0.95)):
        record = await _add(
            async_session, repo.id, rec_id=rec_id, status="active", confidence=confidence
        )
        await accept_decision(
            async_session,
            record,
            accepter="test",
            evidence=[f"seed:{rec_id}"],
            scope=[f"src/{rec_id}.py"],
        )
        record.affected_files_json = "[]"
        record.affected_modules_json = "[]"
        record.confidence = confidence
        await async_session.flush()

    health = await get_decision_health_summary(async_session, repo.id)

    assert [d.id for d in health["unscoped_decisions"]] == ["d4", "c3", "b2", "a1"]


async def test_a_governing_record_is_in_neither_new_lane(async_session):
    """The discriminator: the new lists must not absorb an active decision."""
    from repowise.core.persistence.crud.authority import accept_decision

    repo = await insert_repo(async_session)
    record = await _add(
        async_session, repo.id, rec_id="g1", status="active", files=["src/g1.py"]
    )
    await accept_decision(
        async_session, record, accepter="test", evidence=["seed:g1"], scope=["src/g1.py"]
    )
    await async_session.flush()

    health = await get_decision_health_summary(async_session, repo.id)

    assert health["summary"]["active"] == 1
    assert health["retired_decisions"] == []
    assert health["unscoped_decisions"] == []
