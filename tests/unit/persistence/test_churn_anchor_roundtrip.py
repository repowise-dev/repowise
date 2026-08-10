"""The churn anchor survives the round trip through the ``repositories`` row.

The rest of the fold is covered against real git repositories in
``tests/unit/ingestion/test_repo_totals_churn.py``. Those tests call
``capture_repo_totals`` directly, so every one of them still passes if the
anchor never reaches storage or never comes back — and the whole point of the
change is a git walk that stops happening on the update path. This is the wiring
in between: ``update_repo_git_totals`` writes it, ``_churn_prior`` reads it back
into the shape the capture expects.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import get_repository, update_repo_git_totals
from repowise.core.pipeline.incremental import _churn_prior
from tests.unit.persistence.helpers import insert_repo

_SHA = "a" * 40


@pytest.mark.asyncio
async def test_totals_round_trip_into_a_usable_prior(async_session) -> None:
    repo = await insert_repo(async_session)

    await update_repo_git_totals(
        async_session,
        repo.id,
        total_commit_count=2218,
        total_lines_added=651587,
        total_lines_deleted=273403,
        churn_anchor_sha=_SHA,
    )

    prior = _churn_prior(await get_repository(async_session, repo.id))
    assert prior is not None
    assert prior.churn_anchor_sha == _SHA
    assert prior.total_commit_count == 2218
    assert (prior.total_lines_added, prior.total_lines_deleted) == (651587, 273403)


@pytest.mark.asyncio
async def test_a_repo_with_no_anchor_yet_has_no_prior(async_session) -> None:
    """Every index written before the column existed takes this path: it walks
    the whole history once and anchors itself."""
    repo = await insert_repo(async_session)
    await update_repo_git_totals(
        async_session, repo.id, total_commit_count=10, total_lines_added=5
    )

    assert _churn_prior(await get_repository(async_session, repo.id)) is None
    assert _churn_prior(None) is None


@pytest.mark.asyncio
async def test_a_capture_that_skipped_churn_leaves_the_stored_pair_alone(
    async_session,
) -> None:
    """The write rule the fold's safety rests on.

    A capture above the commit ceiling, or one whose git call failed, returns
    ``None`` for churn *and* for the anchor. Neither may be blanked, and — the
    part that matters — the anchor must not be able to move on its own.
    """
    repo = await insert_repo(async_session)
    await update_repo_git_totals(
        async_session,
        repo.id,
        total_commit_count=100,
        total_lines_added=900,
        total_lines_deleted=100,
        churn_anchor_sha=_SHA,
    )

    # The shape a skipped churn walk produces: a fresh count, no churn, no anchor.
    await update_repo_git_totals(async_session, repo.id, total_commit_count=140)

    row = await get_repository(async_session, repo.id)
    assert row.total_commit_count == 140
    assert (row.total_lines_added, row.total_lines_deleted) == (900, 100)
    assert row.churn_anchor_sha == _SHA
