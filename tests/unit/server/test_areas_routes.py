"""/areas/tree, /areas/{path} and /health/map?area= over a seeded store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.persistence import crud
from tests.unit.server.conftest import create_test_repo


def _metric(path: str, score: float, nloc: int) -> dict:
    return {
        "file_path": path,
        "score": score,
        "max_ccn": 3,
        "max_nesting": 2,
        "nloc": nloc,
        "duplication_pct": 0.0,
        "has_test_file": False,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "module": path.split("/")[0],
    }


async def _seed(app, client: AsyncClient) -> str:
    repo_id = (await create_test_repo(client))["id"]
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                _metric("src/core/a.py", 3.0, 100),
                _metric("src/core/b.py", 9.0, 100),
                _metric("src/ui/c.py", 8.0, 50),
                _metric("docs/d.py", 10.0, 5),
            ],
        )
        await crud.save_health_findings(
            session,
            repo_id,
            [
                HealthFindingData(
                    biomarker_type="complex_method",
                    severity=Severity.HIGH,
                    file_path="src/core/a.py",
                    function_name="run",
                    line_start=3,
                    line_end=30,
                    details={},
                    health_impact=2.0,
                    reason="run branches a lot",
                )
            ],
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/core/a.py",
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            is_hotspot=True,
            bus_factor=1,
        )
        for days_ago, a_score in ((31, 2.0), (0, 3.0)):
            await crud.save_health_snapshot(
                session,
                repo_id,
                hotspot_health=None,
                average_health=7.0,
                worst_performer_path=None,
                worst_performer_score=None,
                per_file_scores={"src/core/a.py": a_score, "src/core/b.py": 9.0},
                taken_at=now - timedelta(days=days_ago),
            )
        await session.commit()
    return repo_id


async def test_tree_unfolds_depth_levels_below_the_root(app, client):
    repo_id = await _seed(app, client)
    one = (await client.get(f"/api/repos/{repo_id}/areas/tree", params={"depth": 1})).json()
    assert one["root"]["file_count"] == 4
    assert [n["path"] for n in one["nodes"]] == ["docs", "src"]
    two = (await client.get(f"/api/repos/{repo_id}/areas/tree", params={"depth": 2})).json()
    assert [n["path"] for n in two["nodes"]] == ["docs", "src", "src/core", "src/ui"]
    scoped = (
        await client.get(f"/api/repos/{repo_id}/areas/tree", params={"root": "src/"})
    ).json()
    assert scoped["root"]["path"] == "src"
    core = next(n for n in scoped["nodes"] if n["path"] == "src/core")
    assert core["health"] == 6.0
    assert core["hotspot_count"] == 1 and core["bus_factor_1_count"] == 1
    assert core["primary_owner"]["name"] == "Alice"
    assert core["findings"]["high"] == 1
    # Snapshot weights are the current NLOC: (3*100 + 9*100)/200 - (2*100 + 9*100)/200.
    assert core["health_delta_30d"] == 0.5


async def test_detail_and_unknown_areas(app, client):
    repo_id = await _seed(app, client)
    detail = (await client.get(f"/api/repos/{repo_id}/areas/src/core")).json()
    assert detail["area"]["path"] == "src/core"
    assert detail["worst_files"][0]["path"] == "src/core/a.py"
    assert [p["health"] for p in detail["trend"]] == [5.5, 6.0]
    assert detail["top_findings"][0]["biomarker_type"] == "complex_method"
    root = (await client.get(f"/api/repos/{repo_id}/areas")).json()
    assert root["area"]["file_count"] == 4
    missing = await client.get(f"/api/repos/{repo_id}/areas/tree", params={"root": "nope"})
    assert missing.status_code == 404
    assert (await client.get(f"/api/repos/{repo_id}/areas/nope")).status_code == 404
    assert (await client.get("/api/repos/no-such-repo/areas/tree")).status_code == 404


async def test_map_narrows_to_the_area_and_counts_it(app, client):
    repo_id = await _seed(app, client)
    feed = (
        await client.get(f"/api/repos/{repo_id}/health/map", params={"area": "src/core"})
    ).json()
    assert feed["area"] == "src/core"
    assert sorted(f["file_path"] for f in feed["files"]) == ["src/core/a.py", "src/core/b.py"]
    assert feed["repository_total"] == 2
    whole = (await client.get(f"/api/repos/{repo_id}/health/map")).json()
    assert whole["area"] is None and whole["repository_total"] == 4


async def test_a_new_snapshot_moves_the_cached_delta(app, client):
    repo_id = await _seed(app, client)
    url = f"/api/repos/{repo_id}/areas/tree"
    before = (await client.get(url, params={"root": "src"})).json()
    assert before["nodes"][0]["health_delta_30d"] == 0.5
    async with app.state.session_factory() as session:
        await crud.save_health_snapshot(
            session,
            repo_id,
            hotspot_health=None,
            average_health=7.0,
            worst_performer_path=None,
            worst_performer_score=None,
            per_file_scores={"src/core/a.py": 4.0, "src/core/b.py": 9.0},
        )
        await session.commit()
    after = (await client.get(url, params={"root": "src"})).json()
    assert after["nodes"][0]["health_delta_30d"] == 1.0


async def test_a_warmed_model_serves_the_next_read_without_a_build(app, client, monkeypatch):
    from repowise.server.services import areas as service

    repo_id = await _seed(app, client)
    await service.warm_area_model(app.state.session_factory, repo_id)

    async def no_build(*_args):
        raise AssertionError("the warm-up should have built the model")

    monkeypatch.setattr(service, "_build", no_build)
    # The warm-up builds the map's default view, the one the first reader opens.
    tree = await client.get(f"/api/repos/{repo_id}/areas/tree", params={"hold": "map"})
    assert tree.status_code == 200


async def _seed_origins(app, client: AsyncClient) -> str:
    """Production code beside tests, a build file and a generated file."""
    repo_id = (await create_test_repo(client))["id"]
    rows = [
        {**_metric("src/a.py", 4.0, 100), "code_origin": "production"},
        {**_metric("src/b.py", 8.0, 300), "code_origin": "production"},
        {**_metric("tests/test_a.py", 10.0, 200), "code_origin": "test", "is_test": True},
        {**_metric("setup.py", 2.0, 40), "code_origin": "build"},
        {**_metric("src/gen_pb2.py", 1.0, 400), "code_origin": "generated"},
    ]
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(session, repo_id, rows)
        await session.commit()
    return repo_id


async def test_hold_map_leaves_the_held_origins_out_of_every_figure(app, client):
    repo_id = await _seed_origins(app, client)
    tree = (await client.get(f"/api/repos/{repo_id}/areas/tree", params={"hold": "map", "depth": 2})).json()
    root = tree["root"]
    # Only the two production files: (4*100 + 8*300) / 400 = 7.0.
    assert root["file_count"] == 2
    assert root["health"] == 7.0
    # A folder holding only tests is simply not there.
    assert "tests" not in {n["path"] for n in tree["nodes"]}
    held = tree["held"]
    assert held["excluded"] == sorted(held["default"])
    assert held["counts"]["test"] == 1 and held["counts"]["build"] == 1 and held["counts"]["generated"] == 1
    feed = (await client.get(f"/api/repos/{repo_id}/health/map", params={"hold": "map"})).json()
    assert {f["file_path"] for f in feed["files"]} == {"src/a.py", "src/b.py"}


async def test_include_brings_one_held_origin_back(app, client):
    repo_id = await _seed_origins(app, client)
    params = {"hold": "map", "include": "test", "depth": 2}
    tree = (await client.get(f"/api/repos/{repo_id}/areas/tree", params=params)).json()
    assert tree["root"]["file_count"] == 3
    assert "test" not in tree["held"]["excluded"]
    detail = (await client.get(f"/api/repos/{repo_id}/areas/tests", params=params)).json()
    assert detail["area"]["file_count"] == 1


async def test_without_hold_the_root_equals_the_page_headline(app, client):
    # The page's "All code" scope and the map's everything-included view are
    # the same files, so the root and the headline are one number.
    repo_id = await _seed_origins(app, client)
    tree = (await client.get(f"/api/repos/{repo_id}/areas/tree")).json()
    overview = (await client.get(f"/api/repos/{repo_id}/health/overview", params={"limit": 1})).json()
    assert tree["root"]["health"] == overview["summary"]["average_health"]
    assert tree["held"]["excluded"] == []


async def test_bringing_every_held_group_back_is_the_whole_repository(app, client):
    repo_id = await _seed_origins(app, client)
    whole = (await client.get(f"/api/repos/{repo_id}/areas/tree")).json()
    params = {"hold": "map", "include": ",".join(whole["held"]["default"])}
    back = (await client.get(f"/api/repos/{repo_id}/areas/tree", params=params)).json()
    overview = (await client.get(f"/api/repos/{repo_id}/health/overview", params={"limit": 1})).json()
    assert back["root"]["health"] == whole["root"]["health"] == overview["summary"]["average_health"]
    assert back["held"]["excluded"] == []


async def test_every_group_back_draws_generated_files_too(app, client):
    # With every held group brought back the set is empty, and the map must
    # still read that as "hold nothing", not as the default vendored hold.
    repo_id = await _seed_origins(app, client)
    whole = (await client.get(f"/api/repos/{repo_id}/areas/tree")).json()
    params = {"hold": "map", "include": " TEST, " + ",".join(whole["held"]["default"])}
    feed = (await client.get(f"/api/repos/{repo_id}/health/map", params=params)).json()
    assert "src/gen_pb2.py" in {f["file_path"] for f in feed["files"]}
    assert feed["omitted"]["held"] == 0
