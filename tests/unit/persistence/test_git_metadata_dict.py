"""Stored git rows come back in the indexer's shape, timestamps aware UTC.

SQLite drops the offset from ``DateTime(timezone=True)``. An update merges
stored rows with freshly indexed ones (aware), and the repository overview
then picks the oldest file by ``first_commit_at``; a naive stored value made
that comparison raise.
"""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.analysis.health.history_refresh import git_meta_rows_to_map
from repowise.core.persistence.crud import get_all_git_metadata
from repowise.core.persistence.models import GitMetadata
from repowise.core.pipeline.resume.rehydrate import stored_git_meta_for
from tests.unit.persistence.helpers import insert_repo

FIRST = datetime(2024, 3, 1, 8, 30, tzinfo=UTC)
LAST = datetime(2025, 12, 21, 0, 6, 49, tzinfo=UTC)


async def _stored_row(session) -> str:
    repo = await insert_repo(session)
    session.add(
        GitMetadata(
            repository_id=repo.id, file_path="old.py", first_commit_at=FIRST, last_commit_at=LAST
        )
    )
    await session.commit()
    session.expunge_all()  # read the row back from the store, not the identity map
    return repo.id


async def test_stored_rows_merge_with_fresh_ones_for_the_overview(async_session) -> None:
    repo_id = await _stored_row(async_session)
    stored = await stored_git_meta_for(async_session, repo_id, ["old.py"])
    fresh = {"new.py": {"file_path": "new.py", "first_commit_at": datetime.now(UTC)}}
    metas = {**stored, **fresh}.values()

    # The overview's oldest-file pick, as it runs on the merged map.
    oldest = min(
        (m for m in metas if m.get("first_commit_at")), key=lambda m: m["first_commit_at"]
    )
    assert oldest["file_path"] == "old.py"
    assert stored["old.py"]["first_commit_at"] == FIRST  # the instant is kept
    assert stored["old.py"]["last_commit_at"] == LAST
    assert "id" not in stored["old.py"] and "repository_id" not in stored["old.py"]


async def test_health_rescore_reads_the_same_shape(async_session) -> None:
    repo_id = await _stored_row(async_session)
    rows = await get_all_git_metadata(async_session, repo_id)
    meta = git_meta_rows_to_map(rows.values())["old.py"]
    assert meta["first_commit_at"].tzinfo is not None
    assert meta["last_commit_at"] == LAST
