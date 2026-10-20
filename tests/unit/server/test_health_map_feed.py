"""The bounded field the map draws, and what it says about what it left out.

A cap is a claim. These cases pin the two halves of that claim: which files the
server admits and in which order, and whether the counts it publishes describe
the rows it actually returned.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import update

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.persistence import crud
from repowise.core.persistence.models import PerformanceOpportunity
from tests.unit.server.conftest import create_test_repo


def _metric(
    path: str,
    nloc: int,
    module: str = "src",
    *,
    score: float | None = 7.0,
    origin: str | None = None,
) -> dict:
    return {
        "file_path": path,
        "score": score,
        "code_origin": origin,
        "max_ccn": 3,
        "max_nesting": 2,
        "nloc": nloc,
        "duplication_pct": 0.0,
        "has_test_file": False,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "module": module,
    }


def _perf_finding(path: str, line: int, path_nodes: list[str]) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
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


async def _seed(app, client: AsyncClient, *, queued: bool = True) -> str:
    """One big clean file, and one tiny file carrying the repository's cause.

    The tiny file is the whole point: a size ranking puts it last, and it is
    where the work is. ``queued`` stores the cause as a default-queue member,
    the verdict the finalizer writes for a production cause with a proven cost.
    """
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    metrics = [_metric(f"src/big{i}.py", 1000 - i) for i in range(5)]
    metrics.append(_metric("src/tiny.py", 3))
    findings = [
        _perf_finding("src/tiny.py", 10, ["src/tiny.py::run", "src/db.py::fetch"]),
        _perf_finding("src/tiny.py", 20, ["src/tiny.py::run", "src/db.py::fetch"]),
    ]
    link_performance_findings(findings)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(session, repo_id, metrics)
        await crud.save_health_findings(session, repo_id, findings)
        await crud.finalize_performance_opportunities(
            session, repo_id, analyzed_commit="a" * 40
        )
        await session.execute(
            update(PerformanceOpportunity)
            .where(PerformanceOpportunity.repository_id == repo_id)
            .values(queue_eligible=queued)
        )
        await session.commit()
    return repo_id


async def _feed(client: AsyncClient, repo_id: str, **params) -> dict:
    return (await client.get(f"/api/repos/{repo_id}/health/map", params=params)).json()


async def test_a_small_file_with_a_cause_outranks_a_large_clean_one(app, client):
    repo_id = await _seed(app, client)
    feed = await _feed(client, repo_id, cap=2)
    drawn = [f["file_path"] for f in feed["files"]]
    assert "src/tiny.py" in drawn
    assert feed["selection"]["basis"] == (
        "active_then_module_floor_then_origin_tier_then_performance_then_debt"
    )
    assert feed["selection"]["performance_shown"] == 1
    # The remaining slot went to the debt rank: equal scores, so the bigger file.
    assert feed["selection"]["nloc_shown"] == 1
    assert "src/big0.py" in drawn


async def test_the_active_selection_is_admitted_before_anything_else(app, client):
    repo_id = await _seed(app, client)
    feed = await _feed(client, repo_id, cap=1, active="src/big4.py")
    assert [f["file_path"] for f in feed["files"]] == ["src/big4.py"]
    assert feed["selection"]["active_shown"] == ["src/big4.py"]
    # The cap is now spent, and the response says what that cost.
    assert feed["omitted"]["performance_files"] == 1
    assert feed["omitted"]["opportunities"] >= 1


async def test_a_pinned_path_the_index_does_not_hold_is_named_not_faked(app, client):
    repo_id = await _seed(app, client)
    feed = await _feed(client, repo_id, cap=5, active="src/does-not-exist.py")
    assert feed["selection"]["active_missing"] == ["src/does-not-exist.py"]
    assert all(f["file_path"] != "src/does-not-exist.py" for f in feed["files"])


async def test_the_counts_describe_the_rows_that_came_back(app, client):
    repo_id = await _seed(app, client)
    feed = await _feed(client, repo_id, cap=3)
    assert feed["shown"] == len(feed["files"]) == 3
    assert feed["eligible_total"] == 6
    assert feed["omitted"]["files"] == feed["eligible_total"] - feed["shown"]


async def test_nothing_is_omitted_once_the_cap_clears_the_repository(app, client):
    repo_id = await _seed(app, client)
    feed = await _feed(client, repo_id, cap=100)
    assert feed["omitted"] == {
        "files": 0,
        "performance_files": 0,
        "opportunities": 0,
        "observations": 0,
        "held": 0,
    }


async def test_rows_carry_the_burden_the_lens_rings_by(app, client):
    repo_id = await _seed(app, client)
    feed = await _feed(client, repo_id, cap=100)
    tiny = next(f for f in feed["files"] if f["file_path"] == "src/tiny.py")
    assert tiny["performance_opportunities"] >= 1
    assert tiny["performance_observations"] == 2
    assert tiny["performance_actionability"] in {"plan_ready", "advisory", "investigate"}
    big = next(f for f in feed["files"] if f["file_path"] == "src/big0.py")
    # Present and zero, never absent: absent is how the lens recognizes a
    # server with no read model, and it must not read that as a clear file.
    assert big["performance_opportunities"] == 0
    assert "performance_actionability" not in big


async def test_a_cause_outside_the_default_queue_reads_clear_on_the_map(app, client):
    """The lens colours what the performance headline leads with: the default queue."""
    repo_id = await _seed(app, client, queued=False)
    feed = await _feed(client, repo_id, cap=100)
    tiny = next(f for f in feed["files"] if f["file_path"] == "src/tiny.py")
    assert tiny["performance_opportunities"] == 0
    assert "performance_actionability" not in tiny
    # The repository block still counts every open cause.
    assert feed["performance"]["opportunities_total"] >= 1
    capped = await _feed(client, repo_id, cap=1)
    assert capped["selection"]["performance_shown"] == 0


async def test_a_file_with_only_expected_causes_reads_clear_on_the_map(
    app, client: AsyncClient
):
    """The map counts what the default queue shows; the repository rollup still counts all."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    metrics = [_metric("src/fs.py", 5), _metric("src/big.py", 500)]
    findings = [
        HealthFindingData(
            biomarker_type="io_in_loop",
            severity=Severity.MEDIUM,
            file_path="src/fs.py",
            function_name="run",
            line_start=1,
            line_end=1,
            details={
                "boundary_kind": "filesystem",
                "cross_function": True,
                "path": ["src/fs.py::run", "src/fs.py::read"],
                "resolution_basis": "reliable-edge",
            },
            health_impact=0.0,
            reason="A file is read for every loop iteration.",
            dimension="performance",
        )
    ]
    link_performance_findings(findings)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(session, repo_id, metrics)
        await crud.save_health_findings(session, repo_id, findings)
        await crud.finalize_performance_opportunities(
            session, repo_id, analyzed_commit="b" * 40
        )
        await session.commit()

    feed = await _feed(client, repo_id, cap=100)
    row = next(f for f in feed["files"] if f["file_path"] == "src/fs.py")
    assert row["performance_opportunities"] == 0
    assert "performance_actionability" not in row
    perf = feed["performance"]
    assert perf["actionability"]["expected"] == 1
    assert sum(perf["actionability"].values()) == perf["opportunities_total"]
    # Nor does it take a priority slot from the size sample.
    capped = await _feed(client, repo_id, cap=1)
    assert [f["file_path"] for f in capped["files"]] == ["src/big.py"]
    assert capped["selection"]["performance_shown"] == 0


