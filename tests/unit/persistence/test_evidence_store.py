"""The runtime evidence store: bulk writes, restamps, pruning and both upgrade paths."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select

from repowise.core.persistence.crud import (
    EVIDENCE_ROW_TABLES,
    prune_evidence_runs,
    save_evidence_run,
)
from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.models import (
    Base,
    EvidenceRun,
    EvidenceSymbolCost,
    EvidenceTestTiming,
)
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config

_TABLES = (
    "evidence_runs",
    "evidence_symbol_costs",
    "evidence_benchmark_results",
    "evidence_test_timings",
    "evidence_test_stats",
    "evidence_query_counts",
    "symbol_hotness",
)


def _run(key: str = "k1", *, commit: str | None = "a" * 40, kind: str = "profile", **extra):
    return {
        "run_key": key,
        "kind": kind,
        "format": "repowise-evidence-v1",
        "source": "cli",
        "workload_name": "cli build",
        "workload_class": "entry_point",
        "value_kind": "wall",
        "unit": "samples",
        "commit_sha": commit,
        "dirty": False,
        "checkout_root": "/work/repo",
        **extra,
    }


def _costs(n: int) -> list[dict]:
    return [
        {"symbol_id": f"src/m.py::f{i}", "self_value": float(i), "total_value": float(i + 1)}
        for i in range(n)
    ]


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_a_run_and_its_rows_are_written_in_bulk(async_session) -> None:
    repo = await insert_repo(async_session)
    write = await save_evidence_run(
        async_session, repo.id, _run(), _costs(1200), history_runs=5, max_runs=50
    )
    await async_session.commit()

    assert write.restamped is False
    assert write.rows_written == 1200
    stored = (await async_session.execute(select(EvidenceRun))).scalar_one()
    assert stored.id == write.run_id
    assert stored.environment == "unknown"
    assert stored.mapping_partial is False
    # Unknown counts stay unknown, never zero.
    assert stored.units_matched is None
    assert stored.total_value is None
    assert await _count(async_session, EvidenceSymbolCost) == 1200
    row = (
        await async_session.execute(
            select(EvidenceSymbolCost).where(EvidenceSymbolCost.symbol_id == "src/m.py::f3")
        )
    ).scalar_one()
    assert (row.run_id, row.repository_id, row.lineage_id) == (write.run_id, repo.id, None)
    assert row.self_samples is None


async def test_the_same_file_at_the_same_commit_restamps_the_run(async_session) -> None:
    repo = await insert_repo(async_session)
    first = await save_evidence_run(
        async_session, repo.id, _run(), _costs(3), history_runs=5, max_runs=50
    )
    later = datetime.now(UTC) + timedelta(hours=1)
    again = await save_evidence_run(
        async_session, repo.id, _run(ingested_at=later), _costs(3), history_runs=5, max_runs=50
    )
    await async_session.commit()

    assert again.restamped is True
    assert again.run_id == first.run_id
    assert await _count(async_session, EvidenceRun) == 1
    assert await _count(async_session, EvidenceSymbolCost) == 3
    stored = (await async_session.execute(select(EvidenceRun))).scalar_one()
    assert stored.ingested_at.replace(tzinfo=UTC) == later


@pytest.mark.parametrize(
    ("second", "expected_runs"),
    [
        ({"key": "k1", "commit": "b" * 40}, 2),  # another commit is another measurement
        ({"key": "k2", "commit": "a" * 40}, 2),  # other content at the same commit
        ({"key": "k1", "commit": None}, 2),  # no commit: cannot be told apart
    ],
)
async def test_other_measurements_append(async_session, second, expected_runs) -> None:
    repo = await insert_repo(async_session)
    await save_evidence_run(
        async_session, repo.id, _run(), _costs(2), history_runs=5, max_runs=50
    )
    write = await save_evidence_run(
        async_session, repo.id, _run(second["key"], commit=second["commit"]), _costs(2),
        history_runs=5,
        max_runs=50,
    )
    await async_session.commit()
    assert write.restamped is False
    assert await _count(async_session, EvidenceRun) == expected_runs
    assert await _count(async_session, EvidenceSymbolCost) == 4


async def test_pruning_drops_the_oldest_runs_of_the_kind_and_their_rows(async_session) -> None:
    repo = await insert_repo(async_session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    profile_ids = []
    for i in range(4):
        write = await save_evidence_run(
            async_session,
            repo.id,
            _run(f"p{i}", ingested_at=base + timedelta(days=i)),
            _costs(2),
            history_runs=2,
            max_runs=50,
        )
        profile_ids.append(write.run_id)
    timing = await save_evidence_run(
        async_session,
        repo.id,
        _run("t0", kind="test_timing", ingested_at=base - timedelta(days=10)),
        [{"test_id": "tests/test_a.py::test_one", "duration_s": 0.5}],
        history_runs=2,
        max_runs=50,
    )
    await async_session.commit()

    kept = set((await async_session.execute(select(EvidenceRun.id))).scalars())
    # Another kind's older run is not counted against the profile bound.
    assert kept == {profile_ids[2], profile_ids[3], timing.run_id}
    cost_runs = set((await async_session.execute(select(EvidenceSymbolCost.run_id))).scalars())
    assert cost_runs == {profile_ids[2], profile_ids[3]}
    assert await _count(async_session, EvidenceTestTiming) == 1

    assert (
        await prune_evidence_runs(async_session, repo.id, "profile", per_workload=1, per_kind=1)
        == 1
    )
    assert await _count(async_session, EvidenceSymbolCost) == 2


async def test_pruning_is_per_workload_under_a_cap_per_kind(async_session) -> None:
    repo = await insert_repo(async_session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ids: dict[str, list[str]] = {"rare": [], "busy": []}
    for i, workload in enumerate(["rare", "busy", "busy", "busy", "busy"]):
        write = await save_evidence_run(
            async_session,
            repo.id,
            _run(f"r{i}", workload_name=workload, ingested_at=base + timedelta(days=i)),
            _costs(1),
            history_runs=2,
            max_runs=50,
        )
        ids[workload].append(write.run_id)
    kept = set((await async_session.execute(select(EvidenceRun.id))).scalars())
    # The busy workload keeps its newest two and leaves the rare one alone.
    assert kept == {ids["rare"][0], *ids["busy"][-2:]}

    # The cap per kind trims the busy workload, never the rare one's only run.
    await prune_evidence_runs(async_session, repo.id, "profile", per_workload=2, per_kind=2)
    assert set((await async_session.execute(select(EvidenceRun.id))).scalars()) == {
        ids["rare"][0],
        ids["busy"][-1],
    }


async def test_a_busy_workload_past_the_cap_keeps_a_rare_workloads_old_run(
    async_session,
) -> None:
    repo = await insert_repo(async_session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rare = await save_evidence_run(
        async_session,
        repo.id,
        _run("rare", workload_name="rare", ingested_at=base),
        _costs(1),
        history_runs=5,
        max_runs=3,
    )
    busy = []
    for i in range(6):
        write = await save_evidence_run(
            async_session,
            repo.id,
            _run(f"b{i}", workload_name="busy", ingested_at=base + timedelta(days=i + 1)),
            _costs(1),
            history_runs=5,
            max_runs=3,
        )
        busy.append(write.run_id)
    await async_session.commit()

    kept = set((await async_session.execute(select(EvidenceRun.id))).scalars())
    assert kept == {rare.run_id, *busy[-2:]}
    cost_runs = set((await async_session.execute(select(EvidenceSymbolCost.run_id))).scalars())
    assert cost_runs == kept

    # More workloads than the cap: each keeps only its newest run.
    assert (
        await prune_evidence_runs(async_session, repo.id, "profile", per_workload=5, per_kind=1)
        == 1
    )
    kept = set((await async_session.execute(select(EvidenceRun.id))).scalars())
    assert kept == {rare.run_id, busy[-1]}


async def test_writer_mistakes_raise(async_session) -> None:
    repo = await insert_repo(async_session)
    with pytest.raises(ValueError, match="unknown evidence kind"):
        await save_evidence_run(
            async_session, repo.id, _run(kind="trace"), [], history_runs=1, max_runs=1
        )
    with pytest.raises(ValueError, match="not columns of evidence_symbol_costs"):
        await save_evidence_run(
            async_session,
            repo.id,
            _run(),
            [{"symbol_id": "a::b", "samples": 3}],
            history_runs=1,
            max_runs=1,
        )
    with pytest.raises(ValueError, match="repeat the key"):
        await save_evidence_run(
            async_session,
            repo.id,
            _run(kind="test_timing"),
            [{"test_id": "t::a", "duration_s": 1.0}, {"test_id": "t::a", "duration_s": 2.0}],
            history_runs=1,
            max_runs=1,
        )
    assert await _count(async_session, EvidenceRun) == 0


async def test_a_failed_row_batch_leaves_no_run(async_session) -> None:
    repo = await insert_repo(async_session)
    with pytest.raises(Exception, match="NOT NULL"):
        await save_evidence_run(
            async_session,
            repo.id,
            _run(kind="query_count"),
            [{"scope_kind": "test", "scope_id": "t::a", "site_symbol_id": "m::f"}],
            history_runs=1,
            max_runs=1,
        )
    assert await _count(async_session, EvidenceRun) == 0


def test_every_kind_has_a_table_keyed_by_run() -> None:
    for table in EVIDENCE_ROW_TABLES.values():
        assert table.c.run_id.primary_key
        assert {fk.column.table.name for fk in table.c.run_id.foreign_keys} == {"evidence_runs"}


def test_keys_are_the_reviewed_ones() -> None:
    """Local stores can never change these: a change here needs a new table."""
    keys = {
        name: [c.name for c in Base.metadata.tables[name].primary_key.columns]
        for name in _TABLES
    }
    assert keys == {
        "evidence_runs": ["id"],
        "evidence_symbol_costs": ["run_id", "symbol_id"],
        "evidence_benchmark_results": ["run_id", "benchmark_key"],
        "evidence_test_timings": ["run_id", "test_id"],
        "evidence_test_stats": ["repository_id", "environment", "test_id"],
        "evidence_query_counts": [
            "run_id", "scope_kind", "scope_id", "site_symbol_id", "statement_fp",
        ],
        "symbol_hotness": ["repository_id", "symbol_id"],
    }
    for name in _TABLES:
        table = Base.metadata.tables[name]
        # No unique constraint beyond the key: one could never be relaxed.
        assert not [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
        lineage = table.c.get("lineage_id")
        assert name == "evidence_runs" or (lineage is not None and lineage.nullable)


def _columns(path: Path, table: str) -> dict[str, tuple[int, int]]:
    """Column name -> (notnull, pk position) as SQLite stores it."""
    with sqlite3.connect(path) as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1]: (row[3], row[5]) for row in rows}


def _indexes(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(f"PRAGMA index_list({table})").fetchall()
    return {row[1] for row in rows if not row[1].startswith("sqlite_autoindex")}


def _init_db(path: Path) -> None:
    async def run() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{path}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("warm_start", [False, True])
def test_migration_matches_the_models_and_downgrades(tmp_path: Path, warm_start: bool) -> None:
    migrated = tmp_path / "migrated.db"
    config = _alembic_config(migrated)
    command.upgrade(config, "0124")
    if warm_start:
        # init_db may create the tables before the migration runs.
        _init_db(migrated)
    command.upgrade(config, "0131")
    command.stamp(config, "0132")  # 0132 is not the evidence store's; it refuses a warm start
    command.upgrade(config, "head")

    created = tmp_path / "created.db"
    _init_db(created)
    for table in _TABLES:
        assert _columns(migrated, table) == _columns(created, table), table
        assert _indexes(migrated, table) == _indexes(created, table), table

    command.downgrade(config, "0133")
    assert "wait_value" not in _columns(migrated, "evidence_runs")
    assert "wait_share" not in _columns(migrated, "symbol_hotness")
    command.downgrade(config, "0132")
    assert "tests_failed" not in _columns(migrated, "evidence_runs")
    command.stamp(config, "0131")
    command.downgrade(config, "0124")
    for table in _TABLES:
        assert _columns(migrated, table) == {}


def test_an_existing_store_gains_the_tables_on_init(tmp_path: Path) -> None:
    """Local stores never run Alembic: ``init_db`` alone must bring them up."""
    path = tmp_path / "wiki.db"
    _init_db(path)
    with sqlite3.connect(path) as connection:
        for table in reversed(_TABLES):
            connection.execute(f"DROP TABLE {table}")
        # Force the reconcile walk, as on a store written by an older release.
        connection.execute("PRAGMA user_version = 0")
    _init_db(path)
    for table in _TABLES:
        assert _columns(path, table), table
    with sqlite3.connect(path) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'evidence_symbol_costs'"
        ).fetchone()[0]
    assert "WITHOUT ROWID" in sql


def test_a_store_from_before_the_hotness_basis_gains_its_columns_on_init(tmp_path: Path) -> None:
    """The reconciler adds the nullable columns later migrations add; keys stay as they were."""
    old = tmp_path / "old.db"
    command.upgrade(_alembic_config(old), "0125")
    with sqlite3.connect(old) as connection:
        connection.execute("PRAGMA user_version = 0")
    _init_db(old)
    created = tmp_path / "created.db"
    _init_db(created)
    for table in ("evidence_runs", "evidence_symbol_costs", "symbol_hotness"):
        assert _columns(old, table) == _columns(created, table), table


def test_workload_hotness_migration_matches_the_model_and_downgrades(tmp_path: Path) -> None:
    """Derived and rebuilt whole, keyed by run and function; local stores get it from init_db."""
    table = Base.metadata.tables["workload_hotness"]
    assert [c.name for c in table.primary_key.columns] == ["repository_id", "run_id", "symbol_id"]
    migrated = tmp_path / "migrated.db"
    config = _alembic_config(migrated)
    command.upgrade(config, "head")
    created = tmp_path / "created.db"
    _init_db(created)
    assert _columns(migrated, "workload_hotness") == _columns(created, "workload_hotness")
    command.downgrade(config, "0134")
    assert _columns(migrated, "workload_hotness") == {}


def test_test_timings_first_in_class_migration_matches_the_model_and_downgrades(
    tmp_path: Path,
) -> None:
    migrated = tmp_path / "migrated.db"
    config = _alembic_config(migrated)
    command.upgrade(config, "head")
    created = tmp_path / "created.db"
    _init_db(created)
    assert _columns(migrated, "evidence_test_timings") == _columns(created, "evidence_test_timings")
    command.downgrade(config, "0135")
    assert "first_in_class" not in _columns(migrated, "evidence_test_timings")
