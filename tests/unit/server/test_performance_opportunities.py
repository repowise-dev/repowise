"""Bounded causal performance product API and exact plan handoff.

Every case seeds through the writer that both index paths use, because the
queue is materialized: findings alone are no longer a queue, and a test that
inserted findings and read the surface would be testing a state the product
never reaches.
"""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy import select

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.persistence import crud
from repowise.core.persistence.models import HealthFinding, PerformanceOpportunity
from tests.unit.server.conftest import create_test_repo


def _finding(
    path: str,
    line: int,
    path_nodes: list[str],
    *,
    marker: str = "io_in_loop",
    magnitude: str | None = "grows_with_data",
):
    """A queue mechanics fixture: its loop is measured to grow unless told otherwise."""
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
            **({"loop_magnitude": magnitude} if magnitude else {}),
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


def _two_production_causes() -> list:
    """The shared-helper cause above plus a one-site production loop, both queued."""
    return [*_findings(), _finding("src/c.py", 40, ["src/c.py::run", "src/db.py::fetch"])]


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

    # The test-suite cause has no strategy, so it is asked for, not queued.
    assert (await _page(client, repo_id, context="test"))["total"] == 0
    test_page = await _page(client, repo_id, context="test", actionability="investigate")
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
        "performance_context": "staging (accepted: production, tooling, test, unknown, all)",
        "performance_boundary": "pigeon (accepted: db, network, filesystem, subprocess, lock, none)",
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


def _filesystem_finding(path: str, line: int, path_nodes: list[str]) -> HealthFindingData:
    """A repetition with no batch API to offer: this is what ``expected`` is for."""
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=line,
        line_end=line,
        details={
            "boundary_kind": "filesystem",
            "cross_function": True,
            "path": path_nodes,
            "resolution_basis": "reliable-edge",
            "loop_magnitude": "grows_with_data",
        },
        health_impact=0.0,
        reason="A file is read for every loop iteration.",
        dimension="performance",
    )


async def test_expected_sits_out_of_the_default_queue_but_not_the_facet(
    app, client: AsyncClient
) -> None:
    """466 of a real corpus's rows are exactly this: real, but nothing to do.

    The default page must not show them, the facet must still count them (so a
    reader can find them), and asking for them explicitly must return only
    them.
    """
    findings = [
        *_findings(),
        _filesystem_finding("src/fs.py", 1, ["src/fs.py::run", "src/fs.py::read"]),
    ]
    repo_id, _ = await _seed(app, client, findings)

    default = await _page(client, repo_id)
    assert all(item["actionability_state"] != "expected" for item in default["items"])
    assert default["summary"]["repository_total"] >= default["total"]

    facets = default["facets"]
    assert {entry["value"]: entry["total"] for entry in facets["actionability"]}.get(
        "expected"
    ) == 1

    only_expected = await _page(client, repo_id, actionability="expected")
    assert only_expected["total"] == 1
    assert only_expected["items"][0]["actionability_state"] == "expected"
    assert only_expected["items"][0]["actionability_reason"] == "inherent_to_boundary"


async def test_the_default_queue_reports_what_it_leaves_out(app, client: AsyncClient) -> None:
    """No strategy, expected, and non-production causes are counted, never dropped."""
    no_strategy = HealthFindingData(
        biomarker_type="resource_construction_in_loop",
        severity=Severity.MEDIUM,
        file_path="src/clients.py",
        function_name="each",
        line_start=5,
        line_end=5,
        details={"boundary_kind": "network", "loop_magnitude": "grows_with_data"},
        health_impact=0.0,
        reason="A client is built for every loop iteration.",
        dimension="performance",
    )
    unmeasured = _finding("src/d.py", 50, ["src/d.py::run", "src/db.py::fetch"], magnitude=None)
    findings = [
        *_findings(),
        no_strategy,
        _filesystem_finding("src/fs.py", 1, ["src/fs.py::run", "src/fs.py::read"]),
        unmeasured,
    ]
    repo_id, _ = await _seed(app, client, findings)

    default = await _page(client, repo_id)
    assert [item["intervention_symbol"] for item in default["items"]] == ["src/shared.py::load"]
    assert default["summary"]["default_queue"] == {
        "total": 1,
        "excluded": {
            "test": 1,
            "tooling": 0,
            "unknown": 0,
            "gated_off": 0,
            "cold_path": 0,
            "expected": 1,
            "no_strategy": 1,
            "unmeasured_cost": 1,
            "cold_role": 0,
            "background_unproven": 0,
        },
    }
    proof = {entry["value"]: entry["total"] for entry in default["facets"]["proof"]}
    assert proof["unproven"] >= 1
    asked = await _page(client, repo_id, actionability="investigate")
    assert [item["intervention_symbol"] for item in asked["items"]] == ["src/clients.py::each"]
    # Left out of the default queue, listed under its own filter, never leading.
    unproven = await _page(client, repo_id, proof="unproven")
    assert [item["file_path"] for item in unproven["items"]] == ["src/d.py"]
    assert unproven["items"][0]["may_lead"] is False


