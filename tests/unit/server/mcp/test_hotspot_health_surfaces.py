"""The MCP surfaces report the owner's hotspot health (P10).

``_build_code_health`` (``get_overview``) had no test at all: nothing called it
with health metrics seeded, so it short-circuited on the empty guard in every
run. It shipped a top-25%-by-NLOC average under a comment claiming it matched
the dashboard, and swapping that for the real KPI broke nothing.

``get_health`` did not return the number at all, so the canonical persisted KPI
was surfaced by neither tool while the two disagreed with each other.

The seeded repo (see ``conftest.health_data``) is two files:
    src/auth/service.py  score 4.5  nloc 200
    src/db/models.py     score 8.5  nloc 50
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from repowise.core.persistence.models import GitMetadata


async def _set_hotspots(session, repo_id: str, paths: set[str]) -> None:
    """Make *paths* exactly the repo's hotspot set.

    Every flag is cleared first. The fixture already flags ``service.py``, so a
    test that only adds a flag inherits a set it did not choose — which is how
    the first cut of these tests asserted the wrong numbers against correct
    code.
    """
    await session.execute(
        update(GitMetadata)
        .where(GitMetadata.repository_id == repo_id)
        .values(is_hotspot=False)
    )
    if paths:
        await session.execute(
            update(GitMetadata)
            .where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path.in_(paths),
            )
            .values(is_hotspot=True)
        )
    await session.flush()


@pytest.mark.asyncio
async def test_get_overview_code_health_uses_the_hotspot_files(
    session, setup_mcp, health_data
):
    """The KPI averages the flagged file, not the biggest one.

    This is the whole disagreement in one assertion. The retired definition
    took the top 25% by NLOC, which here is ``service.py`` (200 NLOC) and would
    coincidentally also give 4.5 — so the flagged file is the *small* one, to
    make the two answers differ: owner 8.5, retired 4.5.
    """
    from repowise.core.persistence import crud
    from repowise.server.mcp_server.tool_overview import _build_code_health

    repo_id = health_data
    await _set_hotspots(session, repo_id, {"src/db/models.py"})
    repository = await crud.get_repository(session, repo_id)

    block = await _build_code_health(session, repository)

    assert block["hotspot_health"] == 8.5
    # The average is unchanged: (4.5*200 + 8.5*50) / 250 == 5.3.
    assert block["average_health"] == 5.3


@pytest.mark.asyncio
async def test_get_overview_hotspot_health_is_none_without_hotspots(
    session, setup_mcp, health_data
):
    """No flagged file means no hotspot health, not a perfect 10.0."""
    from repowise.core.persistence import crud
    from repowise.server.mcp_server.tool_overview import _build_code_health

    await _set_hotspots(session, health_data, set())
    repository = await crud.get_repository(session, health_data)
    block = await _build_code_health(session, repository)

    assert block["hotspot_health"] is None


@pytest.mark.asyncio
async def test_get_health_returns_hotspot_health(session, setup_mcp, health_data):
    """The KPI ``get_health`` never returned (D2)."""
    from repowise.server.mcp_server import get_health

    await _set_hotspots(session, health_data, {"src/auth/service.py"})

    result = await get_health()

    assert "hotspot_health" in result["kpis"]
    assert result["kpis"]["hotspot_health"] == 4.5


@pytest.mark.asyncio
async def test_get_health_and_get_overview_agree(session, setup_mcp, health_data):
    """The two tools that disagreed on 42 of 42 indexes now cannot.

    Asserting agreement rather than two literals is deliberate: a future change
    to the definition should move both or fail here, which is exactly what did
    not happen when one of them drifted.

    The hotspot is ``models.py``, the *small* file, on purpose. Flagging the
    big one makes the retired quartile definition agree with the owner by
    coincidence, and a version of this test that did so passed against the very
    code it was meant to outlaw.
    """
    from repowise.core.persistence import crud
    from repowise.server.mcp_server import get_health
    from repowise.server.mcp_server.tool_overview import _build_code_health

    await _set_hotspots(session, health_data, {"src/db/models.py"})
    repository = await crud.get_repository(session, health_data)

    overview_block = await _build_code_health(session, repository)
    health_result = await get_health()

    assert overview_block["hotspot_health"] == health_result["kpis"]["hotspot_health"]
    assert overview_block["hotspot_health"] == 8.5
