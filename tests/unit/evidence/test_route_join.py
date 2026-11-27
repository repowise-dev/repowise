"""The route-table join: a ``route_latency`` entry names the handler of the
one stored route its method and template match, whatever the parameter syntax."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.evidence import EVIDENCE_FORMAT, parse_evidence
from repowise.core.evidence.ingest import join_entries
from repowise.core.evidence.measured_fold import UNJOINED, fold_routes
from repowise.core.evidence.route_join import match_route, route_index, route_template
from repowise.core.workspace.repo_routes import Route


def _route(method: str, path: str, handler: str | None) -> Route:
    return Route(method, path, "fastapi", "app/r.py", 1, handler_symbol_id=handler)


STORED = [
    _route("GET", "/repos/{param}", "app/r.py::get_repo"),
    _route("DELETE", "/repos/{param}", "app/r.py::delete_repo"),
    _route("GET", "/repos/{param}/files/{param}", "app/r.py::get_file"),
    _route("GET", "/repos/me", "app/r.py::my_repos"),
    _route("GET", "/health", None),
    _route("POST", "/hooks", "app/a.py::hook"),
    _route("POST", "/hooks", "app/b.py::hook"),
    _route("*", "/legacy/{param}", "app/r.py::legacy"),
    _route("ALL", "/spring/{param}", "app/r.py::spring"),
    # Two services: one names the method, one serves every method.
    _route("GET", "/status", "svc_a/app.py::status"),
    _route("*", "/status", "svc_b/views.py::status"),
]


@pytest.mark.parametrize(
    "route",
    [
        "/repos/{repo_id}",
        "/repos/:repo_id",
        "/repos/<int:repo_id>",
        "/repos/<repo_id>",
        "/repos/[repoId]",
        "/repos/*",
        "/Repos/{repo_id:path}/",
    ],
)
def test_every_parameter_syntax_reads_as_one_template(route) -> None:
    assert route_template(route) == "/repos/{param}"
    assert match_route("get", route, route_index(STORED)) == ("app/r.py::get_repo", None)


@pytest.mark.parametrize(
    ("method", "route", "expected"),
    [
        # The method decides between two handlers of one template.
        ("DELETE", "/repos/:id", ("app/r.py::delete_repo", None)),
        ("GET", "/repos/<a>/files/<path:p>", ("app/r.py::get_file", None)),
        # A literal segment is its own route, never a parameter's.
        ("GET", "/repos/me", ("app/r.py::my_repos", None)),
        # A stored route naming no handler (inline) names none.
        ("GET", "/health", (None, "no_handler")),
        # Two handlers for one method and template: never a guess.
        ("POST", "/hooks", (None, "ambiguous_route")),
        # No stored route: a prefix the index never saw, a method it never registered.
        ("GET", "/api/repos/{id}", (None, "unknown_route")),
        ("PUT", "/repos/{id}", (None, "unknown_route")),
        # A route serving every method, read when no route names the method.
        ("PATCH", "/legacy/:id", ("app/r.py::legacy", None)),
        ("GET", "/spring/{id}", ("app/r.py::spring", None)),
        # Either service could have served it: never a guess.
        ("GET", "/status", (None, "ambiguous_route")),
        # A converter with arguments is one parameter; a non-name in angles is not.
        ("GET", "/repos/<any(a, b):kind>", ("app/r.py::get_repo", None)),
        ("GET", "/repos/<-x>", (None, "unknown_route")),
    ],
)
def test_the_join_names_one_handler_or_says_why_not(method, route, expected) -> None:
    assert tuple(match_route(method, route, route_index(STORED))) == expected


def _doc(routes: list[dict]) -> object:
    return parse_evidence(
        json.dumps({"format": EVIDENCE_FORMAT, "kind": "route_latency", "routes": routes})
    )


def test_a_route_document_joins_through_the_stored_routes_and_counts_the_rest() -> None:
    doc = _doc(
        [
            {"method": "GET", "route": "/repos/:id", "count": 10, "total_ms": 100.0},
            {"method": "POST", "route": "/hooks", "count": 2, "total_ms": 4.0},
            {"method": "GET", "route": "/health", "count": 5, "total_ms": 1.0},
            {"method": "GET", "route": "/nowhere", "count": 1, "total_ms": 1.0},
        ]
    )
    joined = join_entries(doc, {}, {}, frozenset(), routes=STORED)
    assert joined.matched == 4 and not joined.skipped
    assert [r["handler_symbol_id"] for r in joined.rows] == ["app/r.py::get_repo", None, None, None]
    assert joined.unlinked == {"ambiguous_route": 1, "no_handler": 1, "unknown_route": 1}


def _run(run_id: str, day: int, name: str = "api") -> SimpleNamespace:
    return SimpleNamespace(
        id=run_id,
        workload_name=name,
        environment="prod",
        measured_at=datetime(2026, 11, day, tzinfo=UTC),
        mapping_partial=False,
    )


def test_the_fold_sums_each_handler_over_its_routes_and_keeps_the_newest_window() -> None:
    runs = [_run("old", 1), _run("new", 2)]
    points = [
        ("old", "app/r.py::get_repo", "GET", "/repos/:id", 1, 999.0, 1.0, 0),
        ("new", "app/r.py::get_repo", "GET", "/repos/:id", 10, 300.0, 80.0, 1),
        ("new", "app/r.py::get_repo", "HEAD", "/repos/:id", 5, 100.0, 120.0, None),
        ("new", "app/r.py::my_repos", "GET", "/repos/me", 40, 500.0, None, 2),
        # An untimed route counts its requests, never a 0 ms mean.
        ("new", "app/r.py::my_repos", "POST", "/repos/me", 60, None, None, None),
        ("new", None, "GET", "/nowhere", 3, 100.0, 50.0, 0),
    ]
    (found,) = fold_routes(runs, points)
    assert (found.run.id, found.total_ms, found.requests, found.routes) == ("new", 1000.0, 118, 5)
    mine, repo = found.rows
    assert (mine.symbol_id, mine.rank, mine.self_share, mine.requests) == (
        "app/r.py::my_repos",
        1,
        0.5,
        100,
    )
    # The mean is over the 40 timed requests only.
    assert (mine.mean_ms, mine.p95_ms, mine.errors) == (12.5, None, 2)
    assert mine.routes == ("GET /repos/me", "POST /repos/me")
    assert (repo.rank, repo.self_share, repo.total_ms, repo.requests) == (2, 0.4, 400.0, 15)
    assert repo.mean_ms == 400.0 / 15
    # The slowest p95 among its routes; errors as far as the source said.
    assert (repo.p95_ms, repo.errors) == (120.0, 1)
    assert repo.routes == ("GET /repos/:id", "HEAD /repos/:id")
    assert found.unattributed == {UNJOINED: {"routes": 1, "total_ms": 100.0, "share": 0.1}}
    assert fold_routes([], points) == []