async def test_an_expected_row_never_leads(app, client: AsyncClient) -> None:
    import json

    finding = _filesystem_finding("src/fs.py", 1, ["src/fs.py::run", "src/fs.py::read"])
    repo_id, _ = await _seed(app, client, [finding])
    async with app.state.session_factory() as session:
        row = await crud.get_performance_summary(session, repo_id)
    summary = json.loads(row.summary_json)
    assert summary["lead"] is None
    assert summary["actionability"] == {"expected": 1}
    assert summary["default_queue"]["excluded"]["expected"] == 1


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
    assert body["found"] is False
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
    assert body["found"] is True
    assert body["lifecycle_status"] == "open"
    assert body["analyzed_commit"] == "a" * 40
    assert body["model_state"]["state"] == "current"
    assert set(body["facets"]) == {
        "actionability_confidence",
        "exposure",
        "amplification",
        "leverage",
        "change_risk",
        "loop_magnitude",
        "execution_role",
    }
    assert body["evidence_total"] == 2
    assert body["evidence_emitted"] == 1
    assert body["evidence_next_cursor"] == 1
    assert body["plan_status"] == "available"
    # The same words the Fix-first item uses for this cause, from one core function.
    assert body["gain_text"].startswith("one database call per loop iteration")


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
    assert detail["found"] is True
    assert detail["lifecycle_status"] == "resolved"


async def test_an_alternative_order_is_applied_before_the_page_not_after(
    app, client: AsyncClient
) -> None:
    """Sorting the fetched page would order twenty rows right and the repo wrong."""
    repo_id, _ = await _seed(app, client, _two_production_causes())
    opportunity_id = next(
        item["opportunity_id"]
        for item in (await _page(client, repo_id))["items"]
        if item["intervention_symbol"] == "src/shared.py::load"
    )
    by_leverage = await _page(client, repo_id, context="all", sort="leverage", limit=1)
    assert by_leverage["total"] == 2
    # The shared-helper cause carries two call sites and the other one, so
    # leverage puts it first whatever its rank is.
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
    # The test-suite cause has no strategy: counted, not queued.
    assert page["total"] == 1
    assert len(page["items"]) == 1
    assert page["summary"]["total"] == 2


async def test_the_queue_scopes_to_one_file_on_the_server(app, client):
    """A file surface asks about one file rather than filtering a page.

    The drawer that opens from the map's performance lens needs this file's
    causes and no others. Narrowing a page it already received would show
    whatever survived the cap, which is not the same question.
    """
    repo_id, _ = await _seed(app, client, _two_production_causes())
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


async def test_the_bulk_call_a_plan_names_is_served(app, client: AsyncClient) -> None:
    finding = HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path="src/owners.py",
        function_name="load",
        line_start=12,
        line_end=12,
        details={
            "boundary_kind": "db",
            "batch_form": '.in_("repo_id", keys)',
            "batch_equivalent": True,
        },
        health_impact=0.0,
        reason="Database work repeats for every loop iteration.",
        dimension="performance",
    )
    repo_id, opportunity_id = await _seed(app, client, [finding])
    body = (
        await client.get(f"/api/repos/{repo_id}/health/performance-opportunities/{opportunity_id}")
    ).json()
    assert body["actionability_state"] == "plan_ready"
    assert body["fix"]["api"] == '.in_("repo_id", keys)'


