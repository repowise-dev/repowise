"""A spawned coroutine is not its caller's work, and a group's facts come from
its live members: the mechanical misfacts that filled the perf default queue."""

from __future__ import annotations

import textwrap
from pathlib import Path

from repowise.core.analysis.execution_graph import ExecutionGraphIndex
from repowise.core.analysis.execution_roles import ExecutionRoles
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.queue.eligibility import (
    perf_queue_counts,
    perf_queue_verdict,
    queue_proof,
)
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from tests.unit.ingestion.test_graph_rehydrate import _serialize

_ROUTES = """
import asyncio
from fastapi import APIRouter

router = APIRouter()
_tasks = set()


@router.post("/sync")
async def sync(repo_id: str):
    task = asyncio.create_task(execute_job(repo_id), name="job")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    for name in ("a", "b"):
        asyncio.create_task(watch(name))
    return {"ok": True}


@router.post("/now")
async def run_now(repo_id: str):
    await direct(repo_id)


async def execute_job(repo_id):
    await persist(repo_id)


async def persist(repo_id):
    return repo_id


async def watch(name):
    return name


async def direct(repo_id):
    await shared(repo_id)
    asyncio.create_task(shared(repo_id))


async def shared(repo_id):
    return repo_id


async def awaited_later(x):
    t = asyncio.create_task(step(x))
    await t


async def awaited_inline(x):
    await asyncio.create_task(step(x))


async def gathered(xs):
    tasks = [asyncio.create_task(step(x)) for x in xs]
    await asyncio.gather(*tasks)


async def grouped(xs):
    async with asyncio.TaskGroup() as tg:
        for x in xs:
            tg.create_task(step(x))


async def tg_param(tg, x):
    tg.create_task(step(x))


async def iterated(xs):
    tasks = []
    for x in xs:
        tasks.append(asyncio.create_task(step(x)))
    for t in tasks:
        await t


async def returned(xs):
    tasks = [asyncio.create_task(step(x)) for x in xs]
    return tasks


class Runner:
    async def joined(self, xs):
        tasks = [asyncio.create_task(step(x)) for x in xs]
        await self._join(tasks)


async def step(x):
    return x
"""


def _parse(tmp_path: Path):
    (tmp_path / "routes.py").write_text(textwrap.dedent(_ROUTES), encoding="utf-8")
    parser = ASTParser()
    return [
        parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        for fi in FileTraverser(tmp_path).traverse()
    ]


def _graph(tmp_path: Path):
    parsed = _parse(tmp_path)
    builder = GraphBuilder(tmp_path)
    for pf in parsed:
        builder.add_file(pf)
    builder.build()
    return parsed, builder


def _spawned(parsed) -> dict[tuple[str, str], bool]:
    return {
        (c.caller_symbol_id.rsplit("::", 1)[-1], c.target_name): c.spawned
        for pf in parsed
        for c in pf.calls
        if c.caller_symbol_id
    }


def test_only_a_task_nobody_waits_for_is_spawned(tmp_path: Path) -> None:
    calls = _spawned(_parse(tmp_path))
    # Dropped, or only kept alive in a set: fire-and-forget.
    assert calls[("sync", "execute_job")] is True
    assert calls[("sync", "watch")] is True
    # Awaited, gathered or owned by a TaskGroup: the caller's own work.
    assert calls[("awaited_later", "step")] is False
    assert calls[("awaited_inline", "step")] is False
    assert calls[("gathered", "step")] is False
    assert calls[("grouped", "step")] is False
    # A receiver not proven to be asyncio may be a group someone waits for.
    assert calls[("tg_param", "step")] is False
    # A container read in any way but adding and removing: its tasks are waited.
    assert calls[("iterated", "step")] is False
    assert calls[("returned", "step")] is False
    assert calls[("joined", "step")] is False


def test_a_spawned_call_site_never_resolves_as_a_call_its_caller_makes(tmp_path: Path) -> None:
    _parsed, builder = _graph(tmp_path)
    graph = builder.graph()
    edge = graph["routes.py::sync"]["routes.py::watch"]
    assert edge["spawn_lines"] == edge["call_lines"]
    index = ExecutionGraphIndex(graph)
    line = edge["call_lines"][0]
    assert index.resolve_call_targets("routes.py::sync", line, "watch") == ((), "call-site")
    assert "routes.py::watch" in index.spawn_only["routes.py::sync"]
    # Still an edge: dead code and test reachability keep seeing the callee.
    assert "routes.py::watch" in index.forward["routes.py::sync"]
    # Called and spawned from one function: the awaited site still resolves.
    assert "routes.py::shared" not in index.spawn_only.get("routes.py::direct", ())


