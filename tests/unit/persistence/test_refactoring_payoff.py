"""A plan that stops being detected says what happened to it.

Applied, file deleted or target changed, from the target's stored measures
before the resolving run and the run's own measures after, with the commit.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.analysis.health.complexity.models import FunctionComplexity
from repowise.core.analysis.health.refactoring import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.identity import REFACTORING_MODEL_VERSION
from repowise.core.analysis.health.refactoring.payoff import (
    APPLIED,
    FILE_DELETED,
    SUPERSEDED,
    TARGET_CHANGED,
    UNKNOWN,
    FunctionMeasures,
    classify_payoff,
)
from repowise.core.persistence.crud.analysis import (
    PayoffContext,
    finalize_refactoring_suggestions,
    get_refactoring_suggestion,
    plan_payoff,
    save_refactoring_suggestions,
    upsert_refactoring_suggestions,
    write_function_facts,
)
from repowise.core.persistence.models import FunctionFact, RefactoringPayoff
from repowise.core.persistence.models import RefactoringSuggestion as SuggestionRow
from tests.unit.persistence.helpers import insert_repo


def _plan(path: str = "a.py", target: str = "run", **overrides) -> RefactoringSuggestion:
    base = dict(
        refactoring_type="extract_method",
        file_path=path,
        target_symbol=target,
        line_start=1,
        line_end=60,
        plan={"span": {"start": 10, "end": 30}, "suggested_name": "_load_rows"},
        evidence={"slice_nloc": 18, "ccn_removed": 6},
        impact_delta=3.0,
        effort_bucket="S",
        blast_radius={"scope": "local"},
        confidence="high",
        source_biomarker="complex_method",
    )
    base.update(overrides)
    return RefactoringSuggestion(**base)


def _fact(symbol: str, ccn: int | None, nloc: int | None, params: int | None = 2) -> dict:
    return {"symbol_id": symbol, "ccn": ccn, "nloc": nloc, "params": params}


def _measures(*rows: dict) -> dict[str, FunctionMeasures]:
    return {
        r["symbol_id"]: FunctionMeasures(r["symbol_id"], r["ccn"], r["nloc"], r["params"])
        for r in rows
    }


def _classify(before, after, *, file_live=True, kind="extract_method", redetected=False):
    return classify_payoff(
        redetected=redetected,
        refactoring_type=kind,
        target="run",
        evidence={"slice_nloc": 18, "ccn_removed": 6},
        file_live=file_live,
        before=before,
        after=after,
        suggested_name="_load_rows",
    )


# --- the classifier ---------------------------------------------------------


def test_an_extract_that_moved_the_predicted_complexity_out_is_applied() -> None:
    before = _measures(_fact("a.py::Job::run", 14, 50))
    after = _measures(_fact("a.py::Job::run", 9, 34), _fact("a.py::Job::_load_rows", 6, 18))
    payoff = _classify(before, after)
    assert payoff.outcome == APPLIED
    assert payoff.new_symbol == "a.py::Job::_load_rows"
    assert (payoff.before.ccn, payoff.after.ccn) == (14, 9)


def test_a_deleted_file_is_file_deleted() -> None:
    assert _classify({}, None, file_live=False).outcome == FILE_DELETED


def test_a_renamed_target_is_target_changed() -> None:
    before = _measures(_fact("a.py::run", 14, 50))
    after = _measures(_fact("a.py::execute", 14, 50))
    assert _classify(before, after).outcome == TARGET_CHANGED


def test_a_rewrite_that_left_no_helper_is_target_changed() -> None:
    before = _measures(_fact("a.py::run", 14, 50))
    after = _measures(_fact("a.py::run", 8, 30))
    assert _classify(before, after).outcome == TARGET_CHANGED


def test_too_small_a_drop_is_not_applied() -> None:
    before = _measures(_fact("a.py::run", 14, 50))
    after = _measures(_fact("a.py::run", 13, 49), _fact("a.py::_load_rows", 2, 3))
    assert _classify(before, after).outcome == TARGET_CHANGED


def test_unmeasured_sides_are_unknown_never_a_guess() -> None:
    before = _measures(_fact("a.py::run", None, None))
    after = _measures(_fact("a.py::run", 9, 34), _fact("a.py::_load_rows", 6, 18))
    assert _classify(before, after).outcome == UNKNOWN
    # No after measures at all (the run had no graph to key facts on).
    assert _classify(_measures(_fact("a.py::run", 14, 50)), None).outcome == UNKNOWN
    # Nothing measured moved: the detector changed its mind, not the code.
    same = _measures(_fact("a.py::run", 14, 50))
    assert _classify(same, same).outcome == UNKNOWN


def test_an_edited_target_detected_again_under_a_new_id_is_superseded() -> None:
    before = _measures(_fact("a.py::run", 14, 50))
    after = _measures(_fact("a.py::run", 15, 53))
    assert _classify(before, after, redetected=True).outcome == SUPERSEDED
    # Applied still wins: the plan moved what it said, another extract remains.
    applied = _measures(_fact("a.py::run", 8, 30), _fact("a.py::_load_rows", 6, 18))
    assert _classify(before, applied, redetected=True).outcome == APPLIED
    assert _classify({}, {}, kind="split_file", redetected=True).outcome == SUPERSEDED


def test_the_most_complex_new_function_is_taken_for_the_helper() -> None:
    before = _measures(_fact("a.py::run", 14, 50))
    after = _measures(
        _fact("a.py::run", 2, 5), _fact("a.py::_anchor", 1, 2), _fact("a.py::rank", 9, 30)
    )
    payoff = classify_payoff(
        refactoring_type="extract_method",
        target="run",
        evidence={"ccn_removed": 6},
        file_live=True,
        before=before,
        after=after,
    )
    assert (payoff.outcome, payoff.new_symbol) == (APPLIED, "a.py::rank")


def test_kinds_without_a_function_target_are_unknown_unless_deleted() -> None:
    assert _classify({}, {}, kind="split_file").outcome == UNKNOWN
    assert _classify({}, {}, kind="split_file", file_live=False).outcome == FILE_DELETED


def test_two_functions_sharing_the_name_are_ambiguous() -> None:
    before = _measures(_fact("a.py::A::run", 14, 50), _fact("a.py::B::run", 3, 5))
    after = _measures(_fact("a.py::A::run", 9, 34), _fact("a.py::B::run", 3, 5))
    assert _classify(before, after).outcome == UNKNOWN


def test_stored_metrics_keep_an_unfound_parameter_list_unknown() -> None:
    fc = FunctionComplexity(name="f", start_line=1, end_line=9, ccn=4, max_nesting=2, cognitive=3, nloc=8)
    assert fc.stored_metrics() == {"ccn": 4, "nloc": 8, "params": None, "max_nesting": 2}


# --- the writer ---------------------------------------------------------------


async def _resolve_all(session, repo_id: str, facts: list[dict] | None, *, live: set[str]):
    await save_refactoring_suggestions(
        session,
        repo_id,
        [],
        payoff=PayoffContext(fact_rows=facts, live_paths=frozenset(live), commit="abc123"),
    )


async def _payoffs(session, repo_id: str) -> dict[str, RefactoringPayoff]:
    rows = await session.execute(
        select(RefactoringPayoff).where(RefactoringPayoff.repository_id == repo_id)
    )
    return {row.suggestion_id: row for row in rows.scalars()}


@pytest.mark.asyncio
async def test_resolving_an_applied_plan_stores_before_after_and_the_commit(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan()])
    await write_function_facts(async_session, repo.id, [_fact("a.py::run", 14, 50)])

    after = [_fact("a.py::run", 8, 32), _fact("a.py::_load_rows", 7, 18, params=1)]
    await _resolve_all(async_session, repo.id, after, live={"a.py"})

    (row,) = (await async_session.execute(select(SuggestionRow))).scalars()
    assert (row.status, row.status_reason) == ("resolved", "no_longer_detected")
    stored = (await _payoffs(async_session, repo.id))[row.id]
    assert stored.outcome == APPLIED
    assert stored.resolved_commit == "abc123"
    assert (stored.before_ccn, stored.after_ccn, stored.before_nloc, stored.after_nloc) == (
        14,
        8,
        50,
        32,
    )
    assert stored.new_symbol == "a.py::_load_rows"
    assert stored.stage is None

    detail = await plan_payoff(async_session, row)
    assert detail["outcome"] == APPLIED
    assert detail["realised"] == {"ccn_removed": 6, "nloc_removed": 18}
    assert detail["before"] == {"ccn": 14, "nloc": 50, "params": 2}
    assert "stage" not in detail


@pytest.mark.asyncio
async def test_a_plan_in_a_deleted_file_is_file_deleted(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan("gone.py")])
    await _resolve_all(async_session, repo.id, [], live=set())
    (stored,) = (await _payoffs(async_session, repo.id)).values()
    assert stored.outcome == FILE_DELETED


@pytest.mark.asyncio
async def test_a_rewritten_target_is_target_changed(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan()])
    await write_function_facts(async_session, repo.id, [_fact("a.py::run", 14, 50)])
    await _resolve_all(async_session, repo.id, [_fact("a.py::execute", 5, 20)], live={"a.py"})
    (stored,) = (await _payoffs(async_session, repo.id)).values()
    assert stored.outcome == TARGET_CHANGED
    assert stored.before_ccn == 14 and stored.after_ccn is None


@pytest.mark.asyncio
async def test_a_plan_detected_again_after_an_edit_is_superseded(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan()])
    await write_function_facts(async_session, repo.id, [_fact("a.py::run", 14, 50)])
    # The edit grew the function, so the same plan has a new id.
    moved = _plan(line_end=63)
    await save_refactoring_suggestions(
        async_session,
        repo.id,
        [moved],
        payoff=PayoffContext(
            fact_rows=[_fact("a.py::run", 15, 53)], live_paths=frozenset({"a.py"})
        ),
    )
    (stored,) = (await _payoffs(async_session, repo.id)).values()
    assert stored.outcome == SUPERSEDED


@pytest.mark.asyncio
async def test_a_reopened_plan_loses_its_payoff_and_open_plans_show_none(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan()])
    await _resolve_all(async_session, repo.id, [], live={"a.py"})
    assert len(await _payoffs(async_session, repo.id)) == 1

    await save_refactoring_suggestions(
        async_session,
        repo.id,
        [_plan()],
        payoff=PayoffContext(fact_rows=[], live_paths=frozenset({"a.py"})),
    )
    (row,) = (await async_session.execute(select(SuggestionRow))).scalars()
    assert row.status == "open"
    assert await _payoffs(async_session, repo.id) == {}
    assert await plan_payoff(async_session, row) is None


@pytest.mark.asyncio
async def test_an_older_models_row_resolves_without_a_payoff(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan()])
    (row,) = (await async_session.execute(select(SuggestionRow))).scalars()
    row.model_version = REFACTORING_MODEL_VERSION - 1
    await async_session.flush()
    await _resolve_all(async_session, repo.id, [], live=set())
    assert row.status == "resolved"
    assert await _payoffs(async_session, repo.id) == {}


@pytest.mark.asyncio
async def test_an_incremental_update_keeps_payoffs_outside_its_scope(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan("a.py"), _plan("b.py")])
    ctx = PayoffContext(fact_rows=[], live_paths=frozenset({"a.py"}), commit="c1")
    await upsert_refactoring_suggestions(
        async_session, repo.id, [_plan("b.py")], file_paths=["a.py"], payoff=ctx
    )
    first = await _payoffs(async_session, repo.id)
    assert len(first) == 1

    ctx = PayoffContext(fact_rows=[], live_paths=frozenset({"b.py"}), commit="c2")
    await upsert_refactoring_suggestions(async_session, repo.id, [], file_paths=["b.py"], payoff=ctx)
    second = await _payoffs(async_session, repo.id)
    assert len(second) == 2
    a_id = next(iter(first))
    assert second[a_id].resolved_commit == "c1"


@pytest.mark.asyncio
async def test_without_a_payoff_context_the_writer_records_nothing(async_session):
    repo = await insert_repo(async_session)
    await save_refactoring_suggestions(async_session, repo.id, [_plan()])
    await finalize_refactoring_suggestions(async_session, repo.id, [])
    row = await get_refactoring_suggestion(
        async_session, repo.id, (await async_session.execute(select(SuggestionRow.id))).scalar_one()
    )
    assert row.status == "resolved"
    assert await _payoffs(async_session, repo.id) == {}


@pytest.mark.asyncio
async def test_fact_rows_store_the_size_measures(async_session):
    repo = await insert_repo(async_session)
    await write_function_facts(async_session, repo.id, [_fact("a.py::run", 14, 50, None)])
    (fact,) = (await async_session.execute(select(FunctionFact))).scalars()
    assert (fact.ccn, fact.nloc, fact.params, fact.max_nesting) == (14, 50, None, None)


# --- the migration ------------------------------------------------------------


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


def _alembic_config(db_path: Path):
    from alembic.config import Config

    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return config


def test_the_migration_adds_and_drops_the_columns_and_table(tmp_path: Path) -> None:
    from alembic import command

    db_path = tmp_path / "wiki.db"
    config = _alembic_config(db_path)
    command.upgrade(config, "0105")
    assert {"ccn", "nloc", "params", "max_nesting"} <= _columns(db_path, "function_facts")
    assert "outcome" in _columns(db_path, "refactoring_payoffs")
    command.downgrade(config, "0104")
    assert "ccn" not in _columns(db_path, "function_facts")
    assert _columns(db_path, "refactoring_payoffs") == set()


def test_the_migration_and_the_model_agree(tmp_path: Path) -> None:
    import asyncio

    from alembic import command

    from repowise.core.persistence.database import create_engine, init_db

    migrated = tmp_path / "migrated.db"
    command.upgrade(_alembic_config(migrated), "head")
    declared = tmp_path / "declared.db"

    async def _build() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{declared}")
        await init_db(engine)
        await engine.dispose()

    asyncio.run(_build())
    for table in ("function_facts", "refactoring_payoffs"):
        assert _columns(migrated, table) == _columns(declared, table)
