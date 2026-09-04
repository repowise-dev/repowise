"""Review lanes over the acceptance join.

The lane a record is in answers "who accepted this and does it still bind",
which the ``status`` column cannot: it is a projection every writer keeps in
step, so it agrees right up until something writes it without an acceptance.
These tests hold the two apart, including on a record stored ``active`` that
nobody ever accepted.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


async def _seed(
    session_factory,
    repo_id: str,
    *,
    title: str,
    status: str = "proposed",
    accept: bool = False,
    scope: list[str] | None = None,
    staleness: float = 0.0,
) -> str:
    """One record, accepted or not, independently of its status column."""
    async with get_session(session_factory) as session:
        rec = await crud.upsert_decision(
            session,
            repository_id=repo_id,
            title=title,
            status=status,
            context="ctx",
            decision="dec",
            rationale="why",
            source="inline_marker",
            affected_files=["src/app.py"] if scope is None else scope,
            evidence_file="src/app.py",
            confidence=0.5,
        )
        rec.staleness_score = staleness
        # ``upsert_decision`` refuses to write ``active``: extraction cannot
        # confer authority. A record stored active with no acceptance behind it
        # therefore only arises from a pre-split store, which is exactly the
        # seam under test, so write the column directly to reproduce one.
        if status == "active" and not accept:
            rec.status = "active"
        if accept:
            await crud.accept_decision(
                session, rec, accepter="tester", evidence=["seed"]
            )
            # accept_decision projects the column back to ``active``; restore
            # the staleness the seed asked for, which the projection does not
            # touch and ``effective_currency`` reads.
            rec.staleness_score = staleness
        await session.flush()
        return rec.id


async def _lane(client: AsyncClient, repo_id: str, lane: str) -> list[dict]:
    res = await client.get(
        f"/api/repos/{repo_id}/decisions", params={"lane": lane, "limit": 500}
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_an_active_status_without_an_acceptance_is_a_candidate(
    client: AsyncClient, app
) -> None:
    """The seam, at the wire. The column says active; nobody accepted it."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="Never accepted", status="active")

    candidates = await _lane(client, repo["id"], "candidates")
    governing = await _lane(client, repo["id"], "governing")

    assert [d["title"] for d in candidates] == ["Never accepted"]
    assert candidates[0]["status"] == "active"
    assert candidates[0]["currency"] is None
    assert governing == []


@pytest.mark.asyncio
async def test_an_accepted_record_governs_and_carries_its_currency(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="A rule", accept=True)

    governing = await _lane(client, repo["id"], "governing")

    assert [d["title"] for d in governing] == ["A rule"]
    assert governing[0]["currency"] == "active"


@pytest.mark.asyncio
async def test_a_decision_whose_files_moved_lands_in_needs_review(
    client: AsyncClient, app
) -> None:
    """It still governs. ``needs_review`` is a decision to re-read, not to drop."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="Drifted", accept=True, staleness=0.9)

    assert [d["title"] for d in await _lane(client, repo["id"], "needs_review")] == [
        "Drifted"
    ]
    assert [d["title"] for d in await _lane(client, repo["id"], "governing")] == [
        "Drifted"
    ]
    assert await _lane(client, repo["id"], "active") == []


@pytest.mark.asyncio
async def test_a_scopeless_acceptance_is_uncheckable_and_does_not_govern(
    client: AsyncClient, app
) -> None:
    """Only reachable through the manifest, which accepts with an artifact.

    The acceptance contract refuses a scopeless acceptance at the API, so this
    seeds one directly to prove the read side classifies it rather than
    reporting it as a rule.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    did = await _seed(sf, repo["id"], title="Names nothing", accept=True)
    async with get_session(sf) as session:
        rec = await crud.get_decision(session, did)
        rec.affected_files_json = "[]"
        rec.affected_modules_json = "[]"
        await session.flush()

    assert [d["title"] for d in await _lane(client, repo["id"], "uncheckable")] == [
        "Names nothing"
    ]
    assert await _lane(client, repo["id"], "governing") == []


@pytest.mark.asyncio
async def test_withdrawing_authority_moves_a_record_to_history(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    did = await _seed(sf, repo["id"], title="Retired", accept=True)

    res = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{did}", json={"status": "deprecated"}
    )
    assert res.status_code == 200, res.text

    assert [d["title"] for d in await _lane(client, repo["id"], "history")] == [
        "Retired"
    ]
    assert await _lane(client, repo["id"], "governing") == []


