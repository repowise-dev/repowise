"""Read snapshots: the Fix first queue and the actions view stored at index time.

Written once the stores are final, served while current, dropped by any write
to a store they read, equal to a live build, and never written on read.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update

from repowise.core.analysis.health.fix_first import FIX_KINDS, FixFirstQueue, build_fix_first
from repowise.core.persistence.crud.analysis import actions as actions_loader
from repowise.core.persistence.crud.analysis import fix_first as fix_first_loader
from repowise.core.persistence.crud.analysis.actions import (
    load_actions_view,
    set_action_state,
    write_read_snapshots,
)
from repowise.core.persistence.crud.analysis.coverage_map import save_test_coverage
from repowise.core.persistence.crud.analysis.fix_first import (
    clear_fix_first_cache,
    load_fix_first,
)
from repowise.core.persistence.models import (
    GraphEdge,
    HealthFinding,
    ReadSnapshot,
    SecurityFinding,
)
from repowise.core.persistence.read_snapshots import _written_table, decode_or_none
from tests.unit.health.fix_first_rows import FINDINGS, METRICS, PERFORMANCE, PLANS, REFACTORING
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


async def _triage(session, rid) -> None:
    finding = (
        await session.execute(select(HealthFinding).where(HealthFinding.function_name == "run"))
    ).scalar_one()
    finding.status = "dismissed"


async def _new_secret(session, rid) -> None:
    session.add(
        SecurityFinding(repository_id=rid, file_path="src/cfg.py", kind="hardcoded_secret",
                        line_number=3, snippet='KEY = "sk_live_9"', severity="high", commit_sha="")
    )


async def _coverage_ingest(session, rid) -> None:
    record = SimpleNamespace(test_id="tests/test_core.py::test_run", test_file="tests/test_core.py",
                             file_path="src/core.py", covered_lines=[12, 13])
    await save_test_coverage(session, rid, [record], source_format="coverage.py")


async def _graph_text_write(session, rid) -> None:
    await session.execute(
        text("UPDATE graph_nodes SET start_line = 7 WHERE repository_id = :r"), {"r": rid}
    )


async def _graph_bulk_write(session, rid) -> None:
    await session.execute(
        update(GraphEdge).where(GraphEdge.repository_id == rid).values(edge_type="calls")
    )


@pytest.mark.parametrize(
    "change", [_triage, _new_secret, _coverage_ingest, _graph_text_write, _graph_bulk_write]
)
async def test_a_write_to_an_input_drops_the_snapshots(async_session, change) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 2

    await change(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 0
    view, queue, _ = await _live(async_session, rid)
    assert _wire((await load_fix_first(async_session, rid, limit=None)).as_dict()) == queue
    assert _wire(await load_actions_view(async_session, rid, now=NOW)) == view


async def test_a_dropped_snapshot_would_have_been_wrong(async_session) -> None:
    rid = await _seed(async_session)
    before = await _live(async_session, rid)
    await _triage(async_session, rid)
    await _new_secret(async_session, rid)
    await async_session.commit()
    view, queue, _ = await _live(async_session, rid)
    assert view != before[0] and queue != before[1]


async def test_writes_elsewhere_keep_the_snapshots(async_session) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    await async_session.execute(
        text("UPDATE repositories SET head_commit = 'abc' WHERE id = :r"), {"r": rid}
    )
    async with async_session.begin_nested():
        await async_session.execute(select(HealthFinding.id))  # a read in a savepoint
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 2


async def test_writes_before_the_snapshot_in_one_session_keep_it(async_session) -> None:
    rid = await _seed(async_session)  # pending input rows in this session
    await _new_secret(async_session, rid)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 2


async def _stored_payload(session, kind: str) -> dict:
    row = await session.get(ReadSnapshot, (await _repo_id(session), kind))
    return json.loads(row.payload_json)


async def _repo_id(session) -> str:
    return (await session.execute(select(ReadSnapshot.repository_id))).scalars().first()


async def _replace_payload(session, kind: str, payload: dict) -> None:
    row = await session.get(ReadSnapshot, (await _repo_id(session), kind))
    row.payload_json = json.dumps(payload)
    await session.flush()


@pytest.mark.parametrize("kind", ["fix_first", "actions"])
async def test_a_payload_an_older_build_wrote_is_a_miss(async_session, kind) -> None:
    rid = await _seed(async_session)
    view, queue, _ = await _live(async_session, rid)
    await write_read_snapshots(async_session, rid)
    payload = await _stored_payload(async_session, kind)
    if kind == "fix_first":
        del payload["items"][0]["verify"]
    else:
        del payload["actions"][0][1]["tier"]
    await _replace_payload(async_session, kind, payload)

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
    rid = await _seed(async_session)
    await async_session.execute(text("DROP TABLE read_snapshots"))
    await async_session.commit()
    view = await load_actions_view(async_session, rid, now=NOW)
    assert view["unavailable"] == {}
    assert (await load_fix_first(async_session, rid, limit=None)).items


def test_decode_inverts_asdict_over_every_item_kind() -> None:
    profile = {"via": "call", "tests": ["tests/test_core.py"], "total": 3, "basis": "inferred",
               "commands": ["pytest tests/test_core.py"]}
    queue = build_fix_first(metrics=METRICS, findings=FINDINGS, refactoring=REFACTORING,
                            performance=PERFORMANCE, plans=PLANS, limit=None,
                            validate=lambda *_: profile)
    assert {i.kind for i in queue.items} == set(FIX_KINDS)
    assert any(i.verify.tests for i in queue.items) and queue.refactoring_reasons
    stored = json.loads(json.dumps(asdict(queue)))
    assert decode_or_none(FixFirstQueue, stored) == queue


@dataclass(frozen=True)
class Leaf:
    n: int


@dataclass(frozen=True)
class Node:
    pair: tuple[str, Leaf]
    either: Leaf | str | None
    many: tuple[Leaf, ...] = ()


def test_decode_fixed_tuples_and_unions() -> None:
    raw = {"pair": ["a", {"n": 1}], "either": {"n": 2}, "many": [{"n": 3}]}
    assert decode_or_none(Node, raw) == Node(("a", Leaf(1)), Leaf(2), (Leaf(3),))
    assert decode_or_none(Node, {**raw, "either": "text"}).either == "text"
    assert decode_or_none(Node, {**raw, "pair": ["a"]}) is None
    assert decode_or_none(Node, {"either": None}) is None


async def test_a_rewrite_in_the_writing_session_replaces_a_current_row(async_session) -> None:
    """Re-index: the stores and the views written in one session, over a row
    whose key still matches. The views must be rebuilt, not kept."""
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()

    await _triage(async_session, rid)  # an input write, not yet committed
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 2
    view, queue, _ = await _live(async_session, rid)
    clear_fix_first_cache()
    stored = await load_fix_first(async_session, rid, limit=None)
    assert _wire(stored.as_dict()) == queue
    assert "src/core.py" not in {i.target.file_path for i in stored.items}
    assert _wire(await load_actions_view(async_session, rid, now=NOW)) == view


async def test_a_failed_view_write_leaves_nothing_stale(async_session, monkeypatch) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()

    async def broken(*_args):
        raise RuntimeError("build failed")

    monkeypatch.setattr(actions_loader, "write_actions_snapshot", broken)
    await _triage(async_session, rid)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 0


async def test_a_rolled_back_savepoint_still_invalidates(async_session) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    savepoint = await async_session.begin_nested()
    await _graph_text_write(async_session, rid)
    await savepoint.rollback()
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 0


async def test_one_sessions_write_does_not_mark_another(async_session, session_factory) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    async with session_factory() as other:
        await _graph_text_write(other, rid)  # flagged, never committed
        async with session_factory() as reader:
            await reader.execute(select(HealthFinding.id))
            await reader.commit()
        await other.rollback()
    assert await _snapshot_rows(async_session) == 2


async def test_a_corrupt_payload_is_a_miss(async_session) -> None:
    rid = await _seed(async_session)
    view, queue, _ = await _live(async_session, rid)
    await write_read_snapshots(async_session, rid)
    for kind in ("fix_first", "actions"):
        row = await async_session.get(ReadSnapshot, (rid, kind))
        row.payload_json = "{not json"
    await async_session.flush()
    clear_fix_first_cache()
    assert _wire((await load_fix_first(async_session, rid, limit=None)).as_dict()) == queue
    assert _wire(await load_actions_view(async_session, rid, now=NOW)) == view


@pytest.mark.parametrize(
    ("sql", "table"),
    [
        ("SELECT 1", None),
        ("  -- note\n select 2", None),
        ("/* x */ PRAGMA table_info(t)", None),
        ("EXPLAIN SELECT 1", None),
        ("WITH r AS (SELECT 1) UPDATE git_metadata SET a = 1", ""),
        ("UPDATE main.graph_nodes SET x = 1", "graph_nodes"),
        ('DELETE FROM "wiki_pages"', "wiki_pages"),
        ("-- note\nUPDATE wiki_pages SET x = 1", "wiki_pages"),
        ("DROP TABLE x", ""),
        ("VACUUM", ""),
    ],
)
def test_text_sql_counts_as_a_write_unless_it_is_a_plain_read(sql, table) -> None:
    assert _written_table(text(sql)) == table
