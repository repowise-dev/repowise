"""A file with zero coverable lines is "not applicable", not 0% covered.

The parser fix (``coverage/lcov.py`` and its three siblings) only means
something if the value survives the rest of the chain: the engine context, the
biomarkers that read it, the coverage-map merge, and the persisted column. Each
of those could re-introduce the hard zero on its own, and the two failing
behaviours the issue reports (``coverage_gradient`` and ``untested_hotspot``
firing on a type-only module) are the end of that chain rather than a parser
detail.

The last group covers the schema side. ``coverage_files.line_coverage_pct``
was NOT NULL, so writing the new value needs the column to accept NULL on both
backends: managed Postgres through the migration, local SQLite through
``init_db``'s model-driven reconciler, since local stores never run Alembic.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import Float
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.coverage_gap import CoverageGapDetector
from repowise.core.analysis.health.biomarkers.coverage_gradient import (
    CoverageGradientDetector,
)
from repowise.core.analysis.health.biomarkers.untested_hotspot import (
    UntestedHotspotDetector,
)
from repowise.core.analysis.health.coverage import parse_lcov, resolve_reports
from repowise.core.analysis.health.coverage.model import FileCoverage
from repowise.core.persistence import create_engine, init_db
from repowise.core.persistence.crud import (
    get_coverage_summary,
    load_coverage_for_repo,
    save_coverage_files,
    upsert_repository,
)

_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "packages" / "core" / "alembic"

# The issue's reproduction: one type-only module, one genuinely uncovered file.
_REPRO = """TN:
SF:src/types.ts
FNF:0
FNH:0
LF:0
LH:0
BRF:0
BRH:0
end_of_record
TN:
SF:src/real.ts
FNF:2
FNH:0
DA:1,0
DA:2,0
LF:2
LH:0
BRF:0
BRH:0
end_of_record
"""


def _ctx(*, path: str, line_cov: float | None, total: int) -> FileContext:
    return FileContext(
        file_path=path,
        language="typescript",
        nloc=40,
        has_test_file=False,
        module="src",
        git_meta={"is_hotspot": True, "commit_count_90d": 30},
        dependents_count=21,
        line_coverage_pct=line_cov,
        branch_coverage_pct=None,
        covered_lines=set(),
        total_coverable_lines=total,
    )


# ---------------------------------------------------------------------------
# The biomarkers the issue names.
# ---------------------------------------------------------------------------


def test_coverage_gradient_is_silent_on_a_type_only_module() -> None:
    """It documented "never imputes uncovered for missing data" and broke it.

    With None from the parser the existing ``cov is None`` guard does the work,
    so the detector needs no change of its own.
    """
    fc = {f.file_path: f for f in parse_lcov(_REPRO).files}["src/types.ts"]
    ctx = _ctx(path="src/types.ts", line_cov=fc.line_coverage_pct, total=0)

    assert fc.line_coverage_pct is None
    assert CoverageGradientDetector().detect(ctx) == []


def test_untested_hotspot_does_not_accuse_a_type_only_module() -> None:
    """The issue's ``-2.00 critical "0% line coverage and 21 dependents"``.

    It is a hotspot with 21 dependents and no test file, so only the coverage
    signal can suppress it. With None there is no measurement to accuse it of,
    and the no-coverage branch finds no signal that a test touches it either,
    so it still fires: the honest answer here is "we did not measure this",
    which is exactly what the biomarker's no-coverage path reports. What must
    not happen is the *measured-zero* accusation.
    """
    fc = {f.file_path: f for f in parse_lcov(_REPRO).files}["src/types.ts"]
    ctx = _ctx(path="src/types.ts", line_cov=fc.line_coverage_pct, total=0)

    results = UntestedHotspotDetector().detect(ctx)

    assert len(results) == 1
    # The finding must not claim a measured 0% line coverage.
    assert results[0].details["line_coverage_pct"] is None
    assert "0% line coverage" not in results[0].reason


def test_coverage_gap_is_silent_on_a_type_only_module() -> None:
    """Its own ``total_coverable_lines <= 0`` guard, reached with a real None."""
    fc = {f.file_path: f for f in parse_lcov(_REPRO).files}["src/types.ts"]
    ctx = _ctx(path="src/types.ts", line_cov=fc.line_coverage_pct, total=0)

    assert CoverageGapDetector().detect(ctx) == []


def test_a_genuinely_uncovered_file_still_fires_both_biomarkers() -> None:
    """The fix must not silence the real finding next to it in the same report.

    ``src/real.ts`` has 2 coverable lines and 0 hit. It stays a hard 0.0 in the
    engine context, so the deductions the issue is about still apply to it.
    """
    fc = {f.file_path: f for f in parse_lcov(_REPRO).files}["src/real.ts"]
    ctx = _ctx(path="src/real.ts", line_cov=fc.line_coverage_pct, total=2)

    assert fc.line_coverage_pct == 0.0
    assert len(CoverageGradientDetector().detect(ctx)) == 1
    assert len(UntestedHotspotDetector().detect(ctx)) == 1


def test_the_gradient_deduction_still_applies_to_a_measured_zero() -> None:
    """Pins that ``None`` and ``0.0`` are not the same input downstream."""
    uncovered = _ctx(path="src/real.ts", line_cov=0.0, total=2)
    absent = _ctx(path="src/types.ts", line_cov=None, total=0)

    assert CoverageGradientDetector().detect(uncovered)[0].deduction == 4.0
    assert CoverageGradientDetector().detect(absent) == []


# ---------------------------------------------------------------------------
# Resolution and merge.
# ---------------------------------------------------------------------------


def test_resolve_reports_carries_the_none_without_flattening_it() -> None:
    """``resolve_reports`` rebuilds each ``FileCoverage`` and merges hit-wins.

    Both steps copy the value through, so either could turn None into 0.0.
    """
    resolved = resolve_reports([parse_lcov(_REPRO)], {"src/types.ts", "src/real.ts"})

    assert resolved.coverage_map["src/types.ts"]["line_coverage_pct"] is None
    assert resolved.coverage_map["src/real.ts"]["line_coverage_pct"] == 0.0
    assert resolved.coverage_map["src/types.ts"]["total_coverable_lines"] == 0


def test_merging_a_zero_coverable_file_keeps_not_applicable() -> None:
    """The merge recomputes the percentage, so it has its own zero guard.

    Two reports for a file with nothing coverable must not produce 0.0, and a
    report that does have lines for it must win.
    """
    empty = resolve_reports(
        [parse_lcov("SF:src/types.ts\nLF:0\nLH:0\nend_of_record\n")], {"src/types.ts"}
    )
    assert empty.coverage_map["src/types.ts"]["line_coverage_pct"] is None

    with_lines = resolve_reports(
        [
            parse_lcov("SF:src/types.ts\nLF:0\nLH:0\nend_of_record\n"),
            parse_lcov("SF:src/types.ts\nDA:1,0\nDA:2,1\nLF:2\nLH:1\nend_of_record\n"),
        ],
        {"src/types.ts"},
    )
    assert with_lines.coverage_map["src/types.ts"]["line_coverage_pct"] == 50.0


# ---------------------------------------------------------------------------
# Persistence.
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path: Path):
    """An in-memory store with the full schema plus one repository row.

    Yields ``(session, repo)``. Local to this module rather than imported from
    ``tests/unit/persistence/conftest.py``: that conftest is scoped to its own
    package's tests, and these are health tests that happen to write rows.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            row = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            yield session, row
    finally:
        await engine.dispose()


