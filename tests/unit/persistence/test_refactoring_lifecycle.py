"""Refactoring plans keep their identity and their triage across analyses.

The writer is the only place lifecycle is decided, and both index paths call it,
which is what makes a full and an incremental run agree. These tests pin the
four things that decision has to get right: an unchanged plan is the same row, a
plan nobody detects any more resolves rather than vanishing, a decision a person
recorded survives the next analysis, and a false positive never comes back.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.analysis.health.refactoring import RefactoringSuggestion
from repowise.core.persistence.crud.analysis import (
    finalize_refactoring_suggestions,
    get_refactoring_suggestion,
    get_refactoring_suggestions,
    save_refactoring_suggestions,
    update_refactoring_suggestion_status,
    upsert_refactoring_suggestions,
)
from repowise.core.persistence.models import RefactoringSuggestion as SuggestionRow
from tests.unit.persistence.helpers import insert_repo


def _suggestion(path: str, target: str, **overrides) -> RefactoringSuggestion:
    base = dict(
        refactoring_type="extract_class",
        file_path=path,
        target_symbol=target,
        line_start=1,
        line_end=80,
        plan={"groups": [{"name": None, "methods": ["m1", "m2"], "fields": ["a"]}]},
        evidence={"lcom4": 2, "method_count": 6, "field_count": 2, "wmc": 9},
        impact_delta=2.4,
        effort_bucket="M",
        blast_radius={"dependents_count": 3},
        confidence="high",
        source_biomarker="low_cohesion",
    )
    base.update(overrides)
    return RefactoringSuggestion(**base)


async def _all_rows(session, repo_id: str) -> list[SuggestionRow]:
    result = await session.execute(
        select(SuggestionRow).where(SuggestionRow.repository_id == repo_id)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_an_unchanged_plan_keeps_its_row_and_its_public_id(async_session):
    repo = await insert_repo(async_session)
    plans = [_suggestion("a.py", "Foo"), _suggestion("b.py", "Bar")]

    await save_refactoring_suggestions(async_session, repo.id, plans)
    await async_session.commit()
    before = {row.public_id: (row.id, row.created_at) for row in await _all_rows(async_session, repo.id)}

    await save_refactoring_suggestions(async_session, repo.id, plans)
    await async_session.commit()
    after = {row.public_id: (row.id, row.created_at) for row in await _all_rows(async_session, repo.id)}

    assert len(before) == 2
    assert after == before, "a second analysis of unchanged source must not mint new rows"
    assert all(key.startswith("refac2_") for key in after)


@pytest.mark.asyncio
async def test_moving_a_class_down_the_file_does_not_mint_a_new_plan(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    [before] = await _all_rows(async_session, repo.id)

    await save_refactoring_suggestions(
        async_session, repo.id, [_suggestion("a.py", "Foo", line_start=400, line_end=479)]
    )
    await async_session.commit()
    rows = await _all_rows(async_session, repo.id)

    assert len(rows) == 1
    assert rows[0].id == before.id
    assert rows[0].line_start == 400, "the row still tracks where the class is now"


@pytest.mark.asyncio
async def test_a_plan_nobody_detects_resolves_rather_than_disappearing(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(
        async_session, repo.id, [_suggestion("a.py", "Foo"), _suggestion("b.py", "Bar")]
    )
    await async_session.commit()

    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()

    rows = {row.file_path: row for row in await _all_rows(async_session, repo.id)}
    assert rows["b.py"].status == "resolved"
    assert rows["b.py"].status_reason == "no_longer_detected"
    assert rows["b.py"].status_changed_at is not None
    # A held id keeps answering, and stops reading as current.
    assert await get_refactoring_suggestion(async_session, repo.id, rows["b.py"].public_id)
    assert [row.file_path for row in await get_refactoring_suggestions(async_session, repo.id)] == [
        "a.py"
    ]


@pytest.mark.asyncio
async def test_a_plan_that_comes_back_reopens_but_a_human_decision_does_not(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    [row] = await _all_rows(async_session, repo.id)

    await save_refactoring_suggestions(async_session, repo.id, [])
    await async_session.commit()
    await async_session.refresh(row)
    assert row.status == "resolved"

    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    await async_session.refresh(row)
    assert row.status == "open", "the detector disagreeing with no_longer_detected is the signal"
    assert row.status_reason is None

    await update_refactoring_suggestion_status(async_session, repo.id, row.id, "resolved")
    await async_session.commit()
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    await async_session.refresh(row)
    assert row.status == "resolved", "a person's decision outranks re-detection"
    assert row.status_reason == "user"


@pytest.mark.asyncio
async def test_a_false_positive_is_never_re_emitted(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    [row] = await _all_rows(async_session, repo.id)

    await update_refactoring_suggestion_status(
        async_session, repo.id, row.public_id, "false_positive"
    )
    await async_session.commit()

    for _ in range(2):
        await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
        await async_session.commit()

    rows = await _all_rows(async_session, repo.id)
    assert len(rows) == 1, "the suppressed kernel must not reappear as a second row"
    assert rows[0].status == "false_positive"
    assert await get_refactoring_suggestions(async_session, repo.id) == []


@pytest.mark.asyncio
async def test_acknowledged_survives_the_next_analysis(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    [row] = await _all_rows(async_session, repo.id)
    await update_refactoring_suggestion_status(async_session, repo.id, row.id, "acknowledged")
    await async_session.commit()

    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    await async_session.refresh(row)
    assert row.status == "acknowledged"


@pytest.mark.asyncio
async def test_a_partial_run_and_a_full_run_reach_the_same_plans(async_session):
    """The two paths differ only in scope, so their output must not differ."""
    repo_full = await insert_repo(async_session, name="full", local_path="/tmp/full")
    repo_partial = await insert_repo(async_session, name="partial", local_path="/tmp/partial")
    first = [_suggestion("a.py", "Foo"), _suggestion("b.py", "Bar")]
    second = [_suggestion("a.py", "Foo"), _suggestion("b.py", "Baz")]

    for repo_id in (repo_full.id, repo_partial.id):
        await save_refactoring_suggestions(async_session, repo_id, first)
    await async_session.commit()

    await save_refactoring_suggestions(async_session, repo_full.id, second)
    await upsert_refactoring_suggestions(
        async_session, repo_partial.id, second, file_paths=["b.py"]
    )
    await async_session.commit()

    def _signature(rows):
        return sorted((r.public_id, r.file_path, r.target_symbol, r.status) for r in rows)

    assert _signature(await _all_rows(async_session, repo_full.id)) == _signature(
        await _all_rows(async_session, repo_partial.id)
    )


@pytest.mark.asyncio
async def test_an_incremental_run_leaves_untouched_files_completely_alone(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(
        async_session, repo.id, [_suggestion("a.py", "Foo"), _suggestion("b.py", "Bar")]
    )
    await async_session.commit()
    before = {row.file_path: (row.id, row.status) for row in await _all_rows(async_session, repo.id)}

    await upsert_refactoring_suggestions(async_session, repo.id, [], file_paths=["b.py"])
    await async_session.commit()
    rows = {row.file_path: row for row in await _all_rows(async_session, repo.id)}

    assert (rows["a.py"].id, rows["a.py"].status) == before["a.py"]
    assert rows["b.py"].status == "resolved"


@pytest.mark.asyncio
async def test_a_row_from_an_older_model_is_resolved_rather_than_reused(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    [row] = await _all_rows(async_session, repo.id)
    row.model_version = 1
    await async_session.commit()

    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()

    rows = sorted(await _all_rows(async_session, repo.id), key=lambda r: r.model_version)
    assert [r.status for r in rows] == ["resolved", "open"]
    assert rows[0].status_reason == "no_longer_detected"


@pytest.mark.asyncio
async def test_a_store_written_before_the_columns_existed_is_restamped(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    [row] = await _all_rows(async_session, repo.id)
    row.public_id = None
    row.model_version = 0
    await async_session.commit()

    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()

    rows = await _all_rows(async_session, repo.id)
    assert len(rows) == 2
    stamped = [r for r in rows if r.public_id]
    assert len(stamped) == 1 and stamped[0].status == "open"


@pytest.mark.asyncio
async def test_a_page_is_fetched_in_sql_not_sliced_in_memory(async_session):
    repo = await insert_repo(async_session)
    plans = [
        _suggestion(f"f{index:03d}.py", f"C{index}", impact_delta=float(index))
        for index in range(50)
    ]
    await save_refactoring_suggestions(async_session, repo.id, plans)
    await async_session.commit()

    page = await get_refactoring_suggestions(async_session, repo.id, limit=10, offset=10)
    everything = await get_refactoring_suggestions(async_session, repo.id)
    assert len(page) == 10
    assert [row.id for row in page] == [row.id for row in everything[10:20]]


@pytest.mark.asyncio
async def test_an_unknown_status_is_refused(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("a.py", "Foo")])
    await async_session.commit()
    [row] = await _all_rows(async_session, repo.id)
    with pytest.raises(ValueError):
        await update_refactoring_suggestion_status(async_session, repo.id, row.id, "wontfix")
    assert await update_refactoring_suggestion_status(
        async_session, repo.id, "no-such-id", "resolved"
    ) is None


@pytest.mark.asyncio
async def test_the_finalizer_reports_how_many_plans_are_open(async_session):
    repo = await insert_repo(async_session)
    plans = [_suggestion("a.py", "Foo"), _suggestion("b.py", "Bar")]
    assert await finalize_refactoring_suggestions(async_session, repo.id, plans) == 2
    await async_session.commit()
    assert await finalize_refactoring_suggestions(async_session, repo.id, plans[:1]) == 1


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _indexes(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        return {row[1] for row in connection.execute(f'PRAGMA index_list("{table}")')}


def test_the_migration_upgrades_and_rolls_back(tmp_path: Path) -> None:
    """Explicit migration for managed stores, both directions.

    Local stores are created by ``init_db`` and never see Alembic, which is why
    the model declaration and this migration have to describe the same shape.
    """
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    db_path = tmp_path / "wiki.db"
    # Built without the ini file on purpose. Passing it makes ``env.py`` call
    # ``fileConfig``, which reconfigures logging for the rest of the session.
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")

    new_columns = {"public_id", "model_version", "status_reason", "status_changed_at"}
    new_indexes = {
        "ix_refactoring_suggestions_repo_status",
        "ix_refactoring_suggestions_repo_status_type",
        "ix_refactoring_suggestions_repo_status_path",
        "uq_refactoring_suggestions_repo_model_public_id",
    }

    command.upgrade(config, "0058")
    assert new_columns <= _columns(db_path, "refactoring_suggestions")
    assert new_indexes <= _indexes(db_path, "refactoring_suggestions")

    command.downgrade(config, "0057")
    assert not new_columns & _columns(db_path, "refactoring_suggestions")
    assert not new_indexes & _indexes(db_path, "refactoring_suggestions")


def _clone(anchor: str, partner: str) -> RefactoringSuggestion:
    """A clone group's identity is its members, not the file it is anchored at."""
    return RefactoringSuggestion(
        refactoring_type="extract_helper",
        file_path=anchor,
        target_symbol=f"{anchor}:10-24",
        line_start=10,
        line_end=24,
        plan={
            "occurrences": [
                {"file": anchor, "line_start": 10, "line_end": 24},
                {"file": partner, "line_start": 55, "line_end": 69},
            ],
            "suggested_site": {"directory": None},
            "duplicated_lines": 15,
            "snippet": "value = compute()\nreturn value\n",
            "snippet_start_line": 10,
            "snippet_truncated": False,
            "suggested_name": None,
        },
        evidence={"occurrence_count": 2, "duplicated_lines": 15, "token_count": 320},
        impact_delta=0.0,
        effort_bucket="S",
        blast_radius={"files": [partner], "file_count": 1},
        confidence="medium",
        source_biomarker="dry_violation",
    )


