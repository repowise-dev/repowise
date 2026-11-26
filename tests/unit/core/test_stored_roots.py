"""Stored roots: root-flagged files, framework-loaded files and route handlers start reach."""

from __future__ import annotations

import pytest

from repowise.core.inventory.roots import load_stored_roots
from repowise.core.inventory.usage import package_usage
from repowise.core.inventory.vuln_reach import load_production_roots
from repowise.core.persistence.models import GraphEdge, GraphNode, HttpRoute
from tests.unit.core.inventory_fixture import REPO, session  # noqa: F401  (fixture)

# One handler per family the route table binds: a Python decorator, a JS
# router's named function, a Go package function, and a declarative route
# (annotation, attribute or a file route's exported function).
_FAMILIES = [
    ("fastapi", "app/api/users.py", "app/api/users.py::list_users"),
    ("flask", "app/views.py", "app/views.py::index"),
    ("express", "server/routes.js", "server/controllers/users.js::list"),
    ("hono", "src/index.ts", "src/handlers.ts::health"),
    ("go", "cmd/server/main.go", "internal/api/users.go::ListUsers"),
    ("spring", "src/main/java/app/Users.java", "src/main/java/app/Users.java::Users.list"),
    ("aspnet", "Api/UsersController.cs", "Api/UsersController.cs::UsersController.Get"),
    ("next-app", "app/api/users/route.ts", "app/api/users/route.ts::GET"),
]


def _route(framework: str, file_path: str, handler: str | None, line: int = 3) -> HttpRoute:
    return HttpRoute(
        repository_id=REPO,
        method="GET",
        path=f"/{framework}",
        file_path=file_path,
        line=line,
        framework=framework,
        handler_symbol_id=handler,
        handler=handler.rsplit("::", 1)[-1] if handler else None,
    )


async def _reachability(session, tmp_path) -> dict[str, dict[str, str]]:  # noqa: F811
    doc = await package_usage(session, REPO, tmp_path, "requests")
    return {s["path"]: s["reachability"] for s in doc["sites"]}


@pytest.mark.parametrize(("framework", "registered_in", "handler"), _FAMILIES)
async def test_a_stored_route_handler_is_a_production_root(
    session,  # noqa: F811
    framework,
    registered_in,
    handler,
):
    session.add(_route(framework, registered_in, handler))
    # An inline handler names no function, so it starts nothing.
    session.add(_route(framework, registered_in, None, line=9))
    await session.commit()

    stored = await load_stored_roots(session, REPO)
    assert stored.route_handlers == {handler}
    assert handler.split("::", 1)[0] in stored.all_files
    roots = await load_production_roots(session, REPO)
    assert handler in roots
    # The entry point the fixture flags is a root too, as its top-level code.
    assert "src/app/main.py::__module__" in roots


async def test_a_handler_outside_production_code_starts_nothing(session):  # noqa: F811
    session.add_all(
        [
            _route("fastapi", "docs_src/tutorial/main.py", "docs_src/tutorial/main.py::read"),
            _route("express", "test/app.test.js", "test/app.test.js::ok"),
        ]
    )
    await session.commit()
    roots = await load_production_roots(session, REPO)
    assert not any(r.startswith(("docs_src/", "test/")) for r in roots)


async def test_package_usage_reaches_a_file_through_its_route_handler(
    session,  # noqa: F811
    tmp_path,
):
    # Nothing imports worker.py, so the import graph alone cannot reach it.
    assert (await _reachability(session, tmp_path))["src/jobs/worker.py"]["state"] == "unknown"
    session.add(_route("flask", "src/jobs/worker.py", "src/jobs/worker.py::poll"))
    await session.commit()
    assert (await _reachability(session, tmp_path))["src/jobs/worker.py"] == {
        "state": "reachable",
        "basis": "import_graph",
    }


async def test_a_framework_loaded_file_is_a_root_and_a_bound_symbol_is_not(
    session,  # noqa: F811
    tmp_path,
):
    session.add_all(
        [
            GraphNode(repository_id=REPO, node_id="framework:celery", node_type="external"),
            # A binding into a symbol says the symbol runs, not that its file loads.
            GraphEdge(
                repository_id=REPO,
                source_node_id="framework:celery",
                target_node_id="src/old/legacy.py::task",
                edge_type="framework_binds",
            ),
        ]
    )
    await session.commit()
    assert "src/old/legacy.py" not in (await load_stored_roots(session, REPO)).files

    session.add(
        GraphEdge(
            repository_id=REPO,
            source_node_id="framework:celery",
            target_node_id="src/jobs/worker.py",
            edge_type="framework",
        )
    )
    await session.commit()
    stored = await load_stored_roots(session, REPO)
    assert stored.files == {"src/app/main.py", "src/jobs/worker.py"}
    assert "src/jobs/worker.py::__module__" in await load_production_roots(session, REPO)
    assert (await _reachability(session, tmp_path))["src/jobs/worker.py"]["state"] == "reachable"


async def test_a_root_flag_without_the_entry_flag_counts(session):  # noqa: F811
    node = (
        await session.execute(
            GraphNode.__table__.select().where(GraphNode.node_id == "src/jobs/worker.py")
        )
    ).first()
    assert node is not None and not node.is_entry_point
    await session.execute(
        GraphNode.__table__.update()
        .where(GraphNode.node_id == "src/jobs/worker.py")
        .values(is_reachability_root=True)
    )
    await session.commit()
    assert "src/jobs/worker.py" in (await load_stored_roots(session, REPO)).files


async def test_a_handler_file_missing_from_the_graph_is_no_usage_root(
    session,  # noqa: F811
    tmp_path,
):
    await session.execute(
        GraphNode.__table__.update()
        .where(GraphNode.node_id == "src/app/main.py")
        .values(is_entry_point=False)
    )
    session.add(_route("flask", "src/gone.py", "src/gone.py::index"))
    await session.commit()
    # No root is in the graph, so nothing is judged against roots.
    assert (await _reachability(session, tmp_path))["src/jobs/worker.py"] == {
        "state": "unknown",
        "basis": "no_entry_points",
    }