async def test_a_not_applicable_row_round_trips_through_the_store(repo) -> None:
    """The column has to hold NULL or the ingest raises on the way in."""
    session, row = repo
    await save_coverage_files(
        session,
        row.id,
        [
            {
                "file_path": "src/types.ts",
                "line_coverage_pct": None,
                "covered_lines": [],
                "total_coverable_lines": 0,
            },
            {
                "file_path": "src/real.ts",
                "line_coverage_pct": 0.0,
                "covered_lines": [],
                "total_coverable_lines": 2,
            },
        ],
        source_format="lcov",
    )

    rows = {r.file_path: r for r in await load_coverage_for_repo(session, row.id)}

    assert rows["src/types.ts"].line_coverage_pct is None
    assert rows["src/real.ts"].line_coverage_pct == 0.0


async def test_save_accepts_a_file_coverage_dataclass_with_none(repo) -> None:
    """The CLI hands over ``FileCoverage`` objects, not dicts.

    ``save_coverage_files`` used to ``float()`` the value unconditionally,
    which raises on None before the row is ever built.
    """
    session, row = repo
    await save_coverage_files(
        session,
        row.id,
        [
            FileCoverage(
                file_path="src/types.ts",
                line_coverage_pct=None,
                branch_coverage_pct=None,
                covered_lines=[],
                total_coverable_lines=0,
            )
        ],
        source_format="lcov",
    )

    rows = await load_coverage_for_repo(session, row.id)

    assert rows[0].line_coverage_pct is None


