"""The idle-decay refresh never mints fragment rows, and heals stores built before
non-code files had a history tier."""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier
from repowise.core.persistence.crud import upsert_git_metadata_bulk
from repowise.core.persistence.models import GitMetadata
from repowise.core.pipeline.persist import persist_git_refresh
from tests.unit.ingestion.test_git_history_tier import _build
from tests.unit.persistence.helpers import insert_repo


async def _rows(session, repo_id: str) -> dict[str, GitMetadata]:
    result = await session.execute(select(GitMetadata).where(GitMetadata.repository_id == repo_id))
    return {row.file_path: row for row in result.scalars().all()}


async def test_decay_rows_refresh_stored_rows_and_never_insert(async_session) -> None:
    repo = await insert_repo(async_session)
    await upsert_git_metadata_bulk(
        async_session,
        repo.id,
        [{"file_path": "a.py", "commit_count_total": 5, "primary_owner_name": "Alice"}],
    )
    decay = {
        "a.py": {"file_path": "a.py", "commit_count_90d": 3},
        # No stored row: inserting this partial is the fragment bug.
        "b.py": {"file_path": "b.py", "commit_count_90d": 2},
    }

    await persist_git_refresh(async_session, repo.id, {}, decay, None)

    rows = await _rows(async_session, repo.id)
    assert set(rows) == {"a.py"}
    assert rows["a.py"].commit_count_90d == 3
    assert rows["a.py"].commit_count_total == 5
    assert rows["a.py"].primary_owner_name == "Alice"


async def test_update_gives_a_pre_tier_store_the_native_history_rows(
    async_session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")
    _build(tmp_path)
    _summary, native = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    native_history = {row["file_path"]: row for row in native if row["history_only"]}
    assert native_history

    # A store indexed before the history tier: code rows only.
    repo = await insert_repo(async_session)
    await upsert_git_metadata_bulk(
        async_session, repo.id, [row for row in native if not row["history_only"]]
    )

    sink: dict[str, dict] = {}
    changed = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_changed_files(
        ["a.py"],
        # The parsed-file set the update passes holds no markup or config.
        all_files={"a.py", "b.py"},
        co_change_sink={},
        idle_decay_sink=sink,
    )
    await persist_git_refresh(
        async_session, repo.id, {m["file_path"]: m for m in changed}, sink, None
    )

    rows = await _rows(async_session, repo.id)
    assert set(rows) == {row["file_path"] for row in native}
    for path, expected in native_history.items():
        row = rows[path]
        assert row.history_only is True
        assert row.churn_percentile == 0.0 and row.is_hotspot is False
        for key, value in expected.items():
            stored = getattr(row, key)
            if hasattr(value, "tzinfo"):
                value, stored = value.replace(tzinfo=None), stored.replace(tzinfo=None)
            assert stored == value, (path, key)