@pytest.mark.asyncio
async def test_a_dismissed_decision_stays_in_history(client: AsyncClient, app) -> None:
    """A withdrawn decision is history; a dismissed candidate is a tombstone.

    ``dismiss_candidate`` writes ``status="dismissed"`` for both, and the
    default listing hides that status, so the accepted one used to fall out of
    every lane and every badge while the History copy promised it was kept.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    did = await _seed(sf, repo["id"], title="Withdrawn rule", accept=True)
    await _seed(sf, repo["id"], title="Rejected guess")
    async with get_session(sf) as session:
        from repowise.core.persistence.crud.authority import dismiss_candidate

        for title in ("Withdrawn rule", "Rejected guess"):
            rec = await crud.find_decision_by_title(
                session,
                repo["id"],
                title,
                source="inline_marker",
                evidence_file="src/app.py",
            )
            await dismiss_candidate(session, rec, accepter="tester")
        await session.flush()

    assert [d["title"] for d in await _lane(client, repo["id"], "history")] == [
        "Withdrawn rule"
    ]
    assert await _lane(client, repo["id"], "candidates") == []
    counts = (
        await client.get(f"/api/repos/{repo['id']}/decisions/lane-counts")
    ).json()
    assert counts["history"] == 1
    assert counts["candidates"] == 0
    assert counts["total"] == 1
    assert did


@pytest.mark.asyncio
async def test_the_governing_lane_pages_over_its_own_rows(
    client: AsyncClient, app
) -> None:
    """It filters accepted rows by currency, so its page is cut after that.

    Cutting first returned an empty tab on a repository whose newest accepted
    records had all been superseded.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    from repowise.core.persistence.crud.authority import record_acceptance

    for i in range(4):
        did = await _seed(sf, repo["id"], title=f"Retired {i}", accept=True)
        async with get_session(sf) as session:
            rec = await crud.get_decision(session, did)
            await record_acceptance(
                session,
                rec,
                action="superseded",
                currency="superseded",
                accepter="tester",
                evidence=["seed"],
            )
            await session.flush()
    await _seed(sf, repo["id"], title="The only rule", accept=True)

    res = await client.get(
        f"/api/repos/{repo['id']}/decisions",
        params={"lane": "governing", "limit": 2},
    )

    assert res.status_code == 200, res.text
    assert [d["title"] for d in res.json()] == ["The only rule"]


@pytest.mark.asyncio
async def test_lane_counts_partition_the_repository(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="Rule", accept=True)
    await _seed(sf, repo["id"], title="Drifted", accept=True, staleness=0.9)
    await _seed(sf, repo["id"], title="Candidate one")
    await _seed(sf, repo["id"], title="Candidate two", status="active")

    res = await client.get(f"/api/repos/{repo['id']}/decisions/lane-counts")
    assert res.status_code == 200, res.text
    counts = res.json()

    assert counts["active"] == 1
    assert counts["needs_review"] == 1
    assert counts["uncheckable"] == 0
    assert counts["history"] == 0
    assert counts["candidates"] == 2
    # The five exclusive lanes add up, and governing rolls up the two that bind.
    assert (
        counts["active"]
        + counts["needs_review"]
        + counts["uncheckable"]
        + counts["history"]
        + counts["candidates"]
        == counts["total"]
    )
    assert counts["governing"] == 2


@pytest.mark.asyncio
async def test_lane_counts_and_status_counts_are_different_questions(
    client: AsyncClient, app
) -> None:
    """A record written straight to the column is counted by one and not the other."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="Never accepted", status="active")

    status_counts = (
        await client.get(f"/api/repos/{repo['id']}/decisions/counts")
    ).json()
    lane_counts = (
        await client.get(f"/api/repos/{repo['id']}/decisions/lane-counts")
    ).json()

    assert status_counts["active"] == 1
    assert lane_counts["active"] == 0
    assert lane_counts["candidates"] == 1


@pytest.mark.asyncio
async def test_a_lane_page_is_a_page_of_that_lane(client: AsyncClient, app) -> None:
    """The filter runs before the page is cut, not after it.

    Priority sort leads with accepted records, so a lane filtered after the cut
    returned an empty Candidates page on a store holding hundreds of them. This
    seeds more accepted records than the page holds and asks for the lane
    behind them.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    for i in range(6):
        await _seed(sf, repo["id"], title=f"Rule {i}", accept=True)
    await _seed(sf, repo["id"], title="The only candidate")

    res = await client.get(
        f"/api/repos/{repo['id']}/decisions",
        params={"lane": "candidates", "limit": 3},
    )

    assert res.status_code == 200, res.text
    assert [d["title"] for d in res.json()] == ["The only candidate"]


