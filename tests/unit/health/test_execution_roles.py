"""Execution roles: seeds per framework, one walk per role, keyed on the loop owner."""

from __future__ import annotations

import networkx as nx
import pytest

from repowise.core.analysis.execution_graph import ExecutionGraphIndex
from repowise.core.analysis.execution_roles import (
    EXECUTION_ROLES,
    ExecutionRoles,
    hottest_role,
    seed_roles,
)
from repowise.core.analysis.health import HealthAnalyzer
from repowise.core.analysis.health.models import HealthFindingData
from repowise.core.analysis.health.perf.opportunity_rank import ROLE_POINTS
from repowise.core.analysis.health.queue.eligibility import perf_queue_verdict, queue_proof


def _graph(*symbols: tuple[str, str, list[str]], start: int = 10) -> nx.DiGraph:
    """Symbols as ``(id, language, decorators)``, each defined by its file."""
    graph = nx.DiGraph()
    for offset, (node, language, decorators) in enumerate(symbols):
        path, _, name = node.partition("::")
        graph.add_node(path, node_type="file")
        graph.add_node(
            node,
            node_type="symbol",
            kind="function",
            name=name.rsplit("::", 1)[-1],
            file_path=path,
            language=language,
            decorators=decorators,
            start_line=start + offset * 20,
            end_line=start + offset * 20 + 15,
        )
        graph.add_edge(path, node, edge_type="defines")
    return graph


def _seed_of(graph: nx.DiGraph, node: str) -> str | None:
    return next((role for role, nodes in seed_roles(graph).items() if node in nodes), None)


@pytest.mark.parametrize(
    ("node", "language", "decorators", "role"),
    [
        ("api/users.py::list_users", "python", ['@router.get("/users")'], "request"),
        ("api/views.py::index", "python", ['@bp.route("/", methods=["GET"])'], "request"),
        ("api/ws.py::stream", "python", ['@app.websocket("/ws")'], "request"),
        ("mcp/tools.py::get_health", "python", ["@mcp.tool()"], "request"),
        ("api/app.py::boot", "python", ['@app.on_event("startup")'], "startup"),
        ("worker/tasks.py::reindex", "python", ["@shared_task"], "scheduled_job"),
        ("worker/tasks.py::rebuild", "python", ["@celery_app.task(bind=True)"], "scheduled_job"),
        ("cli/main.py::init", "python", ["@click.command()", '@click.option("--x")'], "cli"),
        ("cli/main.py::run", "python", ["@app.command()"], "cli"),
        ("src/api.ts::list", "typescript", ['@Get(":id")'], "request"),
        ("app/api/users/route.ts::GET", "typescript", [], "request"),
        ("src/inbound.ts::handleClickClackInbound", "typescript", [], "event_consumer"),
        ("gateway/sms.py::_handle_webhook", "python", [], "event_consumer"),
        ("gateway/run.py::_async_delegation_watcher", "python", [], "scheduled_job"),
        ("src/server.ts::registerBrowserPermissionRoutes", "typescript", [], "request"),
        ("app/main.py::lifespan", "python", [], "startup"),
        ("pkg/util.py::helper", "python", ["@functools.cache"], None),
        # Other languages are never seeded: they read ``unknown``.
        ("src/Api.java::list", "java", ['@Get("/x")'], None),
    ],
)
def test_declaration_seeds(node, language, decorators, role) -> None:
    assert _seed_of(_graph((node, language, decorators)), node) == role


def test_django_views_named_by_a_urlconf_are_request_seeds() -> None:
    graph = _graph(("shop/views.py::detail", "python", []))
    graph.add_node("shop/urls.py", node_type="file")
    graph.add_edge("shop/urls.py", "shop/views.py", edge_type="framework")
    assert _seed_of(graph, "shop/views.py::detail") == "request"


def test_express_bound_handlers_seed_only_where_the_file_uses_express() -> None:
    graph = _graph(
        ("src/routes.ts::__module__", "typescript", []),
        ("src/routes.ts::getUser", "typescript", []),
        ("src/nav.ts::__module__", "typescript", []),
        ("src/nav.ts::guard", "typescript", []),
    )
    graph.add_edge("src/routes.ts::__module__", "src/routes.ts::getUser", edge_type="framework_binds")
    graph.add_edge("src/nav.ts::__module__", "src/nav.ts::guard", edge_type="framework_binds")
    graph.add_edge("src/routes.ts", "external:express", edge_type="imports")
    assert _seed_of(graph, "src/routes.ts::getUser") == "request"
    assert _seed_of(graph, "src/nav.ts::guard") is None


def test_lambda_config_binds_seed_event_consumers() -> None:
    graph = _graph(("src/handler.py::main", "python", []))
    graph.add_node("serverless.yml::__module__", node_type="symbol", kind="module")
    graph.add_edge("serverless.yml::__module__", "src/handler.py::main", edge_type="framework_binds")
    assert _seed_of(graph, "src/handler.py::main") == "event_consumer"


def test_scheduler_imports_and_argparse_commands_seed_their_files() -> None:
    graph = _graph(
        ("server/scheduler.py::setup_scheduler", "python", []),
        ("tool/cli.py::main", "python", []),
        ("tool/cli.py::cmd_sync", "python", []),
    )
    graph.add_edge("server/scheduler.py", "external:apscheduler.schedulers.asyncio", edge_type="imports")
    graph.add_edge("tool/cli.py", "external:argparse", edge_type="imports")
    graph.add_edge("tool/cli.py::main", "tool/cli.py::cmd_sync", edge_type="references")
    seeds = seed_roles(graph)
    assert "server/scheduler.py::setup_scheduler" in seeds["scheduled_job"]
    assert {"tool/cli.py::main", "tool/cli.py::cmd_sync"} <= seeds["cli"]


