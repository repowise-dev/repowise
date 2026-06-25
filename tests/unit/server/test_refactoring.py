"""HTTP-level tests for /api/repos/{repo_id}/refactoring/{targets,:id}."""

from __future__ import annotations

import tempfile
from pathlib import Path

from httpx import AsyncClient

from repowise.core.persistence import (
    batch_upsert_graph_edges,
    batch_upsert_graph_nodes,
)
from repowise.core.persistence import crud


async def create_test_repo(client: AsyncClient) -> dict:
    repo_dir = Path(tempfile.mkdtemp()) / "test-repo"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    resp = await client.post(
        "/api/repos",
        json={
            "name": "test-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/test-repo",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _seed(client: AsyncClient, app) -> str:
    """A repo + a tiny graph (for centrality) + four refactoring plans, one of
    each type. ``hub.py`` is imported by two files so it is the central one."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]

    async with app.state.session_factory() as session:
        await batch_upsert_graph_nodes(session, repo_id, [
            {"node_id": "pkg/hub.py", "node_type": "file", "language": "python", "symbol_count": 9},
            {"node_id": "pkg/leaf.py", "node_type": "file", "language": "python", "symbol_count": 3},
            {"node_id": "pkg/a.py", "node_type": "file", "language": "python", "symbol_count": 2},
            {"node_id": "pkg/b.py", "node_type": "file", "language": "python", "symbol_count": 2},
        ])
        # hub.py has in-degree 2 (a, b import it); leaf.py has in-degree 0.
        await batch_upsert_graph_edges(session, repo_id, [
            {"source_node_id": "pkg/a.py", "target_node_id": "pkg/hub.py", "edge_type": "imports"},
            {"source_node_id": "pkg/b.py", "target_node_id": "pkg/hub.py", "edge_type": "imports"},
        ])
        await crud.save_refactoring_suggestions(session, repo_id, [
            {
                "refactoring_type": "extract_class",
                "file_path": "pkg/leaf.py",
                "target_symbol": "GodClass",
                "line_start": 1,
                "line_end": 200,
                "plan": {"groups": [
                    {"name": None, "methods": ["a", "b"], "fields": ["x"]},
                    {"name": None, "methods": ["c", "d"], "fields": ["y"]},
                ]},
                "evidence": {"lcom4": 2, "method_count": 6, "field_count": 2, "wmc": 40},
                "impact_delta": 2.5,
                "effort_bucket": "L",
                "blast_radius": {"dependents_count": 0},
                "confidence": "high",
                "source_biomarker": "low_cohesion",
            },
            {
                "refactoring_type": "extract_helper",
                "file_path": "pkg/hub.py",
                "target_symbol": "dup_block",
                "line_start": 10,
                "line_end": 30,
                "plan": {
                    "occurrences": [
                        {"file": "pkg/hub.py", "line_start": 10, "line_end": 30},
                        {"file": "pkg/a.py", "line_start": 5, "line_end": 25},
                    ],
                    "suggested_site": {"module": "pkg", "directory": "pkg"},
                    "duplicated_lines": 20,
                },
                "evidence": {"occurrence_count": 2, "duplicated_lines": 20, "co_change_count": 4},
                "impact_delta": 0.6,
                "effort_bucket": "M",
                "blast_radius": {"files": ["pkg/hub.py", "pkg/a.py"], "file_count": 2, "co_change_count": 4},
                "confidence": "high",
                "source_biomarker": "dry_violation",
            },
            {
                "refactoring_type": "move_method",
                "file_path": "pkg/a.py",
                "target_symbol": "Helper.do_work",
                "line_start": 40,
                "line_end": 60,
                "plan": {"method": "do_work", "from_class": "Helper", "to_class": "Worker", "to_file": "pkg/b.py"},
                "evidence": {"foreign_calls": 3, "own_calls": 0, "own_distance": 0.95, "target_distance": 0.4},
                "impact_delta": 0.0,
                "effort_bucket": "S",
                "blast_radius": {"callers": 1, "files": ["pkg/a.py", "pkg/b.py"]},
                "confidence": "medium",
                "source_biomarker": "",
            },
            {
                "refactoring_type": "break_cycle",
                "file_path": "pkg/a.py",
                "target_symbol": "pkg/a.py↔pkg/b.py",
                "line_start": None,
                "line_end": None,
                "plan": {"cycle": ["pkg/a.py", "pkg/b.py"], "cut_edges": [{"from": "pkg/a.py", "to": "pkg/b.py"}]},
                "evidence": {"cycle_size": 2, "edge_count": 2, "cut_count": 1},
                "impact_delta": 0.0,
                "effort_bucket": "M",
                "blast_radius": {"files": ["pkg/a.py", "pkg/b.py"], "file_count": 2},
                "confidence": "medium",
                "source_biomarker": "",
            },
        ])
        await session.commit()
    return repo_id


async def test_targets_returns_ranked_plans_and_summary(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    resp = await client.get(f"/api/repos/{repo_id}/refactoring/targets")
    assert resp.status_code == 200
    body = resp.json()

    assert body["summary"]["total"] == 4
    assert {c["type"]: c["count"] for c in body["summary"]["by_type"]} == {
        "extract_class": 1,
        "extract_helper": 1,
        "move_method": 1,
        "break_cycle": 1,
    }

    plans = body["plans"]
    assert len(plans) == 4
    # Every plan carries its id, re-hydrated dicts, and a rank score.
    for p in plans:
        assert p["id"]
        assert isinstance(p["plan"], dict)
        assert p["rank_score"] > 0
    # Ranked, not raw DB order: the highest rank_score is first.
    scores = [p["rank_score"] for p in plans]
    assert scores == sorted(scores, reverse=True)


async def test_type_filter_narrows_plans_but_keeps_summary(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    resp = await client.get(
        f"/api/repos/{repo_id}/refactoring/targets",
        params={"refactoring_type": "break_cycle"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [p["refactoring_type"] for p in body["plans"]] == ["break_cycle"]
    # Summary still reflects all four types so the chips can show totals.
    assert body["summary"]["total"] == 4


async def test_plan_detail_by_id(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    listed = (await client.get(f"/api/repos/{repo_id}/refactoring/targets")).json()["plans"]
    target = next(p for p in listed if p["refactoring_type"] == "break_cycle")

    resp = await client.get(f"/api/repos/{repo_id}/refactoring/{target['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == target["id"]
    assert detail["plan"]["cut_edges"] == [{"from": "pkg/a.py", "to": "pkg/b.py"}]


async def test_plan_detail_unknown_id_404(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    resp = await client.get(f"/api/repos/{repo_id}/refactoring/deadbeef")
    assert resp.status_code == 404


async def test_centrality_breaks_ties_in_ranking(client: AsyncClient, app) -> None:
    """Two identical plans differing only by file centrality: the one on the
    high-in-degree (hub) file must rank first. Exercises the list endpoint's
    materialized graph_metrics → centrality path."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with app.state.session_factory() as session:
        await crud.batch_upsert_graph_metrics(session, repo_id, {
            "pkg/hub.py": {"in_degree": 25, "out_degree": 1},
            "pkg/leaf.py": {"in_degree": 0, "out_degree": 1},
        })
        # Same type, effort, confidence, impact, blast — only the file differs.
        common = {
            "refactoring_type": "extract_class",
            "target_symbol": "C",
            "line_start": 1,
            "line_end": 50,
            "plan": {"groups": [{"name": None, "methods": ["a"], "fields": ["x"]}]},
            "evidence": {"lcom4": 2},
            "impact_delta": 1.0,
            "effort_bucket": "M",
            "blast_radius": {"dependents_count": 0},
            "confidence": "medium",
            "source_biomarker": "low_cohesion",
        }
        await crud.save_refactoring_suggestions(session, repo_id, [
            {**common, "file_path": "pkg/leaf.py"},
            {**common, "file_path": "pkg/hub.py"},
        ])
        await session.commit()

    body = (await client.get(f"/api/repos/{repo_id}/refactoring/targets")).json()
    files = [p["file_path"] for p in body["plans"]]
    assert files == ["pkg/hub.py", "pkg/leaf.py"]
    assert body["plans"][0]["rank_score"] > body["plans"][1]["rank_score"]


async def test_min_confidence_filters(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    resp = await client.get(
        f"/api/repos/{repo_id}/refactoring/targets",
        params={"min_confidence": "high"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Only the two high-confidence plans survive (extract_class, extract_helper).
    assert body["summary"]["total"] == 2
    assert {p["refactoring_type"] for p in body["plans"]} == {"extract_class", "extract_helper"}


# ---------------------------------------------------------------------------
# POST /generate-code (opt-in LLM enrichment)
# ---------------------------------------------------------------------------


async def _seed_enrich_repo(client: AsyncClient, app, *, enabled: bool) -> tuple[str, str]:
    """A repo with a real checkout (config + one source file) and one plan.

    Uses the ``mock`` provider so ``build_enrichment_provider`` resolves a
    MockProvider with no API key. Returns ``(repo_id, suggestion_id)``."""
    repo_dir = Path(tempfile.mkdtemp()) / "enrich-repo"
    (repo_dir / "pkg").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    (repo_dir / ".repowise").mkdir(exist_ok=True)
    cfg = "provider: mock\n"
    if enabled:
        cfg += "refactoring:\n  llm:\n    enabled: true\n"
    (repo_dir / ".repowise" / "config.yaml").write_text(cfg, encoding="utf-8")
    (repo_dir / "pkg" / "leaf.py").write_text(
        "class GodClass:\n" + "".join(f"    def m{i}(self):\n        return {i}\n" for i in range(6)),
        encoding="utf-8",
    )

    resp = await client.post(
        "/api/repos",
        json={
            "name": "enrich-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/enrich-repo",
        },
    )
    assert resp.status_code == 201
    repo_id = resp.json()["id"]

    async with app.state.session_factory() as session:
        await crud.save_refactoring_suggestions(session, repo_id, [
            {
                "refactoring_type": "extract_class",
                "file_path": "pkg/leaf.py",
                "target_symbol": "GodClass",
                "line_start": 1,
                "line_end": 12,
                "plan": {"groups": [{"name": None, "methods": ["m0"], "fields": []}]},
                "evidence": {"lcom4": 2, "method_count": 6, "field_count": 0, "wmc": 6},
                "impact_delta": 2.5,
                "effort_bucket": "L",
                "blast_radius": {"dependents_count": 0},
                "confidence": "high",
                "source_biomarker": "low_cohesion",
            },
        ])
        await session.commit()
        rows = await crud.get_refactoring_suggestions(session, repo_id)
        suggestion_id = rows[0].id
    return repo_id, suggestion_id


async def test_generate_code_happy_path(client: AsyncClient, app) -> None:
    repo_id, sid = await _seed_enrich_repo(client, app, enabled=True)
    resp = await client.post(f"/api/repos/{repo_id}/refactoring/{sid}/generate-code", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["refactoring_type"] == "extract_class"
    assert body["target_symbol"] == "GodClass"
    assert body["provider"] == "mock"
    assert body["content"]  # the mock returned something
    assert body["spans"] and body["spans"][0]["file"] == "pkg/leaf.py"


async def test_generate_code_disabled_returns_403(client: AsyncClient, app) -> None:
    repo_id, sid = await _seed_enrich_repo(client, app, enabled=False)
    resp = await client.post(f"/api/repos/{repo_id}/refactoring/{sid}/generate-code", json={})
    assert resp.status_code == 403


async def test_generate_code_unknown_id_404(client: AsyncClient, app) -> None:
    repo_id, _ = await _seed_enrich_repo(client, app, enabled=True)
    resp = await client.post(f"/api/repos/{repo_id}/refactoring/deadbeef/generate-code", json={})
    assert resp.status_code == 404
