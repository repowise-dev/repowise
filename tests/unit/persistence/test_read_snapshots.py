"""Read snapshots: the Fix first queue and the actions view stored at index time.

Written once the stores are final, served while current, dropped by any write
to a store they read, equal to a live build, and stored by a reader that
finds one missing only while no input write lands during its build.
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
from repowise.core.persistence.read_snapshots import (
    SELF_KEYED_KINDS,
    _written_table,
    decode_or_none,
)
from tests.unit.health.fix_first_rows import FINDINGS, METRICS, PERFORMANCE, PLANS, REFACTORING
from tests.unit.persistence.test_actions_loader import NOW, _seed


def _wire(value) -> str:
    return json.dumps(value, sort_keys=False, default=str)


async def _live(session, rid: str) -> tuple[str, str]:
    clear_fix_first_cache()
    view = await load_actions_view(session, rid, now=NOW)
    queue = await load_fix_first(session, rid, limit=None)
    return _wire(view), _wire(queue.as_dict())


async def _snapshot_rows(session) -> int:
    """Rows the purge owns; a self-keyed kind checks its own key on read."""
    query = select(func.count()).where(ReadSnapshot.kind.not_in(SELF_KEYED_KINDS))
    return (await session.execute(query)).scalar_one()


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
    assert sorted(kinds) == ["actions", "fix_first", "fix_first_counts", "test_masks"]
    # Nothing moved since, so a second pass writes nothing.
    assert not await fix_first_loader.write_fix_first_snapshot(async_session, rid)
    assert not await actions_loader.write_actions_snapshot(async_session, rid)


async def test_a_fresh_snapshot_is_served_and_equals_a_live_build(
    async_session, no_live_build
) -> None:
    rid = await _seed(async_session)
    view, queue = await _live(async_session, rid)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()

    no_live_build()
    served = await load_fix_first(async_session, rid, limit=None)
    assert _wire(served.as_dict()) == queue
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
    assert await _snapshot_rows(async_session) == 3

    await change(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 0
    view, queue = await _live(async_session, rid)
    assert _wire((await load_fix_first(async_session, rid, limit=None)).as_dict()) == queue
    assert _wire(await load_actions_view(async_session, rid, now=NOW)) == view


async def test_a_dropped_snapshot_would_have_been_wrong(async_session) -> None:
    rid = await _seed(async_session)
    before = await _live(async_session, rid)
    await _triage(async_session, rid)
    await _new_secret(async_session, rid)
    await async_session.commit()
    view, queue = await _live(async_session, rid)
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
    assert await _snapshot_rows(async_session) == 3


async def test_writes_before_the_snapshot_in_one_session_keep_it(async_session) -> None:
    rid = await _seed(async_session)  # pending input rows in this session
    await _new_secret(async_session, rid)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 3


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
    view, queue = await _live(async_session, rid)
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


async def test_a_read_of_a_store_it_cannot_reopen_writes_nothing(async_session) -> None:
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
    assert any(i.verify.tests for i in queue.items)
    stored = json.loads(json.dumps(asdict(queue)))
    assert decode_or_none(FixFirstQueue, stored) == queue


def test_a_queue_stored_before_steps_had_code_still_decodes() -> None:
    """A snapshot written before steps carried their helper texts and command,
    and Verify its prerequisite, reads those as absent until it is rewritten."""
    queue = build_fix_first(metrics=METRICS, findings=FINDINGS, refactoring=REFACTORING,
                            performance=PERFORMANCE, plans=PLANS, limit=None)
    stored = json.loads(json.dumps(asdict(queue)))
    for item in stored["items"]:
        del item["verify"]["prerequisite"]
        for step in item["action"]["steps"]:
            for key in ("signature", "call", "command"):
                del step[key]
    decoded = decode_or_none(FixFirstQueue, stored)
    assert decoded is not None and [i.id for i in decoded.items] == [i.id for i in queue.items]
    assert all(s.command is None for i in decoded.items for s in i.action.steps)


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
    assert await _snapshot_rows(async_session) == 3
    view, queue = await _live(async_session, rid)
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
    failed = await write_read_snapshots(async_session, rid)
    await async_session.commit()
    assert await _snapshot_rows(async_session) == 0
    # Named for the caller, whose output hides the core log.
    assert [(name, str(exc)) for name, exc in failed] == [("actions", "build failed")]


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
    assert await _snapshot_rows(async_session) == 3


async def test_a_corrupt_payload_is_a_miss(async_session) -> None:
    rid = await _seed(async_session)
    view, queue = await _live(async_session, rid)
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


async def _decision(session, rid) -> None:
    from repowise.core.persistence.crud.decisions import upsert_decision
    from repowise.core.persistence.decision_graph import sync_links_from_record

    record = await upsert_decision(
        session, repository_id=rid, title="Keep the core pure", decision="No I/O in core",
        affected_files=["src/core.py"],
    )
    await sync_links_from_record(session, record)


async def _commit_health(session, rid) -> None:
    from repowise.core.persistence.models import GitCommitHealthFinding

    session.add(
        GitCommitHealthFinding(
            repository_id=rid, sha="head", change_finding_id="c1", change_kind="introduced",
            dimension="maintainability", biomarker_type="long_method", severity="high",
            file_path="src/core.py",
        )
    )


async def _kinds(session) -> list[str]:
    rows = await session.execute(
        select(ReadSnapshot.kind).where(ReadSnapshot.kind.not_in(SELF_KEYED_KINDS))
    )
    return sorted(rows.scalars())


@pytest.mark.parametrize("change", [_decision, _commit_health])
async def test_a_write_one_view_does_not_read_keeps_that_view(async_session, change) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()

    await change(async_session, rid)
    await async_session.commit()
    # The actions view reads decisions and commit-health findings; the queue does not.
    assert await _kinds(async_session) == ["fix_first", "fix_first_counts"]
    view, queue = await _live(async_session, rid)
    clear_fix_first_cache()
    assert _wire((await load_fix_first(async_session, rid, limit=None)).as_dict()) == queue
    assert _wire(await load_actions_view(async_session, rid, now=NOW)) == view
    # The next writer rebuilds only the dropped view.
    assert not await fix_first_loader.write_fix_first_snapshot(async_session, rid)
    assert await actions_loader.write_actions_snapshot(async_session, rid)


async def test_a_write_to_a_kept_table_and_an_input_drops_every_view(async_session) -> None:
    rid = await _seed(async_session)
    await write_read_snapshots(async_session, rid)
    await async_session.commit()
    await _decision(async_session, rid)
    await _triage(async_session, rid)
    await async_session.commit()
    assert await _kinds(async_session) == []


def test_the_actions_view_skips_no_table_the_queue_reads() -> None:
    """Guard: ``actions`` quotes the stored ``fix_first`` row, so any table it
    may skip must be one the queue skips too."""
    from repowise.core.persistence.read_snapshots import NOT_READ_BY

    assert NOT_READ_BY.get("actions", frozenset()) <= NOT_READ_BY["fix_first"]


# A reader that finds the actions view missing stores what it built, on a
# file store; the in-memory fixtures above cannot be reopened, so they build
# live and write nothing.


@pytest.fixture
async def file_factory(tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.core.persistence.database import create_engine, init_db

    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}")
    await init_db(engine)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


async def _seeded(factory) -> str:
    async with factory() as session:
        rid = await _seed(session)
        await session.commit()
    return rid


async def _read_view(factory, rid: str, **cut) -> dict:
    async with factory() as session:
        return await load_actions_view(session, rid, now=NOW, **cut)


async def _actions_key(factory) -> str | None:
    async with factory() as session:
        query = select(ReadSnapshot.key).where(ReadSnapshot.kind == "actions")
        return (await session.execute(query)).scalar_one_or_none()


async def test_a_read_that_finds_no_view_stores_it_for_the_next(
    file_factory, no_live_build
) -> None:
    rid = await _seeded(file_factory)
    first = await _read_view(file_factory, rid)
    assert await _actions_key(file_factory) == actions_loader._KEY

    no_live_build()
    assert _wire(await _read_view(file_factory, rid)) == _wire(first)


async def test_an_input_write_during_the_build_is_not_stored_over(
    file_factory, monkeypatch
) -> None:
    rid = await _seeded(file_factory)
    real = actions_loader._ruled

    async def racing(session, repo_id, *args, **kwargs):
        built = await real(session, repo_id, *args, **kwargs)
        async with file_factory() as other:  # lands after the build read the stores
            await _triage(other, repo_id)
            await other.commit()
        return built

    monkeypatch.setattr(actions_loader, "_ruled", racing)
    await _read_view(file_factory, rid)
    assert await _actions_key(file_factory) is None


async def test_a_later_readers_claim_wins(file_factory, monkeypatch) -> None:
    from repowise.core.persistence import read_snapshots

    rid = await _seeded(file_factory)
    real = actions_loader._ruled
    engine = file_factory.kw["bind"]

    async def overtaken(session, repo_id, *args, **kwargs):
        await read_snapshots._claim(engine, repo_id, "actions", "claim:later")
        return await real(session, repo_id, *args, **kwargs)

    monkeypatch.setattr(actions_loader, "_ruled", overtaken)
    await _read_view(file_factory, rid)
    assert await _actions_key(file_factory) == "claim:later"


async def test_a_locked_store_skips_the_store_without_waiting(file_factory, tmp_path) -> None:
    import sqlite3
    import time

    rid = await _seeded(file_factory)
    holder = sqlite3.connect(tmp_path / "wiki.db")
    try:
        holder.execute("BEGIN IMMEDIATE")
        started = time.perf_counter()
        view = await _read_view(file_factory, rid)
        assert time.perf_counter() - started < 10  # the store's busy timeout is 30 s
    finally:
        holder.rollback()
        holder.close()
    assert view["unavailable"] == {}
    assert await _actions_key(file_factory) is None


async def test_a_read_only_store_builds_and_writes_nothing(file_factory, tmp_path) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from repowise.core.persistence.database import create_engine

    rid = await _seeded(file_factory)
    uri = f"sqlite+aiosqlite:///file:{(tmp_path / 'wiki.db').as_posix()}?mode=ro&uri=true"
    engine = create_engine(uri)
    try:
        read_only = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        assert (await _read_view(read_only, rid))["unavailable"] == {}
    finally:
        await engine.dispose()
    assert await _actions_key(file_factory) is None


async def test_a_workspace_read_with_the_cut_stores_nothing(file_factory) -> None:
    rid = await _seeded(file_factory)
    await _read_view(file_factory, rid, alias="member")
    assert await _actions_key(file_factory) is None
