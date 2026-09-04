"""``get_decision_health_summary`` ranks the lists its callers truncate.

Four surfaces read these lists: the MCP health dashboard, ``repowise decision
health``, the overview attention panel and the decisions route. None of them
ranks. Every score that orders them already rides on the record.

Each fixture below is built so that insertion order, id order and path order
all disagree with score order. That is not decoration: the sibling defect in
``used_by`` passed its first test because SQLite happened to serve the join in
path order, so a fixture whose natural order matches the wanted order cannot
tell a ranked implementation from an unranked one.

Measured here while proving these red: this scan comes back in primary-key
order on SQLite rather than insertion order. A fixture inserted ``d4, b2, c3, a1``
was served ``a1, b2, c3, d4``. So the id tie-break is untestable through this
function on this backend, since its correct output is the order the unranked
code already produced. It is in the sort key for a different reason: a total
key is what makes the result the same on a backend that does not do this. A
test asserting it here would pass against the code it exists to outlaw.
"""

from __future__ import annotations

import json

from repowise.core.persistence.crud import get_decision_health_summary
from repowise.core.persistence.models import DecisionRecord, GitMetadata
from tests.unit.persistence.helpers import insert_repo


async def _add_decision(
    session,
    repo_id: str,
    *,
    rec_id: str,
    title: str,
    status: str,
    staleness: float = 0.0,
    confidence: float = 1.0,
    files: list[str] | None = None,
) -> None:
    """Add a record, and accept it when the caller asked for a governing one.

    ``status="active"`` alone no longer makes a record govern: the summary
    counts the acceptance, so a fixture that only set the column would seed
    candidates and prove nothing about the lists this file is about.
    """
    record = DecisionRecord(
        id=rec_id,
        repository_id=repo_id,
        title=title,
        decision=f"{title} because reasons",
        status=status,
        staleness_score=staleness,
        confidence=confidence,
        affected_files_json=json.dumps(files or []),
        source="changelog",
    )
    session.add(record)
    await session.flush()
    if status == "active":
        from repowise.core.persistence.crud.authority import accept_decision

        # An acceptance has to name what it governs, so a fixture that gave no
        # files gets one. Without it the record accepts as ``uncheckable``,
        # which is not the currency any of these tests is about.
        await accept_decision(
            session,
            record,
            accepter="test",
            evidence=[f"seed:{rec_id}"],
            scope=files or [f"src/{rec_id}.py"],
        )
        # The scope ``accept_decision`` wrote stands: it is what makes the
        # record checkable, and wiping it back to empty would derive
        # ``uncheckable`` instead of the currency each test is about. Staleness
        # is restored because the acceptance does not carry it.
        record.staleness_score = staleness
        await session.flush()


async def _add_hotspot(session, repo_id: str, path: str, score: float | None) -> None:
    session.add(
        GitMetadata(
            repository_id=repo_id,
            file_path=path,
            is_hotspot=True,
            temporal_hotspot_score=score,
        )
    )
    await session.flush()


async def test_stale_decisions_come_back_worst_first(async_session):
    repo = await insert_repo(async_session)
    # Inserted least-stale first, and the ids ascend the same way, so scan order
    # and id order are both the exact reverse of the answer.
    for rec_id, staleness in (("a1", 0.55), ("b2", 0.70), ("c3", 0.85), ("d4", 0.95)):
        await _add_decision(
            async_session,
            repo.id,
            rec_id=rec_id,
            title=f"Decision {rec_id}",
            status="active",
            staleness=staleness,
        )

    health = await get_decision_health_summary(async_session, repo.id)

    assert [d.id for d in health["stale_decisions"]] == ["d4", "c3", "b2", "a1"]


async def test_proposed_decisions_come_back_most_confident_first(async_session):
    repo = await insert_repo(async_session)
    for rec_id, confidence in (("a1", 0.30), ("b2", 0.55), ("c3", 0.80), ("d4", 0.95)):
        await _add_decision(
            async_session,
            repo.id,
            rec_id=rec_id,
            title=f"Proposal {rec_id}",
            status="proposed",
            confidence=confidence,
        )

    health = await get_decision_health_summary(async_session, repo.id)

    assert [d.id for d in health["proposed_awaiting_review"]] == ["d4", "c3", "b2", "a1"]


async def test_ungoverned_hotspots_come_back_hottest_first(async_session):
    repo = await insert_repo(async_session)
    # Paths sort alphabetically in the exact reverse of their heat, which is the
    # order this list used to be served in.
    await _add_hotspot(async_session, repo.id, "src/a_barely_warm.py", 0.10)
    await _add_hotspot(async_session, repo.id, "src/m_middling.py", 0.50)
    await _add_hotspot(async_session, repo.id, "src/z_on_fire.py", 0.90)

    health = await get_decision_health_summary(async_session, repo.id)

    assert health["ungoverned_hotspots"] == [
        "src/z_on_fire.py",
        "src/m_middling.py",
        "src/a_barely_warm.py",
    ]


async def test_a_hotspot_with_no_score_sorts_last_rather_than_raising(async_session):
    """``temporal_hotspot_score`` is nullable, unlike the two decision keys."""
    repo = await insert_repo(async_session)
    await _add_hotspot(async_session, repo.id, "src/aaa_unscored.py", None)
    await _add_hotspot(async_session, repo.id, "src/zzz_scored.py", 0.40)

    health = await get_decision_health_summary(async_session, repo.id)

    assert health["ungoverned_hotspots"] == ["src/zzz_scored.py", "src/aaa_unscored.py"]


async def test_a_governed_hotspot_is_still_excluded(async_session):
    """Ranking must not change which hotspots count as ungoverned."""
    repo = await insert_repo(async_session)
    await _add_hotspot(async_session, repo.id, "src/governed.py", 0.99)
    await _add_hotspot(async_session, repo.id, "src/ungoverned.py", 0.10)
    await _add_decision(
        async_session,
        repo.id,
        rec_id="g1",
        title="Governs the hot one",
        status="active",
        files=["src/governed.py"],
    )

    health = await get_decision_health_summary(async_session, repo.id)

    assert health["ungoverned_hotspots"] == ["src/ungoverned.py"]


async def test_ranking_does_not_drop_or_duplicate_a_record(async_session):
    """The sort re-orders the lists; it must not change what is in them."""
    repo = await insert_repo(async_session)
    for rec_id, status in (("a1", "active"), ("b2", "proposed"), ("c3", "active")):
        await _add_decision(
            async_session,
            repo.id,
            rec_id=rec_id,
            title=f"Decision {rec_id}",
            status=status,
            staleness=0.80 if status == "active" else 0.0,
        )

    health = await get_decision_health_summary(async_session, repo.id)

    assert sorted(d.id for d in health["stale_decisions"]) == ["a1", "c3"]
    assert [d.id for d in health["proposed_awaiting_review"]] == ["b2"]
    assert health["summary"]["stale"] == 2
