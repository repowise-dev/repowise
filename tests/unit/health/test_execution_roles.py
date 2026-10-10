"""Execution roles: seeds per framework, hot walks, cold settling, keyed on the loop owner."""

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
from repowise.core.analysis.health.queue.eligibility import (
    DEFAULT_QUEUE_PROOFS,
    perf_queue_verdict,
    queue_proof,
)


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
    by_role = seed_roles(graph).by_role
    return next((role for role, nodes in by_role.items() if node in nodes), None)


def _imports(graph: nx.DiGraph, path: str, module: str) -> None:
    graph.add_edge(path, f"external:{module}", edge_type="imports")


@pytest.mark.parametrize(
    ("node", "language", "decorators", "role"),
    [
        ("api/users.py::list_users", "python", ['@router.get("/users")'], "request"),
        ("api/views.py::index", "python", ['@bp.route("/", methods=["GET"])'], "request"),
        ("api/ws.py::stream", "python", ['@app.websocket("/ws")'], "request"),
        ("mcp/tools.py::get_health", "python", ["@mcp.tool()"], "request"),
        ("rpc/server.py::list_sessions", "python", ['@method("session.list")'], "request"),
        ("api/app.py::boot", "python", ['@app.on_event("startup")'], "startup"),
        ("worker/tasks.py::reindex", "python", ["@shared_task"], "scheduled_job"),
        ("worker/tasks.py::rebuild", "python", ["@celery_app.task(bind=True)"], "scheduled_job"),
        ("cli/main.py::init", "python", ["@click.command()", '@click.option("--x")'], "cli"),
        ("cli/main.py::run", "python", ["@app.command()"], "cli"),
        ("src/api.ts::list", "typescript", ['@Get(":id")'], "request"),
        ("app/api/users/route.ts::GET", "typescript", [], "request"),
        ("app/main.py::lifespan", "python", [], "startup"),
        ("pkg/util.py::helper", "python", ["@functools.cache"], None),
        # Other languages are never seeded: they read ``unknown``.
        ("src/Api.java::list", "java", ['@Get("/x")'], None),
    ],
)
def test_declaration_seeds(node, language, decorators, role) -> None:
    assert _seed_of(_graph((node, language, decorators)), node) == role


@pytest.mark.parametrize(
    ("node", "language", "role"),
    [
        # A name is kept only where the file corroborates it.
        ("extensions/chat/src/inbound.ts::handleClickClackInbound", "typescript", "event_consumer"),
        ("gateway/sms.py::_handle_webhook", "python", "event_consumer"),
        ("gateway/run.py::_async_delegation_watcher", "python", "scheduled_job"),
        ("server/job_executor.py::execute_job", "python", "scheduled_job"),
        ("tui_gateway/server.py::_notification_poller_loop", "python", "scheduled_job"),
        ("src/routes/permissions.ts::registerBrowserPermissionRoutes", "typescript", "request"),
        ("lib/util.py::handle_webhook", "python", None),
        ("lib/util.py::poll_once", "python", None),
        ("gateway/hooks.py::handle_webhook_registration", "python", None),
        # Whole words: none of these is a poller.
        ("worker/env.py::pollutant_level", "python", None),
        ("worker/env.py::polling_config", "python", None),
        ("worker/jobs.py::process_jobless", "python", None),
    ],
)
def test_name_seeds_need_a_corroborating_place(node, language, role) -> None:
    assert _seed_of(_graph((node, language, [])), node) == role


def test_a_registration_corroborates_a_name_anywhere() -> None:
    graph = _graph(("lib/util.py::poll_once", "python", []), ("lib/boot.py::start", "python", []))
    graph.add_edge("lib/boot.py::start", "lib/util.py::poll_once", edge_type="references")
    assert _seed_of(graph, "lib/util.py::poll_once") == "scheduled_job"


def test_a_scheduler_builder_owns_its_registered_closures() -> None:
    graph = _graph(("server/scheduler.py::setup_scheduler", "python", []), ("server/util.py::setup_scheduler", "python", []))
    _imports(graph, "server/scheduler.py", "apscheduler.schedulers.asyncio")
    assert _seed_of(graph, "server/scheduler.py::setup_scheduler") == "scheduled_job"
    assert _seed_of(graph, "server/util.py::setup_scheduler") is None


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
    _imports(graph, "src/routes.ts", "express")
    assert _seed_of(graph, "src/routes.ts::getUser") == "request"
    assert _seed_of(graph, "src/nav.ts::guard") is None


