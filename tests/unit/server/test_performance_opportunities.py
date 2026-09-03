"""Bounded causal performance product API and exact plan handoff.

Every case seeds through the writer that both index paths use, because the
queue is materialized: findings alone are no longer a queue, and a test that
inserted findings and read the surface would be testing a state the product
never reaches.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.persistence import crud
from repowise.core.persistence.models import PerformanceOpportunity
from tests.unit.server.conftest import create_test_repo


def _finding(path: str, line: int, path_nodes: list[str], *, marker: str = "io_in_loop"):
    return HealthFindingData(
        biomarker_type=marker,
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=line,
        line_end=line,
        details={
            "boundary_kind": "db",
            "cross_function": True,
            "path": path_nodes,
            "resolution_basis": "reliable-edge",
        },
        health_impact=0.0,
        reason="Database work repeats for every loop iteration.",
        dimension="performance",
    )


def _findings() -> list:
    return [
        _finding("src/a.py", 10, ["src/a.py::run", "src/shared.py::load", "src/db.py::fetch"]),
        _finding("src/b.py", 20, ["src/b.py::run", "src/shared.py::load", "src/db.py::fetch"]),
        # A separate test-suite cause with no coherent shared intervention.
        _finding(
            "tests/test_db.py",
            30,
            ["tests/test_db.py::run", "src/db.py::fetch"],
            marker="serial_await_in_loop",
        ),
    ]


async def _materialize(app, repo_id: str, findings: list) -> None:
    link_performance_findings(findings)
    async with app.state.session_factory() as session:
        await crud.save_health_findings(session, repo_id, findings)
        await crud.finalize_performance_opportunities(
            session, repo_id, analyzed_commit="a" * 40
        )
        await session.commit()


async def _seed(app, client: AsyncClient, findings: list | None = None) -> tuple[str, str]:
    repo = await create_test_repo(client)
    await _materialize(app, repo["id"], _findings() if findings is None else findings)
    async with app.state.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(PerformanceOpportunity).where(
                        PerformanceOpportunity.repository_id == repo["id"],
                        PerformanceOpportunity.execution_context == "production",
                    )
                )
            )
            .scalars()
            .all()
        )
    return repo["id"], rows[0].opportunity_id if rows else ""


async def _page(client: AsyncClient, repo_id: str, **params) -> dict:
    return (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities", params=params
        )
    ).json()


async def test_naming_no_context_asks_about_production(
    client: AsyncClient, app
) -> None:
    """Most of what the analysis finds is not production code.

    A caller that names no context is asking what to fix, so the default view
    is the code that ships. Everything else stays one argument away and keeps
    its count in the facets and in ``repository_total``.
    """
    repo_id, opportunity_id = await _seed(app, client)
    body = await _page(client, repo_id)

    assert body["total"] == 1
    assert body["items"][0]["opportunity_id"] == opportunity_id
    assert body["summary"]["repository_total"] == 2
    assert "ignored_arguments" not in body
    assert {entry["value"] for entry in body["facets"]["context"]} == {"production", "test"}


async def test_opportunities_are_grouped_bounded_and_split_by_context(
    client: AsyncClient, app
) -> None:
    repo_id, opportunity_id = await _seed(app, client)
    page = await _page(client, repo_id, context="production", limit=1)

    assert len(page["items"]) == 1
    assert page["items"][0]["opportunity_id"] == opportunity_id
    assert page["items"][0]["affected_call_sites_total"] == 2
    assert page["items"][0]["confidence"] == "medium"
    assert page["items"][0]["plan_status"] == "available"
    assert page["items"][0]["plan_id"]
    # The headline describes the context on screen, so it cannot state a
    # number the queue under it contradicts. The census survives beside it.
    assert page["summary"]["total"] == 1
    assert page["summary"]["repository_total"] == 2
    assert page["summary"]["context"] == {"production": 1}
    assert page["summary"]["with_plan_total"] == 1
    assert page["summary"]["status"] == "current"

    every = await _page(client, repo_id, context="all", limit=1)
    assert every["summary"]["total"] == 2
    assert every["summary"]["repository_total"] == 2
    assert every["summary"]["context"] == {"production": 1, "test": 1}

    test_page = await _page(client, repo_id, context="test")
    assert test_page["total"] == 1
    assert test_page["items"][0]["plan_id"] is None
    assert test_page["items"][0]["plan_status"] == "no_safe_plan"


async def test_raw_evidence_is_paged_and_plan_matching_never_falls_back(
    client: AsyncClient, app
) -> None:
    repo_id, opportunity_id = await _seed(app, client)
    first = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}/findings",
            params={"limit": 1},
        )
    ).json()
    second = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}/findings",
            params={"limit": 1, "offset": first["next_offset"]},
        )
    ).json()
    assert first["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["finding_id"] != second["items"][0]["finding_id"]
    # A public reference, never the storage row id a reindex replaces.
    assert first["items"][0]["finding_id"].startswith("finding_")

    async with app.state.session_factory() as session:
        rows = await crud.get_refactoring_suggestions(session, repo_id)
        rows[0].opportunity_id = "unrelated"
        await session.commit()
    page = await _page(client, repo_id, context="production")
    assert page["items"][0]["plan_id"] is None
    assert page["items"][0]["plan_status"] == "not_persisted"


async def test_an_unclassifiable_file_is_counted_and_stays_visible(app, client: AsyncClient):
    """An unknown context must not mean an unlisted opportunity.

    Reporting an unclassifiable file as production was the thing to fix. Losing
    it from every view instead would be the same mistake pointing the other way.
    """
    repo_id, _ = await _seed(
        app,
        client,
        [
            _finding(
                "docs/snippets/walk.py",
                10,
                ["docs/snippets/walk.py::demo", "src/shared.py::load", "src/db.py::fetch"],
            )
        ],
    )
    body = await _page(client, repo_id, context="all")
    assert body["summary"]["context"] == {"unknown": 1}
    assert [item["execution_context"] for item in body["items"]] == ["unknown"]
    assert (await _page(client, repo_id, context="unknown"))["total"] == 1


async def test_the_retired_context_spelling_still_answers_but_is_never_echoed(
    app, client: AsyncClient
) -> None:
    """An older client asked for Production+Tooling under one name.

    Answering it keeps that client working. Echoing the name back would make a
    retired spelling look like the current product concept.
    """
    repo_id, _ = await _seed(app, client)
    body = await _page(client, repo_id, context="production_tooling")
    assert body["total"] == 1
    assert [item["execution_context"] for item in body["items"]] == ["production"]
    assert "production_tooling" not in body["summary"]["context"]


async def test_an_unrecognized_filter_value_is_reported_not_read_as_no_data(
    app, client: AsyncClient
) -> None:
    """Silently emptying the queue would look like a clean repository.

    An unrecognized value is named and then treated as absent, which is what
    every other ignored filter does, so it reads as the default view rather
    than as an empty one.
    """
    repo_id, _ = await _seed(app, client)
    body = await _page(client, repo_id, context="staging", boundary="pigeon")
    assert body["total"] == 1
    assert body["items"][0]["execution_context"] == "production"
    assert body["ignored_arguments"] == {
        "performance_context": "staging",
        "performance_boundary": "pigeon",
    }


async def test_facets_keep_the_alternatives_a_selected_filter_would_erase(
    app, client: AsyncClient
) -> None:
    """A facet counted under its own filter reports every other value as zero."""
    repo_id, _ = await _seed(app, client)
    facets = (await _page(client, repo_id, context="test"))["facets"]
    assert {entry["value"] for entry in facets["context"]} == {"production", "test"}
    assert {entry["value"]: entry["total"] for entry in facets["context"]}["production"] == 1
    # A filter on another dimension does narrow this one.
    assert (await _page(client, repo_id, boundary="network"))["facets"]["context"] == []


async def test_an_id_from_an_older_model_reports_stale_rather_than_no_plan(
    app, client: AsyncClient
) -> None:
    """A first-model id used to fail to match, which read as nothing to do."""
    repo_id, _ = await _seed(app, client)
    body = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities/perf_0123456789abcdef0123"
        )
    ).json()
    assert body["resolved"] is False
    assert body["model_state"]["state"] == "stale_model"
    assert body["model_state"]["refresh_required"] is True
    assert "repowise update" in body["detail"]


async def test_detail_carries_the_facets_and_evidence_for_one_cause(
    app, client: AsyncClient
) -> None:
    repo_id, opportunity_id = await _seed(app, client)
    body = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}",
            params={"evidence_limit": 1},
        )
    ).json()
    assert body["resolved"] is True
    assert body["lifecycle_status"] == "open"
    assert body["analyzed_commit"] == "a" * 40
    assert body["model_state"]["state"] == "current"
    assert set(body["facets"]) == {
        "actionability_confidence",
        "exposure",
        "amplification",
        "leverage",
        "change_risk",
    }
    assert body["evidence_total"] == 2
    assert body["evidence_emitted"] == 1
    assert body["evidence_next_cursor"] == 1
    assert body["plan_status"] == "available"


async def test_a_cause_that_stops_being_observed_is_resolved_not_deleted(
    app, client: AsyncClient
) -> None:
    """A held id has to keep answering after the code was fixed."""
    repo_id, opportunity_id = await _seed(app, client)
    await _materialize(app, repo_id, [_findings()[2]])

    page = await _page(client, repo_id, context="all")
    assert [item["opportunity_id"] for item in page["items"]] != [opportunity_id]
    detail = (
        await client.get(
            f"/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}"
        )
    ).json()
    assert detail["resolved"] is True
    assert detail["lifecycle_status"] == "resolved"


async def test_an_alternative_order_is_applied_before_the_page_not_after(
    app, client: AsyncClient
) -> None:
    """Sorting the fetched page would order twenty rows right and the repo wrong."""
    repo_id, opportunity_id = await _seed(app, client)
    by_leverage = await _page(client, repo_id, context="all", sort="leverage", limit=1)
    assert by_leverage["total"] == 2
    # The production cause carries two call sites; the test one carries a
    # single site, so leverage puts production first whatever its rank is.
    assert by_leverage["items"][0]["opportunity_id"] == opportunity_id
    assert by_leverage["items"][0]["affected_call_sites_total"] == 2


async def test_a_page_costs_the_page_not_the_repository(app, client: AsyncClient) -> None:
    """Two hundred unrelated findings must not reach the performance queue."""
    repo_id, _ = await _seed(app, client)
    async with app.state.session_factory() as session:
        await crud.save_health_findings(
            session,
            repo_id,
            [
                HealthFindingData(
                    biomarker_type="long_function",
                    severity=Severity.MEDIUM,
                    file_path=f"src/noise_{index}.py",
                    function_name="run",
                    line_start=index,
                    line_end=index,
                    details={},
                    health_impact=1.0,
                    reason="Unrelated defect finding.",
                    dimension="defect",
                )
                for index in range(200)
            ]
            + _findings(),
        )
        await crud.finalize_performance_opportunities(session, repo_id)
        await session.commit()

    page = await _page(client, repo_id, context="all", limit=1)
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["summary"]["total"] == 2


async def test_the_queue_scopes_to_one_file_on_the_server(app, client):
    """A file surface asks about one file rather than filtering a page.

    The drawer that opens from the map's performance lens needs this file's
    causes and no others. Narrowing a page it already received would show
    whatever survived the cap, which is not the same question.
    """
    repo_id, _ = await _seed(app, client)
    whole = await _page(client, repo_id, context="all")
    # Scope to whichever file the grouping named as the place to intervene: the
    # column is the intervention site, not every file the evidence touches.
    target = whole["items"][0]["file_path"]
    scoped = await _page(client, repo_id, context="all", file_paths=target)
    assert scoped["total"] >= 1
    assert {item["file_path"] for item in scoped["items"]} == {target}
    # The whole queue is larger, so the scope is doing the work and not the cap.
    assert whole["total"] > scoped["total"]


async def test_a_file_with_no_cause_scopes_to_an_empty_queue(app, client):
    repo_id, _ = await _seed(app, client)
    page = await _page(client, repo_id, context="all", file_paths="src/nothing-here.py")
    assert page["total"] == 0
    assert page["items"] == []
