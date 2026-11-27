"""``crud.measured_in_change``: the measured functions a change edits, function level only."""

from __future__ import annotations

from repowise.core.persistence.crud import measured_in_change, replace_workload_hotness
from repowise.core.persistence.models import EvidenceBenchmarkResult, GraphEdge, GraphNode
from tests.unit.persistence.helpers import insert_repo, make_wiki_symbol
from tests.unit.persistence.test_measured_costs import APP, _hot, _run, _seed_code

BENCH = "app_test.go::BenchmarkServe"
BENCH_RERUN = "go test -run '^$' -bench '^BenchmarkServe$' -count 5 ."


async def _benchmark_calls_serve(session, repo_id: str, *, stored: bool) -> None:
    session.add_all(
        [
            GraphNode(repository_id=repo_id, node_id=BENCH, node_type="symbol"),
            GraphEdge(
                repository_id=repo_id,
                source_node_id=BENCH,
                target_node_id=f"{APP}::serve",
                edge_type="calls",
            ),
        ]
    )
    if stored:
        session.add(_run(repo_id, "b1", cls="benchmark"))
        session.add(
            EvidenceBenchmarkResult(
                run_id="b1",
                benchmark_key="BenchmarkServe",
                repository_id=repo_id,
                symbol_id=BENCH,
                median=1.0,
            )
        )
    await session.flush()


async def test_no_evidence_means_nothing_measured(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    assert await measured_in_change(async_session, repo.id, {APP: {3}}) == []


async def test_a_changed_function_a_leading_workload_ranks_gets_its_rerun(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve", rank=2)])

    [found] = await measured_in_change(async_session, repo.id, {APP: {3}})

    assert found.as_dict() == {
        "fn": f"{APP}::serve",
        "measured": 'rank 2 in entry_point "w-r1"',
        "verify": "repowise perf measure --save before -- w-r1 before the change, "
        "then --against before after it",
    }
    assert found.verify_basis == "workload"


async def test_an_edit_inside_a_nested_function_is_not_an_edit_of_the_hot_one(
    async_session,
) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve")])

    # Line 6 sits in ``inner`` (5-8), the narrower function inside ``serve``.
    assert await measured_in_change(async_session, repo.id, {APP: {6}}) == []


async def test_a_hot_file_is_not_a_hot_function(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("load")])

    # ``helper`` (50-60) shares the file with the ranked ``load``.
    assert await measured_in_change(async_session, repo.id, {APP: {55}}) == []


async def test_unit_test_runs_never_make_a_function_measured(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r3", cls="unit_test"))
    await async_session.flush()
    await replace_workload_hotness(
        async_session, repo.id, [_hot("serve", run="r3", tier="lead_candidate")]
    )
    assert await measured_in_change(async_session, repo.id, {APP: {3}}) == []


async def test_a_benchmark_with_results_measures_what_it_calls(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _benchmark_calls_serve(async_session, repo.id, stored=True)

    [found] = await measured_in_change(async_session, repo.id, {APP: {3}})

    assert found.as_dict() == {
        "fn": f"{APP}::serve",
        "measured": "benchmark",
        "verify": BENCH_RERUN,
    }


async def test_the_benchmark_rerun_beats_the_workload_rerun(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await _benchmark_calls_serve(async_session, repo.id, stored=True)
    await replace_workload_hotness(async_session, repo.id, [_hot("serve")])

    [found] = await measured_in_change(async_session, repo.id, {APP: {3}})

    assert (found.as_dict()["measured"], found.verify) == (
        'rank 1 in entry_point "w-r1"',
        BENCH_RERUN,
    )


async def test_a_benchmark_nobody_ran_measures_nothing(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    # Other evidence is stored, so the read gets past its no-evidence check.
    async_session.add(_run(repo.id, "r1"))
    await _benchmark_calls_serve(async_session, repo.id, stored=False)
    await replace_workload_hotness(async_session, repo.id, [_hot("load")])

    assert await measured_in_change(async_session, repo.id, {APP: {3}}) == []


async def test_code_outside_production_is_never_measured(async_session) -> None:
    repo = await insert_repo(async_session)
    test_file = "tests/test_app.py"
    async_session.add(make_wiki_symbol(repo.id, test_file, "helper", start_line=1, end_line=9))
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(
        async_session, repo.id, [_hot("helper", symbol_id=f"{test_file}::helper")]
    )
    assert await measured_in_change(async_session, repo.id, {test_file: {3}}) == []


async def test_an_outer_function_edited_in_its_own_body_stays_beside_a_nested_one(
    async_session,
) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve"), _hot("inner", rank=2)])

    # Line 3 is ``serve``'s own body; line 6 is inside ``inner`` (5-8).
    found = await measured_in_change(async_session, repo.id, {APP: {3, 6}})

    assert [m.symbol_id for m in found] == [f"{APP}::serve", f"{APP}::inner"]
