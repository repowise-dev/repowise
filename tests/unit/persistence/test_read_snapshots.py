"""Read snapshots: the Fix first queue and the actions view stored at index time.

Written once the stores are final, served while their key holds, ignored once
any store they read moves, equal to a live build, and never written on read.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from repowise.core.persistence.crud.analysis import actions as actions_loader
from repowise.core.persistence.crud.analysis import fix_first as fix_first_loader
from repowise.core.persistence.crud.analysis.actions import (
    load_actions_view,
    set_action_state,
    write_read_snapshots,
)
from repowise.core.persistence.crud.analysis.fix_first import (
    clear_fix_first_cache,
    load_fix_first,
)
from repowise.core.persistence.models import HealthFinding, ReadSnapshot, SecurityFinding
from tests.unit.persistence.test_actions_loader import NOW, _seed


def _wire(value) -> str:
    return json.dumps(value, sort_keys=False, default=str)


async def _live(session, rid: str) -> tuple[str, str, dict]:
    clear_fix_first_cache()
    view = await load_actions_view(session, rid, now=NOW)
    queue = await load_fix_first(session, rid, limit=None)
    return _wire(view), _wire(queue.as_dict()), dict(queue.refactoring_reasons)


async def _snapshot_rows(session) -> int:
    return (await session.execute(select(func.count()).select_from(ReadSnapshot))).scalar_one()


@pytest.fixture
def no_live_build(monkeypatch):
    """Fail any live build, so a read that succeeds came from the snapshot."""

    async def refuse(*_args, **_kwargs):
        raise AssertionError("built live")

    def arm() -> None:
        clear_fix_first_cache()
        monkeypatch.setattr(fix_first_loader, "_build", refuse)
        monkeypatch.setattr(actions_loader, "_ruled", refuse)

    return arm


async def test_persist_writes_both_snapshots_once(async_session) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    kinds = (await async_session.execute(select(ReadSnapshot.kind))).scalars().all()
    assert sorted(kinds) == ["actions", "fix_first"]
    # Nothing moved since, so a second pass writes nothing.
    assert not await fix_first_loader.write_fix_first_snapshot(async_session, rid)
    assert not await actions_loader.write_actions_snapshot(async_session, rid)


async def test_a_fresh_snapshot_is_served_and_equals_a_live_build(
    async_session, no_live_build
) -> None:
    rid = await _seed(async_session)
    view, queue, reasons = await _live(async_session, rid)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()

    no_live_build()
    served = await load_fix_first(async_session, rid, limit=None)
    assert _wire(served.as_dict()) == queue
    assert served.refactoring_reasons == reasons
    assert _wire(await load_actions_view(async_session, rid, now=NOW)) == view
    # Every limit and id is a slice of the stored queue.
    lead = served.items[0]
    assert (await load_fix_first(async_session, rid, limit=1)).items == (lead,)
    assert (await load_fix_first(async_session, rid, item_id=lead.id)).find(lead.id) == lead


async def test_a_persons_answer_applies_to_the_stored_view(async_session, no_live_build) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    first = (await load_actions_view(async_session, rid, now=NOW))["horizons"]["quarter"]
    action = first["actions"][0]
    await set_action_state(
        async_session, rid, action["id"], state="dismissed", fingerprint=action["fingerprint"]
    )
    await async_session.commit()

    no_live_build()
    after = (await load_actions_view(async_session, rid, now=NOW))["horizons"]["quarter"]
    assert action["id"] not in {a["id"] for a in after["actions"]}
    assert after["hidden"] == first["hidden"] + 1


@pytest.mark.parametrize("change", ["triage", "new_store_row"])
async def test_a_stale_snapshot_is_ignored(async_session, change) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    before = await _live(async_session, rid)
    if change == "triage":
        finding = (
            await async_session.execute(
                select(HealthFinding).where(HealthFinding.function_name == "run")
            )
        ).scalar_one()
        finding.status = "dismissed"
    else:
        async_session.add(
            SecurityFinding(repository_id=rid, file_path="src/cfg.py", kind="hardcoded_secret",
                            line_number=3, snippet='KEY = "sk_live_9"', severity="high", commit_sha="")
        )
    await async_session.commit()

    view, queue, _ = await _live(async_session, rid)
    # The stored views now describe stores that moved: serving them would be wrong.
    assert (view, queue)[change == "triage"] != before[change == "triage"]
    clear_fix_first_cache()
    assert _wire((await load_fix_first(async_session, rid, limit=None)).as_dict()) == queue
    assert _wire(await load_actions_view(async_session, rid, now=NOW)) == view


async def test_a_read_never_writes_a_snapshot(async_session) -> None:
    rid = await _seed(async_session)
    await async_session.commit()
    await _live(async_session, rid)
    assert await _snapshot_rows(async_session) == 0
    assert not async_session.new and not async_session.dirty


async def test_a_store_without_the_table_builds_live(async_session) -> None:
    from sqlalchemy import text

    rid = await _seed(async_session)
    await async_session.execute(text("DROP TABLE read_snapshots"))
    await async_session.commit()
    view = await load_actions_view(async_session, rid, now=NOW)
    assert view["unavailable"] == {}
    assert (await load_fix_first(async_session, rid, limit=None)).items