def test_lambda_config_binds_seed_event_consumers() -> None:
    graph = _graph(("src/handler.py::main", "python", []))
    graph.add_node("serverless.yml::__module__", node_type="symbol", kind="module")
    graph.add_edge("serverless.yml::__module__", "src/handler.py::main", edge_type="framework_binds")
    assert _seed_of(graph, "src/handler.py::main") == "event_consumer"


def test_a_scheduler_import_seeds_only_registered_functions() -> None:
    graph = _graph(
        ("server/scheduler.py::setup_scheduler", "python", []),
        ("server/scheduler.py::sync_repos", "python", []),
        ("tool/cli.py::main", "python", []),
        ("tool/cli.py::cmd_sync", "python", []),
    )
    _imports(graph, "server/scheduler.py", "apscheduler.schedulers.asyncio")
    _imports(graph, "tool/cli.py", "argparse")
    graph.add_edge(
        "server/scheduler.py::setup_scheduler", "server/scheduler.py::sync_repos", edge_type="references"
    )
    graph.add_edge("tool/cli.py::main", "tool/cli.py::cmd_sync", edge_type="references")
    seeds = seed_roles(graph)
    # ``setup_scheduler`` itself is the scheduler builder (see the test above).
    assert seeds.by_role["scheduled_job"] - {"server/scheduler.py::setup_scheduler"} == {
        "server/scheduler.py::sync_repos"
    }
    assert {"tool/cli.py::main", "tool/cli.py::cmd_sync"} <= seeds.by_role["cli"]


def test_vscode_activate_is_startup_and_registered_commands_are_cli() -> None:
    graph = _graph(("src/extension.ts::activate", "typescript", []), ("src/extension.ts::refresh", "typescript", []))
    _imports(graph, "src/extension.ts", "vscode")
    graph.add_edge("src/extension.ts::activate", "src/extension.ts::refresh", edge_type="references")
    assert _seed_of(graph, "src/extension.ts::activate") == "startup"
    assert _seed_of(graph, "src/extension.ts::refresh") == "cli"


def test_aiohttp_handlers_seed_requests() -> None:
    graph = _graph(("gateway/api.py::_handle_get_job", "python", []), ("gateway/api.py::helper", "python", []))
    _imports(graph, "gateway/api.py", "aiohttp")
    by_role = seed_roles(graph).by_role
    assert "gateway/api.py::_handle_get_job" in by_role["request"]
    assert "gateway/api.py::helper" not in by_role["request"]


def _call(graph: nx.DiGraph, source: str, target: str) -> None:
    graph.add_edge(source, target, edge_type="calls", call_lines=[1])


def _roles(graph: nx.DiGraph) -> ExecutionRoles:
    return ExecutionRoles.build(graph, ExecutionGraphIndex(graph))


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


def test_the_hottest_reaching_role_wins_and_paths_decide_test_tooling_and_ui() -> None:
    roles = _roles(_reach_graph())
    assert roles.role_of("svc/items.py::save_all", "svc/items.py") == "request"
    # Only startup calls it, so it is startup's work.
    assert roles.role_of("svc/items.py::warm_cache", "svc/items.py") == "startup"
    # Reached only by a test: no evidence of how it runs in production.
    assert roles.role_of("svc/items.py::orphan", "svc/items.py") == "unknown"
    assert roles.role_of("tests/test_items.py::test_save", "tests/test_items.py") == "test"
    assert roles.role_of("scripts/release.py::main", "scripts/release.py") == "tooling"
    assert roles.role_of("web/src/Panel.tsx::Panel", "web/src/Panel.tsx") == "ui"
    assert roles.role_of("apps/desktop/src/store/session.ts::set", "apps/desktop/src/store/session.ts") == "ui"
    assert roles.role_of("app/dash/page.tsx::Page", "app/dash/page.tsx") == "unknown"