@pytest.mark.asyncio
async def test_a_derived_lane_pages_over_its_own_rows(
    client: AsyncClient, app
) -> None:
    """``needs_review`` is derived, so its page is cut after the derivation."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    for i in range(4):
        await _seed(sf, repo["id"], title=f"Fresh {i}", accept=True)
    for i in range(3):
        await _seed(
            sf, repo["id"], title=f"Drifted {i}", accept=True, staleness=0.9
        )

    first = await client.get(
        f"/api/repos/{repo['id']}/decisions",
        params={"lane": "needs_review", "limit": 2, "offset": 0},
    )
    second = await client.get(
        f"/api/repos/{repo['id']}/decisions",
        params={"lane": "needs_review", "limit": 2, "offset": 2},
    )

    titles = [d["title"] for d in first.json()] + [
        d["title"] for d in second.json()
    ]
    assert len(first.json()) == 2
    assert len(second.json()) == 1
    assert sorted(titles) == ["Drifted 0", "Drifted 1", "Drifted 2"]


@pytest.mark.asyncio
async def test_an_unknown_lane_is_rejected_rather_than_ignored(
    client: AsyncClient, app
) -> None:
    """A misspelt lane must not return an unfiltered page that reads as the lane."""
    repo = await create_test_repo(client)

    res = await client.get(
        f"/api/repos/{repo['id']}/decisions", params={"lane": "governng"}
    )

    assert res.status_code == 422


@pytest.mark.asyncio
async def test_the_lane_parameter_is_optional(client: AsyncClient, app) -> None:
    """Omitting it lists everything, so an older client keeps working."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="Rule", accept=True)
    await _seed(sf, repo["id"], title="Candidate")

    res = await client.get(f"/api/repos/{repo['id']}/decisions")

    assert res.status_code == 200
    assert len(res.json()) == 2


# ---------------------------------------------------------------------------
# Creating a record by hand
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creating_with_a_scope_records_an_acceptance(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)

    res = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Use JWT",
            "decision": "Issue signed JWTs",
            "affected_files": ["src/auth/service.py"],
        },
    )

    assert res.status_code == 201, res.text
    assert res.json()["status"] == "active"
    assert [d["title"] for d in await _lane(client, repo["id"], "governing")] == [
        "Use JWT"
    ]


@pytest.mark.asyncio
async def test_creating_without_a_scope_lands_a_candidate_rather_than_failing(
    client: AsyncClient, app
) -> None:
    """The form and the acceptance contract have to agree.

    An acceptance names what it governs, so a scopeless entry cannot be one.
    Refusing the write instead would throw away every field the author did
    fill in, and reported the gap in a failure toast after the fact; the
    record is kept as a candidate, which is what ``repowise decision add``
    does with the same input.
    """
    repo = await create_test_repo(client)

    res = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={"title": "Use JWT", "decision": "Issue signed JWTs"},
    )

    assert res.status_code == 201, res.text
    assert res.json()["status"] == "proposed"
    assert [d["title"] for d in await _lane(client, repo["id"], "candidates")] == [
        "Use JWT"
    ]
    assert await _lane(client, repo["id"], "governing") == []


@pytest.mark.asyncio
async def test_a_module_only_scope_is_enough_to_accept(
    client: AsyncClient, app
) -> None:
    """The scope is files *or* modules, the same pair the contract checks."""
    repo = await create_test_repo(client)

    res = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={
            "title": "Keep the resolver pure",
            "decision": "No I/O in resolvers",
            "affected_modules": ["src/resolvers"],
        },
    )

    assert res.status_code == 201, res.text
    assert res.json()["status"] == "active"


@pytest.mark.asyncio
async def test_recording_a_title_again_cannot_withdraw_its_scope(
    client: AsyncClient, app
) -> None:
    """The create endpoint upserts on the title, so it can hit a live decision.

    ``upsert_decision`` overwrites the scope with whatever the body carries. A
    second post of an accepted decision's title with no files therefore cleared
    the files it governed and left its acceptance row pointing at a record that
    no longer binds, from a call that says "create". Refuse instead.
    """
    repo = await create_test_repo(client)
    body = {
        "title": "Use JWT",
        "decision": "Issue signed JWTs",
        "affected_files": ["src/auth/service.py"],
    }
    first = await client.post(f"/api/repos/{repo['id']}/decisions", json=body)
    assert first.status_code == 201, first.text

    again = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={"title": "Use JWT", "decision": "Issue signed JWTs"},
    )

    assert again.status_code == 409
    assert "already an accepted decision" in again.json()["detail"]
    governing = await _lane(client, repo["id"], "governing")
    assert [d["title"] for d in governing] == ["Use JWT"]
    assert governing[0]["affected_files"] == ["src/auth/service.py"]