async def test_cold_and_unproven_background_roles_sit_one_filter_away(
    app, client: AsyncClient
) -> None:
    """Startup and CLI loops leave the default queue; a scheduled job needs growth."""
    served = _finding("src/c.py", 40, ["src/c.py::run", "src/db.py::fetch"])
    served.details["execution_role"] = "request"
    cli = _finding("src/e.py", 60, ["src/e.py::run", "src/db.py::fetch"])
    cli.details["execution_role"] = "cli"
    job = _finding(
        "src/f.py", 70, ["src/f.py::run", "src/db.py::fetch"], magnitude="bounded"
    )
    job.details["execution_role"] = "scheduled_job"
    repo_id, _ = await _seed(app, client, [served, cli, job])

    default = await _page(client, repo_id)
    assert [item["intervention_symbol"] for item in default["items"]] == ["src/c.py::run"]
    excluded = default["summary"]["default_queue"]["excluded"]
    assert (excluded["cold_role"], excluded["background_unproven"]) == (1, 1)
    roles = {entry["value"]: entry["total"] for entry in default["facets"]["role"]}
    assert roles == {"request": 1, "cli": 1, "scheduled_job": 1}
    assert default["items"][0]["facets"]["execution_role"] == "request"

    asked = await _page(client, repo_id, role="cli")
    assert [item["intervention_symbol"] for item in asked["items"]] == ["src/e.py::run"]
    unproven = await _page(client, repo_id, proof="background_unproven")
    assert [item["intervention_symbol"] for item in unproven["items"]] == ["src/f.py::run"]
    everything = await _page(client, repo_id, role="all")
    assert everything["total"] == 2


async def test_a_stored_finding_takes_the_role_this_run_found(app, client: AsyncClient) -> None:
    """A seed that moves in another file restamps a finding this run did not rescan."""
    from repowise.core.analysis.execution_roles import ExecutionRoles

    finding = _finding("src/c.py", 40, ["src/c.py::run", "src/db.py::fetch"])
    finding.details.update(execution_role="cli", role_owner="src/c.py::run")
    repo_id, _ = await _seed(app, client, [finding])
    assert (await _page(client, repo_id))["total"] == 0

    async with app.state.session_factory() as session:
        await crud.finalize_performance_opportunities(
            session,
            repo_id,
            analyzed_commit="a" * 40,
            execution_roles=ExecutionRoles({"src/c.py::run": "request"}),
        )
        await session.commit()
    page = await _page(client, repo_id)
    assert [item["facets"]["execution_role"] for item in page["items"]] == ["request"]
    async with app.state.session_factory() as session:
        stored = (
            await session.execute(
                select(HealthFinding.details_json).where(
                    HealthFinding.repository_id == repo_id
                )
            )
        ).scalar_one()
    assert '"execution_role":"request"' in stored


async def test_a_finding_stored_before_role_owner_is_keyed_on_its_loop_owner(
    app, client: AsyncClient
) -> None:
    """A legacy row gets its owner found as the analysis finds it, then keeps it."""
    import networkx as nx

    from repowise.core.analysis.execution_graph import ExecutionGraphIndex
    from repowise.core.analysis.execution_roles import ExecutionRoles

    crossing = _finding("src/c.py", 40, ["src/c.py::run", "src/db.py::fetch"])
    inside = _finding("src/e.py", 12, [])
    inside.details.pop("path")
    inside.details["cross_function"] = False
    repo_id, _ = await _seed(app, client, [crossing, inside])

    graph = nx.DiGraph()
    graph.add_node(
        "src/e.py::load", node_type="symbol", name="load", file_path="src/e.py",
        start_line=10, end_line=20,
    )
    roles = ExecutionRoles(
        {"src/c.py::run": "request", "src/e.py::load": "event_consumer"},
        ExecutionGraphIndex(graph),
    )
    async with app.state.session_factory() as session:
        await crud.finalize_performance_opportunities(
            session, repo_id, analyzed_commit="a" * 40, execution_roles=roles
        )
        await session.commit()
        stored = (
            await session.execute(
                select(HealthFinding.file_path, HealthFinding.details_json).where(
                    HealthFinding.repository_id == repo_id
                )
            )
        ).all()
    details = {path: json.loads(raw) for path, raw in stored}
    assert details["src/c.py"]["role_owner"] == "src/c.py::run"
    assert details["src/c.py"]["execution_role"] == "request"
    assert details["src/e.py"]["role_owner"] == "src/e.py::load"
    assert details["src/e.py"]["execution_role"] == "event_consumer"