async def test_the_repo_rollup_skips_the_unmeasurable_file(repo) -> None:
    """The summary divided by the percentage, so None raised TypeError.

    It still counts the file in ``file_count`` (it was instrumented) but
    contributes nothing to covered or total, neither of which a file with no
    coverable lines can move.
    """
    session, row = repo
    await save_coverage_files(
        session,
        row.id,
        [
            {
                "file_path": "src/types.ts",
                "line_coverage_pct": None,
                "covered_lines": [],
                "total_coverable_lines": 0,
            },
            {
                "file_path": "src/real.ts",
                "line_coverage_pct": 0.0,
                "covered_lines": [],
                "total_coverable_lines": 2,
            },
            {
                "file_path": "src/half.ts",
                "line_coverage_pct": 50.0,
                "covered_lines": [1],
                "total_coverable_lines": 2,
            },
        ],
        source_format="lcov",
    )

    summary = await get_coverage_summary(session, row.id)

    assert summary["file_count"] == 3
    assert summary["covered_lines"] == 1
    assert summary["total_lines"] == 4
    assert summary["line_coverage_pct"] == 25.0


async def test_the_rollup_is_unchanged_by_the_unmeasurable_file(repo) -> None:
    """Adding an LF:0 row must not move the repo percentage at all.

    Before the fix it dragged the average toward zero, which is the same bug
    seen from the other side.
    """
    session, row = repo
    measurable = [
        {
            "file_path": "src/half.ts",
            "line_coverage_pct": 50.0,
            "covered_lines": [1],
            "total_coverable_lines": 2,
        },
        {
            "file_path": "src/full.ts",
            "line_coverage_pct": 100.0,
            "covered_lines": [1, 2],
            "total_coverable_lines": 2,
        },
    ]
    await save_coverage_files(session, row.id, measurable, source_format="lcov")
    before = await get_coverage_summary(session, row.id)

    await save_coverage_files(
        session,
        row.id,
        [
            *measurable,
            {
                "file_path": "src/types.ts",
                "line_coverage_pct": None,
                "covered_lines": [],
                "total_coverable_lines": 0,
            },
        ],
        source_format="lcov",
    )
    after = await get_coverage_summary(session, row.id)

    assert before["line_coverage_pct"] == after["line_coverage_pct"] == 75.0


# ---------------------------------------------------------------------------
# Schema: the column has to accept NULL on both backends.
# ---------------------------------------------------------------------------