def test_aiohttp_handlers_seed_requests() -> None:
    graph = _graph(("gateway/api.py::_handle_get_job", "python", []), ("gateway/api.py::helper", "python", []))
    graph.add_edge("gateway/api.py", "external:aiohttp", edge_type="imports")
    seeds = seed_roles(graph)
    assert "gateway/api.py::_handle_get_job" in seeds["request"]
    assert "gateway/api.py::helper" not in seeds["request"]


def _call(graph: nx.DiGraph, source: str, target: str) -> None:
    graph.add_edge(source, target, edge_type="calls", call_lines=[1])


def _reach_graph() -> nx.DiGraph:
    graph = _graph(
        ("api/routes.py::create", "python", ['@router.post("/items")']),
        ("cli/main.py::sync", "python", ["@click.command()"]),
        ("app/main.py::lifespan", "python", []),
        ("svc/items.py::save_all", "python", []),
        ("svc/items.py::warm_cache", "python", []),
        ("svc/items.py::orphan", "python", []),
        ("tests/test_items.py::test_save", "python", []),
    )
    _call(graph, "api/routes.py::create", "svc/items.py::save_all")
    _call(graph, "cli/main.py::sync", "svc/items.py::save_all")
    _call(graph, "app/main.py::lifespan", "svc/items.py::warm_cache")
    _call(graph, "tests/test_items.py::test_save", "svc/items.py::orphan")
    return graph


def test_the_hottest_reaching_role_wins_and_paths_decide_test_and_tooling() -> None:
    graph = _reach_graph()
    roles = ExecutionRoles.build(graph, ExecutionGraphIndex(graph))
    assert roles.role_of("svc/items.py::save_all", "svc/items.py") == "request"
    assert roles.role_of("svc/items.py::warm_cache", "svc/items.py") == "startup"
    # Reached only by a test: no evidence of how it runs in production.
    assert roles.role_of("svc/items.py::orphan", "svc/items.py") == "unknown"
    assert roles.role_of("tests/test_items.py::test_save", "tests/test_items.py") == "test"
    assert roles.role_of("scripts/release.py::main", "scripts/release.py") == "tooling"


def _finding(file_path: str, line: int, details: dict | None = None) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity="low",
        file_path=file_path,
        function_name=None,
        line_start=line,
        line_end=line,
        details=details or {},
        health_impact=0.0,
        dimension="performance",
    )


def test_the_role_is_keyed_on_the_loop_owner_even_without_a_path() -> None:
    graph = _reach_graph()
    analyzer = HealthAnalyzer(graph)
    # save_all spans 70-85 and warm_cache 90-105 (see ``_graph``).
    intra = _finding("svc/items.py", 95)
    cross = _finding("svc/items.py", 72, {"path": ["svc/items.py::save_all", "db.py::write"]})
    other = _finding("svc/items.py", 95)
    other.dimension = "defect"
    analyzer._mark_perf_entry_reachability([intra, cross, other])
    assert intra.details["execution_role"] == "startup"
    assert cross.details["execution_role"] == "request"
    assert "execution_role" not in other.details


def test_group_role_prefers_hot_and_never_reads_unknown_as_cold() -> None:
    assert hottest_role(["cli", "request"]) == "request"
    assert hottest_role(["cli", None]) == "unknown"
    assert hottest_role(["startup", "test"]) == "startup"
    assert hottest_role([]) == "unknown"
    assert set(ROLE_POINTS) == set(EXECUTION_ROLES)


def _opportunity(role: str, magnitude: str = "grows_with_data") -> dict:
    return {
        "execution_context": "production",
        "actionability_state": "advisory",
        "biomarker_type": "io_in_loop",
        "facets": {"loop_magnitude": magnitude, "execution_role": role},
    }


@pytest.mark.parametrize(
    ("role", "magnitude", "reason", "proof"),
    [
        ("request", "grows_with_data", None, "proven"),
        ("event_consumer", "grows_with_data", None, "proven"),
        ("unknown", "grows_with_data", None, "proven"),
        ("scheduled_job", "grows_with_data", None, "proven"),
        ("scheduled_job", "bounded", "background_unproven", "unproven"),
        ("startup", "grows_with_data", "cold_role", "proven"),
        ("cli", "grows_with_data", "cold_role", "proven"),
        # Unmeasured cost is checked first, so the count stays where it was.
        ("cli", "unknown", "unmeasured_cost", "unproven"),
    ],
)
def test_default_queue_eligibility_by_role(role, magnitude, reason, proof) -> None:
    item = _opportunity(role, magnitude)
    assert perf_queue_verdict(item).reason == reason
    assert queue_proof(item) == proof


def test_a_walk_stops_where_another_role_begins() -> None:
    """A request that launches a job does not run the job's loops."""
    graph = _graph(
        ("api/repos.py::sync", "python", ['@router.post("/sync")']),
        ("server/jobs.py::execute_job", "python", []),
        ("core/pipeline.py::persist_all", "python", []),
    )
    _call(graph, "api/repos.py::sync", "server/jobs.py::execute_job")
    _call(graph, "server/jobs.py::execute_job", "core/pipeline.py::persist_all")
    roles = ExecutionRoles.build(graph, ExecutionGraphIndex(graph))
    assert roles.role_of("server/jobs.py::execute_job", "server/jobs.py") == "scheduled_job"
    assert roles.role_of("core/pipeline.py::persist_all", "core/pipeline.py") == "scheduled_job"