@pytest.mark.asyncio
async def test_recording_a_title_again_with_a_scope_still_works(
    client: AsyncClient, app
) -> None:
    """Only the withdrawal is refused, not an ordinary re-record."""
    repo = await create_test_repo(client)
    body = {
        "title": "Use JWT",
        "decision": "Issue signed JWTs",
        "affected_files": ["src/auth/service.py"],
    }
    await client.post(f"/api/repos/{repo['id']}/decisions", json=body)

    again = await client.post(
        f"/api/repos/{repo['id']}/decisions",
        json={**body, "affected_files": ["src/auth/service.py", "src/auth/mw.py"]},
    )

    assert again.status_code == 201, again.text
    assert again.json()["affected_files"] == [
        "src/auth/service.py",
        "src/auth/mw.py",
    ]


@pytest.mark.asyncio
async def test_a_scopeless_re_record_of_a_candidate_is_fine(
    client: AsyncClient, app
) -> None:
    """Nothing is withdrawn, because nothing was accepted."""
    repo = await create_test_repo(client)
    body = {"title": "Maybe JWT", "decision": "Consider signed JWTs"}
    await client.post(f"/api/repos/{repo['id']}/decisions", json=body)

    again = await client.post(f"/api/repos/{repo['id']}/decisions", json=body)

    assert again.status_code == 201, again.text
    assert again.json()["status"] == "proposed"


# ---------------------------------------------------------------------------
# The health rollup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_counts_the_acceptance_not_the_status_column(
    client: AsyncClient, app
) -> None:
    """The fifth governance reader, and the one a migrated store meets first.

    Between the acceptance tables appearing and the classifier running, the
    status column still says ``active`` for records nobody accepted. Counting
    it made ``repowise decision health`` and ``get_why()`` report a hundred
    governing decisions on a store whose acceptance log was empty, while every
    other surface reported none.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="Never accepted", status="active")
    await _seed(sf, repo["id"], title="A real rule", accept=True)

    res = await client.get(f"/api/repos/{repo['id']}/decisions/health")

    assert res.status_code == 200, res.text
    summary = res.json()["summary"]
    assert summary["active"] == 1
    assert summary["proposed"] == 1
    titles = [d["title"] for d in res.json()["proposed_awaiting_review"]]
    assert "Never accepted" in titles


@pytest.mark.asyncio
async def test_a_candidate_does_not_make_a_hotspot_governed(
    client: AsyncClient, app
) -> None:
    """``ungoverned_hotspots`` exists to say where nobody has decided anything.

    A candidate naming a hotspot used to remove it from that list, which is
    the one answer the list must not give.
    """
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    async with get_session(sf) as session:
        from repowise.core.persistence.models import GitMetadata

        session.add(
            GitMetadata(
                repository_id=repo["id"],
                file_path="src/app.py",
                is_hotspot=True,
                temporal_hotspot_score=0.9,
            )
        )
        await session.flush()
    await _seed(sf, repo["id"], title="Names the hotspot", status="active")

    res = await client.get(f"/api/repos/{repo['id']}/decisions/health")

    assert res.status_code == 200, res.text
    assert "src/app.py" in res.json()["ungoverned_hotspots"]

    # Accepting it is what makes the file governed, and only then.
    async with get_session(sf) as session:
        rec = await crud.find_decision_by_title(
            session,
            repo["id"],
            "Names the hotspot",
            source="inline_marker",
            evidence_file="src/app.py",
        )
        await crud.accept_decision(session, rec, accepter="tester", evidence=["x"])
        await session.flush()

    after = await client.get(f"/api/repos/{repo['id']}/decisions/health")
    assert "src/app.py" not in after.json()["ungoverned_hotspots"]


@pytest.mark.asyncio
async def test_health_and_lane_counts_agree(client: AsyncClient, app) -> None:
    """Two rollups, one question. They disagreed before, and loudly."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="Rule", accept=True)
    await _seed(sf, repo["id"], title="Drifted", accept=True, staleness=0.9)
    await _seed(sf, repo["id"], title="Candidate", status="active")

    health = (await client.get(f"/api/repos/{repo['id']}/decisions/health")).json()
    lanes = (
        await client.get(f"/api/repos/{repo['id']}/decisions/lane-counts")
    ).json()

    assert health["summary"]["active"] == lanes["governing"]
    assert health["summary"]["proposed"] == lanes["candidates"]
    assert health["summary"]["stale"] == lanes["needs_review"]
    assert health["summary"]["unscoped"] == lanes["uncheckable"]
