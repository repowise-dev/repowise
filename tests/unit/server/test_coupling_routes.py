"""/coupling filters, /coupling/areas and the area card's coupled areas over a seeded store."""

from __future__ import annotations

import json

from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.models import GraphEdge
from tests.unit.server.conftest import create_test_repo


def _partner(path: str, count: float, structural: str, support: int = 4) -> dict:
    return {
        "file_path": path,
        "co_change_count": count,
        "frequency": support,
        "self_commits": 10,
        "partner_commits": 10,
        "last_co_change": "2026-06-01",
        "structural": structural,
    }


PARTNERS = {
    "src/ui/a.ts": [
        _partner("src/web/b.ts", 3.0, "unexplained"),
        _partner("src/ui/c.ts", 2.0, "corroborated"),
        _partner("src/api/q.py", 2.5, "unexplained"),
    ],
    "src/core/x.py": [
        _partner("src/web/b.ts", 1.0, "corroborated", support=2),
        _partner("tests/test_x.py", 0.5, "unexplained"),
    ],
}


async def _seed(app, client: AsyncClient) -> str:
    repo_id = (await create_test_repo(client))["id"]
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                {
                    "file_path": path,
                    "score": 8.0,
                    "max_ccn": 1,
                    "max_nesting": 1,
                    "nloc": 10,
                    "duplication_pct": 0.0,
                    "has_test_file": False,
                    "line_coverage_pct": None,
                    "branch_coverage_pct": None,
                    "module": path.split("/")[1],
                }
                for path in ("src/ui/a.ts", "src/ui/c.ts", "src/web/b.ts", "src/core/x.py")
            ],
        )
        for path, partners in PARTNERS.items():
            await crud.upsert_git_metadata(
                session,
                repository_id=repo_id,
                file_path=path,
                co_change_partners_json=json.dumps(partners),
            )
        # web imports a different ui file: ui and web are linked as areas even
        # though the co-changing pair has no edge of its own.
        session.add(
            GraphEdge(
                repository_id=repo_id,
                source_node_id="src/web/b.ts",
                target_node_id="src/ui/c.ts",
                edge_type="imports",
            )
        )
        await session.commit()
    return repo_id


async def test_graph_filters_and_stamps_cross_area(app, client):
    repo_id = await _seed(app, client)
    url = f"/api/repos/{repo_id}/coupling"
    whole = (await client.get(url)).json()
    assert whole["total_edges"] == 5 and whole["depth"] == 2
    cross = {(e["source"], e["target"]): e["cross_area"] for e in whole["edges"]}
    assert cross[("src/ui/a.ts", "src/ui/c.ts")] is False
    assert cross[("src/ui/a.ts", "src/web/b.ts")] is True

    hidden = (
        await client.get(url, params={"unexplained": True, "exclude_tests": True})
    ).json()
    assert {(e["source"], e["target"]) for e in hidden["edges"]} == {
        ("src/ui/a.ts", "src/web/b.ts"),
        ("src/api/q.py", "src/ui/a.ts"),
    }
    assert hidden["total_edges"] == 2 and hidden["coupled_files"] == 3

    between = (
        await client.get(url, params={"area": "src/core", "partner": "src/web"})
    ).json()
    assert [(e["source"], e["target"]) for e in between["edges"]] == [
        ("src/core/x.py", "src/web/b.ts")
    ]
    assert (await client.get(url, params={"min_support": 3})).json()["total_edges"] == 4
    assert (await client.get(url, params={"partner": "src/web"})).status_code == 422


async def test_only_areas_with_no_dependency_between_them_are_hidden(app, client):
    repo_id = await _seed(app, client)
    out = (
        await client.get(
            f"/api/repos/{repo_id}/coupling/areas",
            params={"cross_area": True, "exclude_tests": True, "depth": 2},
        )
    ).json()
    assert out["depth"] == 2 and out["root"] == ""
    totals = out["totals"]
    assert (totals["area_pairs"], totals["unexplained_area_pairs"]) == (3, 2)
    assert (totals["hidden_area_pairs"], totals["unlabelled_pairs"]) == (1, 0)
    verdicts = {(p["source"], p["target"]): (p["linked"], p["hidden"]) for p in out["area_pairs"]}
    # ui and web import each other elsewhere: expected coupling, not a finding.
    assert verdicts[("src/ui", "src/web")] == (True, False)
    assert verdicts[("src/api", "src/ui")] == (False, True)
    # Hidden pairs rank first.
    assert (out["area_pairs"][0]["source"], out["area_pairs"][0]["target"]) == ("src/api", "src/ui")
    assert out["area_pairs"][0]["top_pairs"][0]["structural"] == "unexplained"
    missing = await client.get("/api/repos/nope/coupling/areas")
    assert missing.status_code == 404


async def test_area_card_lists_the_areas_it_changes_with(app, client):
    repo_id = await _seed(app, client)
    card = (await client.get(f"/api/repos/{repo_id}/areas/src/ui")).json()
    assert [
        (c["path"], c["unexplained_pairs"], c["linked"], c["hidden"]) for c in card["coupled_areas"]
    ] == [("src/web", 1, True, False), ("src/api", 1, False, True)]
    # The test partner is left out, so the core card lists web alone.
    core = (await client.get(f"/api/repos/{repo_id}/areas/src/core")).json()
    assert [c["path"] for c in core["coupled_areas"]] == ["src/web"]