@pytest.mark.asyncio
async def test_a_scoped_run_reuses_a_clone_row_anchored_outside_its_scope(async_session):
    """The stored row sits at a.py; the run only sees b.py. One plan, one row."""
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_clone("a.py", "b.py")])
    await async_session.commit()
    [before] = await _all_rows(async_session, repo.id)

    await upsert_refactoring_suggestions(
        async_session, repo.id, [_clone("b.py", "a.py")], file_paths=["b.py"]
    )
    await async_session.commit()

    rows = await _all_rows(async_session, repo.id)
    assert len(rows) == 1, "the same clone group must not become a second row"
    assert rows[0].id == before.id
    assert rows[0].file_path == "b.py", "the row follows the anchor the run reported"
    assert rows[0].status == "open"


@pytest.mark.asyncio
async def test_the_structural_writer_leaves_performance_plans_to_their_owner(async_session):
    """Their finalizer rebuilds them from the merged findings; this must not race it."""
    repo = await insert_repo(async_session)
    plan = _suggestion(
        "a.py",
        "a.py::handler",
        refactoring_type="performance_fix",
        plan={"opportunity_id": "perf2_abc", "strategy": "batch"},
        source_biomarker="io_in_loop",
    )
    await save_refactoring_suggestions(async_session, repo.id, [plan])
    await async_session.commit()

    # A later structural run knows nothing about performance plans.
    await save_refactoring_suggestions(async_session, repo.id, [_suggestion("b.py", "Bar")])
    await async_session.commit()

    rows = {row.refactoring_type: row for row in await _all_rows(async_session, repo.id)}
    assert rows["performance_fix"].status == "open"
    assert rows["performance_fix"].status_reason is None
    assert rows["performance_fix"].opportunity_id == "perf2_abc"
