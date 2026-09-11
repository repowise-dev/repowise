"""``update_repo_git_totals`` keeps the shallow flag three-valued.

The capture side is covered against real clones in
``tests/unit/ingestion/test_git_commit_rows_integration.py``. This is the wiring
in between, and specifically the one way it could quietly go wrong: the writer
applies a field only when it is not ``None``, so a falsy check anywhere on that
path would drop ``False`` and leave "this clone is complete" indistinguishable
from never having looked.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.crud import get_repository, update_repo_git_totals
from tests.unit.persistence.helpers import insert_repo


@pytest.mark.asyncio
async def test_the_shallow_flag_round_trips_and_stays_three_valued(async_session) -> None:
    repo = await insert_repo(async_session)
    assert (await get_repository(async_session, repo.id)).is_shallow_clone is None

    await update_repo_git_totals(async_session, repo.id, is_shallow_clone=False)
    assert (await get_repository(async_session, repo.id)).is_shallow_clone is False

    await update_repo_git_totals(async_session, repo.id, is_shallow_clone=True)
    assert (await get_repository(async_session, repo.id)).is_shallow_clone is True

    # A later capture that could not run the check must not blank the answer.
    await update_repo_git_totals(async_session, repo.id, total_commit_count=7)
    assert (await get_repository(async_session, repo.id)).is_shallow_clone is True
