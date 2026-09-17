"""The lanes ``get_decision_health_summary`` counted and then discarded.

``counts`` reported three superseded records and the summary named none of
them, although the record is in hand at the ``continue`` that drops it. Unlike
the lanes that are merely capped, nothing downstream could recover one: no
filter argument reaches them and no id was ever emitted to look one up with.

``active`` stays count-only deliberately, so there is a test for that too: its
ids reach a reader through the query and path modes, which is the property that
makes it the one count-only lane that was already recoverable.
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
    """The count and the list must describe the same population.

    A list that names some of what it counted is the same defect wearing a
    list: a reader still cannot get from the number to the records.
    """
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
    """``unscoped`` is the other lane no other mode can reach.

    The record is accepted, so it is not in the proposed queue, and it names no
    file, so path mode cannot find it either.
    """
    from repowise.core.persistence.crud.authority import accept_decision

    repo = await insert_repo(async_session)
    record = await _add(async_session, repo.id, rec_id="u1", status="active", confidence=0.4)
    # An acceptance has to name a scope, so the record is accepted with one and
    # then stripped: ``uncheckable`` is derived from the record naming nothing,
    # not from the acceptance.
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
