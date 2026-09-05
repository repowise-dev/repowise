"""Every JSON route names its response schema, or says why it does not.

The TypeScript contract is generated from ``components.schemas``, so a route
whose success response is an anonymous object contributes nothing to it and
its shape goes back to being hand-copied. This walks the live application and
fails when a route serves an unnamed body without an entry below.

Two lists, and the difference matters. ``WAIVED`` is permanent: those routes do
not serve a modelled JSON object at all. ``UNMODELLED`` is a shrinking backlog
of routes that should be modelled and are not yet; a route may only leave it,
never join it, and modelling one without deleting its line here fails too.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from repowise.server.app import create_app

#: Routes that will never carry a named JSON schema, and why.
WAIVED: dict[tuple[str, str], str] = {
    ("POST", "/api/repos/{repo_id}/chat/messages"): "SSE stream",
    ("GET", "/api/jobs/{job_id}/stream"): "SSE stream",
    ("GET", "/api/repos/{repo_id}/export"): "streamed zip download",
    ("GET", "/api/repos/{repo_id}/file-content"): "raw file body",
    ("GET", "/api/repos/{repo_id}/health/badge.svg"): "image/svg+xml",
    ("GET", "/api/graph/{repo_id}/c4/mermaid"): "text/plain diagram source",
    ("GET", "/api/graph/{repo_id}/c4/structurizr"): "text/plain diagram source",
    ("GET", "/metrics"): "Prometheus text exposition",
    ("POST", "/api/providers/{provider_id}/key"): "204, no body",
    ("DELETE", "/api/providers/{provider_id}/key"): "204, no body",
    ("GET", "/api/pages"): (
        "returns full or summary rows by ``fields``; the models serialize "
        "themselves and FastAPI cannot coerce one shape into the other"
    ),
}

#: Routes still serving an anonymous object. Delete a line when you model one.
UNMODELLED: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/repos/{repo_id}/files"),
        ("GET", "/api/repos/{repo_id}/files/{file_path}"),
        ("POST", "/api/repos/{repo_id}/generate/estimate"),
        ("GET", "/api/repos/{repo_id}/health/coverage"),
        ("GET", "/api/repos/{repo_id}/health/files"),
        ("GET", "/api/repos/{repo_id}/health/files/breakdown"),
        ("GET", "/api/repos/{repo_id}/health/map"),
        ("GET", "/api/repos/{repo_id}/health/modules"),
        ("GET", "/api/repos/{repo_id}/health/overview"),
        ("GET", "/api/repos/{repo_id}/health/performance-opportunities"),
        ("GET", "/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}"),
        (
            "GET",
            "/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}/findings",
        ),
        ("GET", "/api/repos/{repo_id}/health/tests-reaching"),
        ("GET", "/api/repos/{repo_id}/overview-summary"),
        ("POST", "/api/repos/{repo_id}/preflight"),
        ("GET", "/api/repos/{repo_id}/stats/highlights"),
        ("GET", "/api/symbols/detail"),
    }
)

_SUCCESS_CODES = ("200", "201", "202", "204")
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})


def _openapi_path(path: str) -> str:
    """Drop Starlette's converter suffix; OpenAPI names the parameter alone."""
    return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)


def _names_a_schema(schema: dict | None) -> bool:
    """Whether a response body resolves to a schema the generator can emit."""
    if not schema:
        return False
    if "$ref" in schema or "anyOf" in schema or "allOf" in schema:
        return True
    if schema.get("type") == "array":
        return "$ref" in (schema.get("items") or {})
    return False


@pytest.fixture(scope="module")
def modelled_state() -> dict[tuple[str, str], bool]:
    app = create_app()
    schema = app.openapi()
    state: dict[tuple[str, str], bool] = {}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method.upper() not in _METHODS:
                continue
            responses = operation.get("responses", {})
            code = next((c for c in _SUCCESS_CODES if c in responses), None)
            if code is None:
                continue
            content = responses[code].get("content")
            body = next(iter(content.values())).get("schema") if content else None
            state[(method.upper(), path)] = _names_a_schema(body)
    return state


def test_every_route_is_modelled_waived_or_tracked(modelled_state) -> None:
    unnamed = {route for route, modelled in modelled_state.items() if not modelled}
    unaccounted = unnamed - set(WAIVED) - UNMODELLED

    assert not unaccounted, (
        "These routes serve an anonymous JSON body. Give each a response_model, "
        "or add it to WAIVED with a reason: " + repr(sorted(unaccounted))
    )


def test_a_modelled_route_leaves_the_backlog(modelled_state) -> None:
    fixed = {route for route in UNMODELLED if modelled_state.get(route)}

    assert not fixed, (
        "These routes are modelled now. Delete them from UNMODELLED: " + repr(sorted(fixed))
    )


def test_the_lists_only_name_live_routes(modelled_state) -> None:
    stale = (set(WAIVED) | UNMODELLED) - set(modelled_state)

    assert not stale, "These entries name no live route: " + repr(sorted(stale))


def test_no_route_is_both_waived_and_tracked() -> None:
    assert not set(WAIVED) & UNMODELLED


def test_the_router_surface_is_fully_classified(modelled_state) -> None:
    """Guards the walk itself: a router mounted but unseen would hide gaps."""
    live = {
        (method, _openapi_path(route.path))
        for route in create_app().routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method in _METHODS
    }
    # 204 routes carry no success body and are absent from the walk by design.
    missing = live - set(modelled_state) - set(WAIVED)

    assert not missing, repr(sorted(missing))
