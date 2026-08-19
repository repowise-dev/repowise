"""Round-trip tests for the fix-event rollup pass (``pipeline/fix_rollups.py``).

Covers the parts the pure-function tests can't: that attribution is written back
onto the event rows, that only production-code fixes reach the magnet count,
that decay is anchored to the repo rather than wall-clock time, and that
re-running the pass on unchanged data is a no-op.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import FixEvent, GitMetadata, WikiSymbol
from repowise.core.pipeline.fix_rollups import apply_fix_rollups
from tests.unit.persistence.helpers import insert_repo

HEAD = datetime(2026, 7, 1, tzinfo=UTC)
PATH = "src/store.py"


async def _seed(session, repo_id: str, *, events: list[dict], last_commit_at=HEAD) -> None:
    session.add(
        GitMetadata(
            repository_id=repo_id,
            file_path=PATH,
            last_commit_at=last_commit_at,
            prior_defect_count=len(events),
        )
    )
    for symbol_id, start, end in (
        (f"{PATH}::Store", 1, 60),
        (f"{PATH}::Store.save", 10, 25),
        (f"{PATH}::Store.load", 30, 45),
    ):
        session.add(
            WikiSymbol(
                repository_id=repo_id,
                file_path=PATH,
                symbol_id=symbol_id,
                name=symbol_id.split("::")[-1],
                qualified_name=symbol_id,
                kind="function",
                start_line=start,
                end_line=end,
            )
        )
    for i, ev in enumerate(events):
        session.add(
            FixEvent(
                repository_id=repo_id,
                fix_sha=ev.get("sha", f"{i:040d}"),
                file_path=PATH,
                shape_kind=ev.get("shape_kind", "code_fix"),
                old_ranges_json=json.dumps(ev.get("ranges", [[12, 14]])),
                committed_at=ev["at"],
            )
        )
    await session.flush()


async def _meta(session, repo_id: str) -> GitMetadata:
    return (
        await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repo_id, GitMetadata.file_path == PATH
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_attribution_and_rollup_written_for_a_magnet_file(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        events=[
            {"at": HEAD, "ranges": [[12, 14]]},
            {"at": HEAD - timedelta(days=3), "ranges": [[12, 20]]},
            {"at": HEAD - timedelta(days=10), "ranges": [[31, 33]]},
            {"at": HEAD - timedelta(days=25), "ranges": [[12, 13]]},
        ],
    )

    written = await apply_fix_rollups(async_session, repo.id)
    assert written == 1

    meta = await _meta(async_session, repo.id)
    assert meta.bug_magnet is True
    assert meta.fix_mass > 3.0
    assert meta.last_fix_at.replace(tzinfo=UTC) == HEAD
    assert json.loads(meta.fix_symbol_counts_json) == {
        f"{PATH}::Store.save": 3,
        f"{PATH}::Store.load": 1,
    }

    rows = (
        (await async_session.execute(select(FixEvent).where(FixEvent.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert {json.loads(r.symbol_ids_json)[0] for r in rows} == {
        f"{PATH}::Store.save",
        f"{PATH}::Store.load",
    }
    # The newest fix is the file's last commit, so nothing moved under it.
    newest = max(rows, key=lambda r: r.committed_at)
    assert newest.attribution == "exact"
    assert all(r.attribution in ("exact", "approximate") for r in rows)


@pytest.mark.asyncio
async def test_only_production_code_fixes_reach_the_magnet_count(async_session) -> None:
    """The magnet flag uses the same filter #931 put on ``prior_defect_count``."""
    repo = await insert_repo(async_session)
    await _seed(
        async_session,
        repo.id,
        events=[
            {"at": HEAD, "shape_kind": "code_fix"},
            {"at": HEAD, "shape_kind": "test_only"},
            {"at": HEAD, "shape_kind": "doc_only"},
            {"at": HEAD, "shape_kind": "config_other"},
        ],
    )
    await apply_fix_rollups(async_session, repo.id)

    meta = await _meta(async_session, repo.id)
    assert meta.fix_mass == 1.0
    assert meta.bug_magnet is False
    assert json.loads(meta.fix_symbol_counts_json) == {f"{PATH}::Store.save": 1}

    # Non-code rows are still attributed — the ranges are true either way, they
    # simply do not count as defects.
    rows = (
        (await async_session.execute(select(FixEvent).where(FixEvent.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert all(json.loads(r.symbol_ids_json) for r in rows)


@pytest.mark.asyncio
async def test_pure_insertions_attribute_to_nothing(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id, events=[{"at": HEAD, "ranges": []}])
    await apply_fix_rollups(async_session, repo.id)

    row = (
        await async_session.execute(select(FixEvent).where(FixEvent.repository_id == repo.id))
    ).scalar_one()
    assert row.attribution == "none"
    assert json.loads(row.symbol_ids_json) == []
    # It is still a fix: the file-level mass counts it, only the symbol map cannot.
    meta = await _meta(async_session, repo.id)
    assert meta.fix_mass == 1.0
    assert json.loads(meta.fix_symbol_counts_json) == {}


@pytest.mark.asyncio
async def test_decay_is_anchored_to_the_repo_not_to_wall_clock(async_session) -> None:
    """A dormant repo's fixes are fresh relative to its own newest commit.

    Anchoring to wall-clock time would make every rollup drift between two
    indexes of the same checkout, which is exactly what the update-parity check
    forbids.
    """
    repo = await insert_repo(async_session)
    old = datetime(2024, 1, 1, tzinfo=UTC)
    await _seed(
        async_session,
        repo.id,
        events=[{"at": old, "sha": f"a{i:039d}"} for i in range(3)],
        last_commit_at=old,
    )
    await apply_fix_rollups(async_session, repo.id)

    meta = await _meta(async_session, repo.id)
    assert meta.fix_mass == 3.0
    assert meta.bug_magnet is True


@pytest.mark.asyncio
async def test_rerunning_the_pass_is_idempotent(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id, events=[{"at": HEAD}, {"at": HEAD - timedelta(days=20)}])

    def _snapshot(m: GitMetadata) -> tuple:
        # SQLite hands datetimes back naive, so compare the instant, not the
        # tzinfo the value happened to be carrying when it was written.
        return (
            m.fix_mass,
            m.bug_magnet,
            m.fix_symbol_counts_json,
            m.last_fix_at.replace(tzinfo=None),
        )

    await apply_fix_rollups(async_session, repo.id)
    first = _snapshot(await _meta(async_session, repo.id))

    await apply_fix_rollups(async_session, repo.id)
    assert _snapshot(await _meta(async_session, repo.id)) == first


@pytest.mark.asyncio
async def test_no_fix_events_is_a_no_op(async_session) -> None:
    repo = await insert_repo(async_session)
    assert await apply_fix_rollups(async_session, repo.id) == 0
