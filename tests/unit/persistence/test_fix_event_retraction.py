"""An update drops the stored fix events of commits the fix rule no longer names."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import FixEvent
from repowise.core.pipeline.incremental.commits import persist_incremental_fix_events
from tests.unit.persistence.helpers import insert_repo

_AT = datetime(2026, 7, 1, tzinfo=UTC)
_PATH = "src/store.py"
_REFACTOR, _FIX = "a" * 40, "b" * 40


class _Indexer:
    def capture_new_fix_events(self, *, known_shas=None):
        return [], int(_AT.timestamp()) - 1, {_PATH}, {_REFACTOR} & set(known_shas or ())


@pytest.mark.asyncio
async def test_a_retracted_fix_commit_leaves_the_table(async_session) -> None:
    repo = await insert_repo(async_session)
    for sha in (_REFACTOR, _FIX):
        async_session.add(
            FixEvent(
                repository_id=repo.id, fix_sha=sha, file_path=_PATH,
                shape_kind="code_fix", old_ranges_json="[]", committed_at=_AT,
            )
        )
    await async_session.flush()

    await persist_incremental_fix_events(async_session, repo.id, _Indexer())

    stored = (await async_session.execute(select(FixEvent.fix_sha))).scalars().all()
    assert stored == [_FIX]