def test_a_cold_caller_never_makes_a_shared_helper_cold() -> None:
    """A helper an unseeded view and a CLI both call has no evidence: ``unknown``."""
    graph = _graph(
        ("cli/main.py::sync", "python", ["@click.command()"]),
        ("web/views.py::page", "python", []),
        ("svc/items.py::shared", "python", []),
        ("svc/items.py::cli_only", "python", []),
        ("svc/items.py::deeper", "python", []),
    )
    _call(graph, "cli/main.py::sync", "svc/items.py::shared")
    _call(graph, "web/views.py::page", "svc/items.py::shared")
    _call(graph, "cli/main.py::sync", "svc/items.py::cli_only")
    _call(graph, "svc/items.py::cli_only", "svc/items.py::deeper")
    roles = _roles(graph)
    assert roles.role_of("svc/items.py::shared", "svc/items.py") == "unknown"
    assert roles.role_of("svc/items.py::cli_only", "svc/items.py") == "cli"
    assert roles.role_of("svc/items.py::deeper", "svc/items.py") == "cli"


def test_a_hot_walk_runs_through_a_name_seed_and_stops_at_a_declared_one() -> None:
    """Calling a function runs it: a request that calls ``execute_job`` directly
    does that work per request. A declared job is where the job's own work begins."""
    graph = _graph(
        ("api/repos.py::sync", "python", ['@router.post("/sync")']),
        ("server/jobs.py::execute_job", "python", []),
        ("core/pipeline.py::persist_all", "python", []),
        ("worker/tasks.py::reindex", "python", ["@shared_task"]),
        ("core/pipeline.py::rebuild", "python", []),
    )
    _call(graph, "api/repos.py::sync", "server/jobs.py::execute_job")
    _call(graph, "server/jobs.py::execute_job", "core/pipeline.py::persist_all")
    _call(graph, "api/repos.py::sync", "worker/tasks.py::reindex")
    _call(graph, "worker/tasks.py::reindex", "core/pipeline.py::rebuild")
    roles = _roles(graph)
    assert roles.role_of("core/pipeline.py::persist_all", "core/pipeline.py") == "request"
    assert roles.role_of("worker/tasks.py::reindex", "worker/tasks.py") == "scheduled_job"
    assert roles.role_of("core/pipeline.py::rebuild", "core/pipeline.py") == "scheduled_job"


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
    roles = analyzer._mark_perf_entry_reachability([intra, cross, other])
    assert intra.details["execution_role"] == "startup"
    assert intra.details["role_owner"] == "svc/items.py::warm_cache"
    assert cross.details["execution_role"] == "request"
    assert "execution_role" not in other.details
    assert roles is not None and roles.role_of("svc/items.py::save_all", "svc/items.py") == "request"


def test_group_role_prefers_hot_and_never_reads_unknown_as_cold() -> None:
    assert hottest_role(["cli", "request"]) == "request"
    assert hottest_role(["cli", None]) == "unknown"
    assert hottest_role(["startup", "test"]) == "startup"
    assert hottest_role([]) == "unknown"
    assert set(ROLE_POINTS) == set(EXECUTION_ROLES)
    assert ROLE_POINTS["unknown"] > ROLE_POINTS["cli"] == ROLE_POINTS["ui"] == 0


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
        ("scheduled_job", "bounded", "background_unproven", "background_unproven"),
        # Unmeasured cost is checked first, so the count stays where it was.
        ("scheduled_job", "unknown", "unmeasured_cost", "unproven"),
        ("startup", "grows_with_data", "cold_role", "proven"),
        ("cli", "grows_with_data", "cold_role", "proven"),
        ("ui", "grows_with_data", "cold_role", "proven"),
        ("cli", "unknown", "unmeasured_cost", "unproven"),
    ],
)
def test_default_queue_eligibility_by_role(role, magnitude, reason, proof) -> None:
    item = _opportunity(role, magnitude)
    assert perf_queue_verdict(item).reason == reason
    assert queue_proof(item) == proof


@pytest.mark.parametrize("role", EXECUTION_ROLES)
@pytest.mark.parametrize("magnitude", ["grows_with_data", "bounded", "unknown"])
def test_the_stored_proof_never_disagrees_with_the_verdict(role, magnitude) -> None:
    """The SQL queue filters the stored proof; the counts read the verdict."""
    item = _opportunity(role, magnitude)
    reason = perf_queue_verdict(item).reason
    in_proof = queue_proof(item) in DEFAULT_QUEUE_PROOFS
    assert in_proof == (reason not in ("unmeasured_cost", "background_unproven"))