async def test_the_repository_block_reports_what_the_lens_can_say(app, client):
    repo_id = await _seed(app, client)
    feed = await _feed(client, repo_id, cap=100)
    perf = feed["performance"]
    assert perf["files_with_opportunities"] == 1
    assert perf["opportunities_total"] >= 1
    assert sum(perf["actionability"].values()) == perf["opportunities_total"]
    assert perf["analyzed_commit"] == "a" * 40


async def test_an_unanalyzed_repository_reports_no_performance_block(app, client):
    repo = await create_test_repo(client)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(session, repo["id"], [_metric("src/a.py", 10)])
        await session.commit()
    feed = await _feed(client, repo["id"], cap=10)
    # Not an empty rollup: "never analyzed" and "analyzed and clear" are
    # different answers and the lens draws them differently.
    assert feed["performance"] is None


async def test_the_selection_is_stable_across_two_reads(app, client):
    repo_id = await _seed(app, client)
    first = await _feed(client, repo_id, cap=4)
    second = await _feed(client, repo_id, cap=4)
    assert [f["file_path"] for f in first["files"]] == [
        f["file_path"] for f in second["files"]
    ]


async def test_the_cap_is_bounded(app, client):
    repo_id = await _seed(app, client)
    assert (
        await client.get(f"/api/repos/{repo_id}/health/map", params={"cap": 99999})
    ).status_code == 422
    # A canvas holds eight thousand nodes, so the ceiling sits there.
    assert (
        await client.get(f"/api/repos/{repo_id}/health/map", params={"cap": 8000})
    ).status_code == 200


async def test_a_cause_on_an_unsizeable_file_is_not_reported_as_capped_out(app, client):
    """The omission counts describe what raising the cap would recover.

    A file with no lines cannot be drawn at any cap and cannot be pinned into
    the field either, so counting its cause under ``performance_files`` would
    attach it to a recovery that does not exist.
    """
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    metrics = [_metric("src/real.py", 100), _metric("src/empty.py", 0)]
    findings = [_perf_finding("src/empty.py", 5, ["src/empty.py::run", "src/db.py::fetch"])]
    link_performance_findings(findings)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(session, repo_id, metrics)
        await crud.save_health_findings(session, repo_id, findings)
        await crud.finalize_performance_opportunities(session, repo_id)
        await session.commit()

    feed = await _feed(client, repo_id, cap=100)
    assert feed["eligible_total"] == 1
    assert feed["omitted"]["performance_files"] == 0
    assert feed["selection"]["performance_eligible"] == 0
    # The cause still exists and the response still counts it repository-wide,
    # so it is absent from the field rather than absent from the product.
    assert feed["performance"]["files_with_opportunities"] == 1
    assert feed["performance"]["files_with_opportunities_eligible"] == 0


