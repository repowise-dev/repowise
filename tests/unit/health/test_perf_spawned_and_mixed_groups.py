"""A spawned coroutine is not its caller's work, and a group's facts come from
its live members: the mechanical misfacts that filled the perf default queue."""

from __future__ import annotations

import textwrap
from pathlib import Path

from repowise.core.analysis.execution_graph import ExecutionGraphIndex
from repowise.core.analysis.execution_roles import ExecutionRoles
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.queue.eligibility import perf_queue_verdict, queue_proof
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_ROUTES = """
import asyncio
from fastapi import APIRouter

router = APIRouter()


@router.post("/sync")
async def sync(repo_id: str):
    asyncio.create_task(execute_job(repo_id))
    for name in ("a", "b"):
        asyncio.create_task(watch(name))
    return {"ok": True}


async def execute_job(repo_id):
    await persist(repo_id)


async def persist(repo_id):
    return repo_id


async def watch(name):
    return name


async def direct(repo_id):
    await execute_job(repo_id)
"""


def _graph(tmp_path: Path):
    (tmp_path / "routes.py").write_text(textwrap.dedent(_ROUTES), encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(tmp_path)
    parsed = [
        parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        for fi in FileTraverser(tmp_path).traverse()
    ]
    for pf in parsed:
        builder.add_file(pf)
    builder.build()
    return parsed, builder.graph()


def test_a_coroutine_handed_to_create_task_is_a_spawned_call(tmp_path: Path) -> None:
    parsed, graph = _graph(tmp_path)
    calls = {(c.target_name, c.spawned) for pf in parsed for c in pf.calls}
    assert ("execute_job", True) in calls and ("watch", True) in calls
    # Awaiting it runs it in the caller.
    assert ("execute_job", False) in calls
    edge = graph["routes.py::sync"]["routes.py::execute_job"]
    assert edge["spawn_lines"] == edge["call_lines"]
    assert "spawn_lines" not in graph["routes.py::direct"]["routes.py::execute_job"]


def test_a_spawned_call_site_never_resolves_as_a_call_its_caller_makes(tmp_path: Path) -> None:
    _parsed, graph = _graph(tmp_path)
    index = ExecutionGraphIndex(graph)
    line = graph["routes.py::sync"]["routes.py::watch"]["call_lines"][0]
    assert index.resolve_call_targets("routes.py::sync", line, "watch") == ((), "call-site")
    assert "routes.py::watch" in index.spawn_only["routes.py::sync"]
    # Still an edge: dead code and test reachability keep seeing the callee.
    assert "routes.py::watch" in index.forward["routes.py::sync"]


def test_a_request_that_spawns_a_job_does_not_make_the_job_a_request(tmp_path: Path) -> None:
    _parsed, graph = _graph(tmp_path)
    roles = ExecutionRoles.build(graph, ExecutionGraphIndex(graph))
    assert roles.role_of("routes.py::sync", "routes.py") == "request"
    assert roles.role_of("routes.py::execute_job", "routes.py") == "scheduled_job"
    assert roles.role_of("routes.py::persist", "routes.py") == "scheduled_job"


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
    (mixed,) = build_performance_opportunities(
        [_finding(10, "request", loop_magnitude="bounded"), _finding(12, "request")]
    )
    assert mixed.actionability_state != "expected"