def test_a_request_that_spawns_a_job_does_not_make_the_job_a_request(tmp_path: Path) -> None:
    _parsed, builder = _graph(tmp_path)
    graph = builder.graph()
    roles = ExecutionRoles.build(graph, ExecutionGraphIndex(graph))
    assert roles.role_of("routes.py::sync", "routes.py") == "request"
    assert roles.role_of("routes.py::execute_job", "routes.py") == "scheduled_job"
    assert roles.role_of("routes.py::persist", "routes.py") == "scheduled_job"
    # Spawned somewhere, but also awaited on a request's path: still the request's.
    assert roles.role_of("routes.py::shared", "routes.py") == "request"


def test_a_rehydrated_graph_gives_the_same_spawns_and_roles(tmp_path: Path) -> None:
    parsed, fresh = _graph(tmp_path)
    nodes, edges = _serialize(fresh)
    rehydrated = GraphBuilder.from_persisted(nodes, edges, fresh.file_metrics_snapshot())
    rehydrated.restore_parse_only_attrs(parsed)
    fresh_index = ExecutionGraphIndex(fresh.graph())
    re_index = ExecutionGraphIndex(rehydrated.graph())
    assert re_index.spawn_only == fresh_index.spawn_only != {}
    fresh_roles = ExecutionRoles.build(fresh.graph(), fresh_index).reached
    assert ExecutionRoles.build(rehydrated.graph(), re_index).reached == fresh_roles


def _finding(line: int, role: str | None = None, **details: object) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path="svc/load.py",
        function_name="load_all",
        line_start=line,
        line_end=line,
        details={
            "boundary_kind": "db",
            "cross_function": False,
            "path": [],
            **({"execution_role": role} if role else {}),
            **details,
        },
        health_impact=0.0,
        dimension="performance",
    )


def test_a_gated_member_neither_leads_nor_lends_its_role() -> None:
    (item,) = build_performance_opportunities(
        [
            _finding(10, "request", gated_off=True, loop_magnitude="grows_with_data"),
            _finding(30, "unknown"),
        ]
    )
    assert item.actionability_reason != "gated_off"
    assert item.evidence[0]["line_start"] == 30
    assert item.facets["execution_role"] == "unknown"
    assert item.facets["loop_magnitude"] == "unknown"
    assert item.observations_total == 2 and item.affected_call_sites_total == 1


def test_a_chunked_member_does_not_lead_a_live_one() -> None:
    (item,) = build_performance_opportunities(
        [_finding(10, "request", chunked_iteration=True), _finding(30, "request")]
    )
    assert item.evidence[0]["line_start"] == 30
    # Every member chunked: the loop is already the batch.
    (chunked,) = build_performance_opportunities([_finding(10, "request", chunked_iteration=True)])
    assert chunked.actionability_reason == "loop_already_chunked"


def test_growth_is_read_off_the_members_the_role_comes_from() -> None:
    """A request loop nobody measured does not borrow a startup loop's growth."""
    (item,) = build_performance_opportunities(
        [_finding(10, "startup", loop_magnitude="grows_with_data"), _finding(30, "request")]
    )
    assert item.facets["execution_role"] == "request"
    assert item.facets["loop_magnitude"] == "unknown"
    assert perf_queue_verdict(item).reason == "unmeasured_cost"
    assert queue_proof(item) == "unproven"
    (grows,) = build_performance_opportunities(
        [_finding(10, "request", loop_magnitude="grows_with_data"), _finding(30, "startup")]
    )
    assert grows.facets["loop_magnitude"] == "grows_with_data"


def test_a_bounded_loop_has_nothing_to_change() -> None:
    (item,) = build_performance_opportunities(
        [_finding(10, "request", loop_magnitude="bounded"), _finding(12, "request", loop_magnitude="bounded")]
    )
    assert (item.actionability_state, item.actionability_reason) == ("expected", "bounded_loop")
    assert perf_queue_verdict(item).reason == "expected"
    # The queue summary counts it under ``expected``, the reason the store keeps.
    assert perf_queue_counts([item])["excluded"]["expected"] == 1
    (mixed,) = build_performance_opportunities(
        [_finding(10, "request", loop_magnitude="bounded"), _finding(12, "request")]
    )
    assert mixed.actionability_state != "expected"
