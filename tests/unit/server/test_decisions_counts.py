"""Tests for the decisions counts aggregate and priority ordering.

Both exist because the decisions page reported "97 of 100" on a repository
holding several hundred records: it counted the page it had fetched, and the
list endpoint caps at 500. Counting rows is not measuring a total.
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
    status: str,
    confidence: float = 0.5,
    source: str = "inline_marker",
) -> str:
    async with get_session(session_factory) as session:
        rec = await crud.upsert_decision(
            session,
            repository_id=repo_id,
            title=title,
            status=status,
            context="ctx",
            decision="dec",
            rationale="why",
            source=source,
            affected_files=["src/app.py"],
            evidence_file="src/app.py",
            confidence=confidence,
        )
        # ``active`` is a projection of an acceptance, so a seed that wants a
        # governing decision has to perform one; a bare upsert lands a
        # candidate however the status argument reads.
        if status == "active":
            await crud.accept_decision(session, rec, accepter="tester")
        return rec.id


@pytest.mark.asyncio
async def test_counts_are_measured_not_paged(client: AsyncClient, app) -> None:
    """The total comes from a COUNT, so it is not bounded by the page size."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory

    for i in range(7):
        await _seed(sf, repo["id"], title=f"Proposal {i}", status="proposed")
    for i in range(2):
        await _seed(sf, repo["id"], title=f"Rule {i}", status="active")
    await _seed(sf, repo["id"], title="Old", status="superseded")

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/counts")
    assert resp.status_code == 200
    body = resp.json()

    assert body == {
        "total": 10,
        "active": 2,
        "proposed": 7,
        "superseded": 1,
        "deprecated": 0,
    }

    # And the total exceeds what a single small page would have reported.
    page = await client.get(
        f"/api/repos/{repo['id']}/decisions", params={"limit": 3}
    )
    assert len(page.json()) == 3
    assert body["total"] == 10


@pytest.mark.asyncio
async def test_counts_zero_fill_absent_statuses(client: AsyncClient, app) -> None:
    """The shape is stable even when a status has no rows."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"], title="Only one", status="active")

    body = (await client.get(f"/api/repos/{repo['id']}/decisions/counts")).json()
    assert body == {
        "total": 1,
        "active": 1,
        "proposed": 0,
        "superseded": 0,
        "deprecated": 0,
    }


@pytest.mark.asyncio
async def test_counts_respect_include_proposed(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="A rule", status="active")
    await _seed(sf, repo["id"], title="A guess", status="proposed")

    body = (
        await client.get(
            f"/api/repos/{repo['id']}/decisions/counts",
            params={"include_proposed": "false"},
        )
    ).json()
    assert body["total"] == 1
    assert body["proposed"] == 0


@pytest.mark.asyncio
async def test_counts_route_is_not_shadowed_by_decision_id(
    client: AsyncClient,
) -> None:
    """`/counts` must be declared above `/{decision_id}`.

    FastAPI matches in declaration order, so below it the literal path is
    looked up as a decision id and 404s.
    """
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/decisions/counts")
    assert resp.status_code == 200
    assert "total" in resp.json()


@pytest.mark.asyncio
async def test_priority_sort_leads_with_confirmed_rules(
    client: AsyncClient, app
) -> None:
    """Newest-first buried every active decision under fresh proposals."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory

    # Seeded oldest-first, so `recent` would invert this order entirely.
    await _seed(sf, repo["id"], title="Confirmed rule", status="active", confidence=0.4)
    await _seed(sf, repo["id"], title="Weak guess", status="proposed", confidence=0.2)
    await _seed(sf, repo["id"], title="Strong guess", status="proposed", confidence=0.95)
    await _seed(sf, repo["id"], title="Retired", status="superseded", confidence=0.9)

    rows = (
        await client.get(f"/api/repos/{repo['id']}/decisions")
    ).json()
    assert [r["title"] for r in rows] == [
        "Confirmed rule",
        "Strong guess",
        "Weak guess",
        "Retired",
    ]


@pytest.mark.asyncio
async def test_recent_sort_is_still_available(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    await _seed(sf, repo["id"], title="First", status="active")
    await _seed(sf, repo["id"], title="Second", status="proposed")

    rows = (
        await client.get(
            f"/api/repos/{repo['id']}/decisions", params={"sort": "recent"}
        )
    ).json()
    assert [r["title"] for r in rows] == ["Second", "First"]


@pytest.mark.asyncio
async def test_unknown_sort_is_rejected(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(
        f"/api/repos/{repo['id']}/decisions", params={"sort": "sideways"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_offset_pages_without_repeating_rows(
    client: AsyncClient, app
) -> None:
    """Server-side paging: the table asks for a window, not the whole set."""
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    for i in range(5):
        await _seed(sf, repo["id"], title=f"D{i}", status="proposed", confidence=1 - i / 10)

    first = (
        await client.get(
            f"/api/repos/{repo['id']}/decisions", params={"limit": 2, "offset": 0}
        )
    ).json()
    second = (
        await client.get(
            f"/api/repos/{repo['id']}/decisions", params={"limit": 2, "offset": 2}
        )
    ).json()

    assert len(first) == 2
    assert len(second) == 2
    assert {r["id"] for r in first}.isdisjoint({r["id"] for r in second})