def _coverage_files_ddl(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE name='coverage_files'").fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _line_pct_is_not_null(db_path: Path) -> bool:
    """Whether the live table still declares the column NOT NULL.

    Read off the column's own line of the CREATE TABLE rather than the whole
    statement: every other column on this table is legitimately NOT NULL, so a
    substring search over the DDL answers the wrong question.
    """
    ddl = _coverage_files_ddl(db_path)
    for line in ddl.splitlines():
        if "line_coverage_pct" in line:
            return "NOT NULL" in line
    raise AssertionError(f"line_coverage_pct missing from {ddl!r}")


def _make_legacy(conn) -> None:
    """Rebuild the table the way every store written before this fix looks.

    ``line_coverage_pct FLOAT DEFAULT 0.0 NOT NULL`` is what migration 0019
    created and what the model declared until this change.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    op = Operations(MigrationContext.configure(conn))
    with op.batch_alter_table("coverage_files") as batch_op:
        batch_op.alter_column(
            "line_coverage_pct",
            existing_type=Float(),
            nullable=False,
            server_default="0.0",
        )


def _seed_a_legacy_store(db_path: Path) -> str:
    """A store on the old schema with one row already in it. Returns its id.

    Synchronous on purpose, driving its own ``asyncio.run``: these tests also
    run ``alembic command.upgrade``, whose ``env.py`` calls ``asyncio.run``
    itself and cannot be invoked from inside a live loop.
    """

    async def _build() -> str:
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            await init_db(engine)
            async with engine.begin() as conn:
                await conn.run_sync(_make_legacy)
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                repo = await upsert_repository(session, name="repo", local_path=str(db_path))
                await save_coverage_files(
                    session,
                    repo.id,
                    [
                        {
                            "file_path": "src/keep.ts",
                            "line_coverage_pct": 42.5,
                            "covered_lines": [1, 2],
                            "total_coverable_lines": 4,
                        }
                    ],
                    source_format="lcov",
                )
                await session.commit()
                return repo.id
        finally:
            await engine.dispose()

    return asyncio.run(_build())


def _rows(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return list(
            conn.execute(
                "SELECT file_path, line_coverage_pct, covered_lines_json, "
                "total_coverable_lines, mapping_partial FROM coverage_files "
                "ORDER BY file_path"
            )
        )
    finally:
        conn.close()


def test_reconciler_relaxes_a_legacy_store_and_keeps_its_rows(tmp_path: Path) -> None:
    """Local SQLite stores never run Alembic, so ``init_db`` has to do it.

    Without this the column stays NOT NULL on every store created before the
    fix and the next ``coverage add`` dies on a constraint violation for a
    value the model now permits.

    The rebuild is a real table rebuild on SQLite, so the row that was already
    there is the thing at risk and is checked first, before any write. (The
    write itself is a delete-then-insert per repo, so it replaces the row by
    design and says nothing about the rebuild.)
    """
    db_path = tmp_path / "wiki.db"
    repo_id = _seed_a_legacy_store(db_path)
    assert _line_pct_is_not_null(db_path)
    assert len(_rows(db_path)) == 1

    # Re-open the way an upgraded install does.
    async def _reopen() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(_reopen())

    assert not _line_pct_is_not_null(db_path)
    assert _rows(db_path) == [("src/keep.ts", 42.5, "[1, 2]", 4, 0)]

    async def _write_the_unmeasurable_row() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                await save_coverage_files(
                    session,
                    repo_id,
                    [
                        {
                            "file_path": "src/types.ts",
                            "line_coverage_pct": None,
                            "covered_lines": [],
                            "total_coverable_lines": 0,
                        }
                    ],
                    source_format="lcov",
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_write_the_unmeasurable_row())

    # The write the fix exists for: NULL on a column that was NOT NULL.
    assert _rows(db_path) == [("src/types.ts", None, "[]", 0, 0)]


def test_reconciling_a_legacy_store_is_idempotent(tmp_path: Path) -> None:
    """A second ``init_db`` must not rebuild the table again or fail."""
    db_path = tmp_path / "wiki.db"
    _seed_a_legacy_store(db_path)

    async def _reopen_twice() -> None:
        for _ in range(2):
            engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
            try:
                await init_db(engine)
            finally:
                await engine.dispose()

    asyncio.run(_reopen_twice())

    assert not _line_pct_is_not_null(db_path)
    assert len(_rows(db_path)) == 1


def test_the_migration_widens_the_column_on_sqlite(tmp_path: Path) -> None:
    """The migration chain runs against SQLite in this repo's own tests.

    Pins that ``0044``-style ``upgrade head`` reaches a column that accepts
    NULL, which is what the managed Postgres store gets from the same file.
    """
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "migrated.db"
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[3] / "packages" / "core" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    command.upgrade(config, "head")

    assert not _line_pct_is_not_null(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO coverage_files (id, repository_id, file_path, "
            "source_format, line_coverage_pct, covered_lines_json, "
            "total_coverable_lines, ingested_at, mapping_partial) "
            "VALUES ('a', '1', 'src/types.ts', 'lcov', NULL, '[]', 0, "
            "'2026-01-01', 0)"
        )
        conn.commit()
    finally:
        conn.close()

    assert _rows(db_path) == [("src/types.ts", None, "[]", 0, 0)]


def test_the_migration_restores_not_null_when_downgraded(tmp_path: Path) -> None:
    """Downgrade has to clear the NULLs it cannot represent, not just fail."""
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "migrated.db"
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[3] / "packages" / "core" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    command.upgrade(config, "head")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO coverage_files (id, repository_id, file_path, "
            "source_format, line_coverage_pct, covered_lines_json, "
            "total_coverable_lines, ingested_at, mapping_partial) "
            "VALUES ('a', '1', 'src/types.ts', 'lcov', NULL, '[]', 0, "
            "'2026-01-01', 0)"
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(config, "0065")

    assert _line_pct_is_not_null(db_path)
    assert _rows(db_path) == [("src/types.ts", 0.0, "[]", 0, 0)]


def test_the_migrated_shape_matches_the_model(tmp_path: Path) -> None:
    """A hosted store and a local one must agree about this column.

    The local store is built by ``init_db`` and never sees Alembic, so the two
    descriptions of the table have to be kept in step by hand.
    """
    from alembic import command
    from alembic.config import Config

    migrated = tmp_path / "migrated.db"
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[3] / "packages" / "core" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{migrated}")
    command.upgrade(config, "head")

    declared = tmp_path / "declared.db"

    async def _build_declared() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{declared}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(_build_declared())

    def _nullable(path: Path) -> bool | None:
        conn = sqlite3.connect(path)
        try:
            for row in conn.execute('PRAGMA table_info("coverage_files")'):
                if row[1] == "line_coverage_pct":
                    return not bool(row[3])
            return None
        finally:
            conn.close()

    assert _nullable(migrated) is True
    assert _nullable(declared) is True


def test_a_failed_relax_is_reported_not_swallowed(tmp_path: Path, monkeypatch) -> None:
    """The reconciler's converge-over-calls contract still holds for this step.

    A relax that cannot run must be recorded and re-raised like any other
    failed statement, not left to look like a store that needed no repair.
    """
    import asyncio

    from repowise.core.persistence import database as db_module

    db_path = tmp_path / "wiki.db"

    def _boom(connection: object, table_name: str, column_name: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db_module, "_relax_not_null", _boom)

    async def _build() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            await init_db(engine)
            async with engine.begin() as conn:
                await conn.run_sync(_make_legacy)
        finally:
            await engine.dispose()

        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    with pytest.raises(Exception, match="locked"):
        asyncio.run(_build())


@pytest.mark.asyncio
async def test_the_engine_context_reads_none_not_zero(tmp_path: Path) -> None:
    """The engine is what hands the detector its number.

    ``line_cov = cov.get("line_coverage_pct") if cov else None`` already
    propagates whatever the map holds, so this pins the end of the chain the
    issue is about: a stored LF:0 file reaches ``FileContext`` as None.
    """
    from repowise.core.pipeline.incremental import load_stored_coverage_map

    db_path = tmp_path / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True)
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        await init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
            await save_coverage_files(
                session,
                repo.id,
                [
                    {
                        "file_path": "src/types.ts",
                        "line_coverage_pct": None,
                        "covered_lines": [],
                        "total_coverable_lines": 0,
                    }
                ],
                source_format="lcov",
            )
            await session.commit()
    finally:
        await engine.dispose()

    coverage_map = await load_stored_coverage_map(tmp_path)

    assert coverage_map["src/types.ts"]["line_coverage_pct"] is None