async def _seed_rows(app, client: AsyncClient, metrics: list[dict]) -> str:
    repo = await create_test_repo(client)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(session, repo["id"], metrics)
        await session.commit()
    return repo["id"]


async def test_vendored_and_generated_files_are_held_out_and_counted(app, client):
    repo_id = await _seed_rows(
        app,
        client,
        [
            _metric("src/app.py", 50, origin="production"),
            _metric("vendor/lib.js", 9000, "vendor", origin="vendored"),
            _metric("gen/api_pb2.py", 4000, "gen", origin="generated"),
        ],
    )
    feed = await _feed(client, repo_id, cap=100)
    assert [f["file_path"] for f in feed["files"]] == ["src/app.py"]
    # Not drawn, not counted as capped out: held, and the response says so.
    assert feed["eligible_total"] == 1
    assert feed["omitted"]["files"] == 0
    assert feed["omitted"]["held"] == 2

    everything = await _feed(client, repo_id, cap=100, include_held="true")
    assert everything["eligible_total"] == 3
    assert everything["omitted"]["held"] == 0
    assert everything["selection"]["include_held"] is True


async def test_a_pinned_vendored_file_is_drawn_even_while_held(app, client):
    repo_id = await _seed_rows(
        app,
        client,
        [_metric("src/app.py", 50), _metric("vendor/lib.js", 90, "vendor", origin="vendored")],
    )
    feed = await _feed(client, repo_id, cap=100, active="vendor/lib.js")
    assert "vendor/lib.js" in [f["file_path"] for f in feed["files"]]
    assert feed["omitted"]["held"] == 0


async def test_production_code_is_admitted_before_tests_and_examples(app, client):
    repo_id = await _seed_rows(
        app,
        client,
        [
            _metric("docs/example.py", 900, "docs", score=1.0, origin="docs_example"),
            _metric("tests/test_x.py", 800, "tests", score=1.0, origin="test"),
            _metric("scripts/build.py", 700, "scripts", score=1.0, origin="tooling"),
            _metric("src/a.py", 10, "src", origin="production"),
        ],
    )
    # A cap no larger than the floor's share leaves nothing for a floor, so the
    # tier order alone decides.
    feed = await _feed(client, repo_id, cap=4)
    assert [f["file_path"] for f in feed["files"]] == [
        "src/a.py",
        "scripts/build.py",
        "tests/test_x.py",
        "docs/example.py",
    ]
    assert feed["files"][0]["code_origin"] == "production"


async def test_an_unhealthy_file_outranks_a_bigger_clean_one(app, client):
    repo_id = await _seed_rows(
        app,
        client,
        [
            _metric("src/huge_clean.py", 5000, score=10.0),
            _metric("src/small_sick.py", 40, score=2.0),
            _metric("src/unscored.py", 9000, score=None),
        ],
    )
    feed = await _feed(client, repo_id, cap=3)
    # Debt first, the clean file next by size, and the file health could not
    # score last in its tier whatever its size.
    assert [f["file_path"] for f in feed["files"]] == [
        "src/small_sick.py",
        "src/huge_clean.py",
        "src/unscored.py",
    ]


async def test_a_small_module_keeps_a_floor_against_a_big_one(app, client):
    big = [_metric(f"core/f{i}.py", 1000, "core", score=2.0) for i in range(20)]
    small = [_metric("cli/main.py", 10, "cli", score=9.5)]
    repo_id = await _seed_rows(app, client, big + small)
    feed = await _feed(client, repo_id, cap=10)
    drawn = [f["file_path"] for f in feed["files"]]
    # The debt rank alone would spend all ten slots on the unhealthy module.
    assert "cli/main.py" in drawn
    assert feed["selection"]["module_floor_shown"] == 2
    assert feed["shown"] == 10


async def test_a_query_pins_the_files_whose_path_contains_it(app, client):
    rows = [_metric(f"src/f{i}.py", 1000 - i) for i in range(10)]
    rows.append(_metric("src/needle_here.py", 1))
    repo_id = await _seed_rows(app, client, rows)
    feed = await _feed(client, repo_id, cap=2, q="NEEDLE")
    assert "src/needle_here.py" in [f["file_path"] for f in feed["files"]]
    assert feed["selection"]["query_matches"] == 1
    assert feed["selection"]["query_shown"] == 1


async def test_rows_flag_the_hotspots_the_kpi_averages_over(app, client):
    # The hotspot health lens greys every file that is not a hotspot, so the
    # flag has to come from the same set ``hotspot_health`` averages over.
    repo_id = await _seed(app, client)
    async with app.state.session_factory() as session:
        await crud.upsert_git_metadata(
            session, repository_id=repo_id, file_path="src/big0.py", is_hotspot=True
        )
        await session.commit()
    feed = await _feed(client, repo_id, cap=100)
    flags = {f["file_path"]: f["is_hotspot"] for f in feed["files"]}
    assert flags["src/big0.py"] is True
    assert flags["src/tiny.py"] is False
