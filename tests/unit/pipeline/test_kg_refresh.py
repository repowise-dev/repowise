"""Tests for the incremental knowledge-graph refresh (#669).

``repowise update`` must rebuild the KG skeleton + curation when the graph
shape changed, skip the rebuild when the fingerprint is unchanged, and carry
forward the prior artifact's LLM-enriched prose instead of paying an LLM call
on index-only runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
from kg_fixtures import FakeSymbol, build_repo

from repowise.core.analysis.knowledge_graph import (
    KnowledgeGraphResult,
    compute_kg_fingerprint,
)
from repowise.core.pipeline.incremental import (
    _carry_forward_kg_enrichment,
    refresh_knowledge_graph,
)


def _repo():
    return build_repo(
        ["src/a.py", "src/b.py", "src/main.py", "tests/test_a.py"],
        tests={"tests/test_a.py"},
        entries={"src/main.py"},
        edges=[("src/main.py", "src/a.py"), ("src/a.py", "src/b.py")],
    )


def _write_kg_json(repo_path: Path, data: dict) -> Path:
    kg_path = repo_path / ".repowise" / "knowledge-graph.json"
    kg_path.parent.mkdir(parents=True, exist_ok=True)
    kg_path.write_text(json.dumps(data), encoding="utf-8")
    return kg_path


@pytest.mark.asyncio
async def test_skips_when_fingerprint_unchanged(tmp_path):
    repo = _repo()
    fp = compute_kg_fingerprint(repo.builder)
    _write_kg_json(tmp_path, {"nodes": [], "layers": [], "tour": []})

    result = await refresh_knowledge_graph(
        tmp_path,
        repo.parsed,
        repo.builder,
        repo.repo_structure,
        {},
        None,
        prior_fingerprint=fp,
        changed_paths=[],
    )

    assert result is None


@pytest.mark.asyncio
async def test_rebuilds_when_fingerprint_changed(tmp_path):
    repo = _repo()
    _write_kg_json(tmp_path, {"nodes": [], "layers": [], "tour": []})

    result = await refresh_knowledge_graph(
        tmp_path,
        repo.parsed,
        repo.builder,
        repo.repo_structure,
        {},
        None,
        prior_fingerprint="0000000000000000",
        changed_paths=[],
    )

    assert result is not None
    assert result.fingerprint == compute_kg_fingerprint(repo.builder)
    assert result.layers
    assert any(n["id"] == "file:src/main.py" for n in result.nodes)


@pytest.mark.asyncio
async def test_rebuilds_when_no_prior_fingerprint(tmp_path):
    repo = _repo()

    result = await refresh_knowledge_graph(
        tmp_path,
        repo.parsed,
        repo.builder,
        repo.repo_structure,
        {},
        None,
        prior_fingerprint=None,
        changed_paths=[],
    )

    assert result is not None
    assert result.layers


@pytest.mark.asyncio
async def test_prior_summaries_carried_via_artifact(tmp_path):
    repo = _repo()
    _write_kg_json(
        tmp_path,
        {
            "nodes": [{"id": "file:src/a.py", "summary": "Rich page-derived summary for a."}],
            "layers": [],
            "tour": [],
        },
    )

    result = await refresh_knowledge_graph(
        tmp_path,
        repo.parsed,
        repo.builder,
        repo.repo_structure,
        {},
        None,
        prior_fingerprint=None,
        changed_paths=[],
    )

    assert result is not None
    node = next(n for n in result.nodes if n["id"] == "file:src/a.py")
    assert node["summary"] == "Rich page-derived summary for a."


@pytest.mark.asyncio
async def test_changed_file_gets_the_floor_not_its_prior_summary(tmp_path):
    """A changed file's prior summary names its old code; it gets the floor a
    fresh index would write, while unchanged files keep theirs."""
    repo = _repo()
    _write_kg_json(
        tmp_path,
        {
            "nodes": [
                {"id": "file:src/a.py", "summary": "Defines the old parser."},
                {"id": "file:src/b.py", "summary": "Rich page-derived summary for b."},
            ],
            "layers": [],
            "tour": [],
        },
    )

    result = await refresh_knowledge_graph(
        tmp_path,
        repo.parsed,
        repo.builder,
        repo.repo_structure,
        {},
        None,
        prior_fingerprint=None,
        changed_paths=["src/a.py"],
    )

    assert result is not None
    summaries = {n["id"]: n["summary"] for n in result.nodes}
    assert summaries["file:src/a.py"]
    assert summaries["file:src/a.py"] != "Defines the old parser."
    assert summaries["file:src/b.py"] == "Rich page-derived summary for b."


@pytest.mark.asyncio
async def test_unchanged_shape_patches_only_the_changed_file_nodes(tmp_path):
    """The rebuild is skipped, but a changed file's node gets its own fields
    and floor again; other nodes and the layers stay as stored."""
    repo = _repo()
    fp = compute_kg_fingerprint(repo.builder)
    _write_kg_json(
        tmp_path,
        {
            "nodes": [
                {"id": "file:src/a.py", "filePath": "src/a.py", "summary": "Defines old()."},
                {"id": "file:src/b.py", "filePath": "src/b.py", "summary": "Kept for b."},
            ],
            "layers": [{"id": "layer:core", "name": "Core", "description": "Named."}],
            "tour": [],
        },
    )

    async def refresh(changed):
        return await refresh_knowledge_graph(
            tmp_path,
            repo.parsed,
            repo.builder,
            repo.repo_structure,
            {},
            None,
            prior_fingerprint=fp,
            changed_paths=changed,
        )

    result = await refresh(["src/a.py"])

    assert result is not None
    assert result.fingerprint == fp
    nodes = {n["id"]: n for n in result.nodes}
    assert nodes["file:src/a.py"]["summary"] not in ("", "Defines old().")
    assert "symbolCount" in nodes["file:src/a.py"]
    assert nodes["file:src/b.py"] == {
        "id": "file:src/b.py",
        "filePath": "src/b.py",
        "summary": "Kept for b.",
    }
    assert result.layers[0]["name"] == "Core"

    # Once the artifact holds the patched node, the same change moves nothing.
    _write_kg_json(tmp_path, result.to_dict())
    assert await refresh(["src/a.py"]) is None
    assert await refresh(["README.md"]) is None


def _a_nodes(kg) -> dict:
    nodes = {n["id"]: n for n in kg.nodes if n.get("filePath") == "src/a.py"}
    edges = sorted((e["source"], e["target"], e["type"]) for e in kg.edges if e["target"] in nodes)
    return {"nodes": nodes, "edges": edges, "entry_points": kg.project.get("entry_points")}


async def _seeded(tmp_path, repo, git_meta):
    """Write the artifact a full refresh produces; return its fingerprint."""
    kg = await refresh_knowledge_graph(
        tmp_path,
        repo.parsed,
        repo.builder,
        repo.repo_structure,
        git_meta,
        None,
        prior_fingerprint=None,
        changed_paths=[],
    )
    _write_kg_json(tmp_path, kg.to_dict())
    return compute_kg_fingerprint(repo.builder)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_moves", [False, True], ids=["patch", "entry-rebuild"])
async def test_unchanged_shape_matches_a_rebuild_for_the_changed_file(tmp_path, entry_moves):
    """A renamed symbol, a new owner and (rebuilt) a new entry point: the
    changed file's nodes, symbol edges and the entry points match a rebuild."""
    repo = _repo()
    fp = await _seeded(tmp_path, repo, {"src/a.py": {"primary_owner_name": "alice"}})
    pf = next(p for p in repo.parsed if p.file_info.path == "src/a.py")
    pf.symbols = [FakeSymbol(name="renamed")]
    pf.file_info.is_entry_point = entry_moves
    git_meta = {"src/a.py": {"primary_owner_name": "bob"}}

    async def refresh(prior):
        return await refresh_knowledge_graph(
            tmp_path,
            repo.parsed,
            repo.builder,
            repo.repo_structure,
            git_meta,
            None,
            prior_fingerprint=prior,
            changed_paths=["src/a.py"],
        )

    patched = await refresh(fp)
    rebuilt = await refresh("0000000000000000")

    assert patched is not None and patched.fingerprint == fp
    assert _a_nodes(patched) == _a_nodes(rebuilt)
    assert "function:src/a.py:renamed" in _a_nodes(patched)["nodes"]


def test_carry_forward_layer_names_and_tour():
    kg = KnowledgeGraphResult(
        nodes=[{"id": "file:src/a.py", "summary": ""}],
        layers=[
            {"id": "layer:core", "name": "core", "description": ""},
            {"id": "layer:new", "name": "new", "description": ""},
        ],
        tour=[],
    )
    prior = KnowledgeGraphResult(
        nodes=[{"id": "file:src/a.py", "summary": "Prior summary."}],
        layers=[
            {
                "id": "layer:core",
                "name": "Core Engine",
                "description": "The ingestion and analysis engine.",
            },
            # No description → deterministic-only, must NOT be carried.
            {"id": "layer:new", "name": "Renamed Without Enrichment", "description": ""},
            {"id": "layer:gone", "name": "Removed", "description": "Stale."},
        ],
        tour=[{"step": 1, "nodeId": "file:src/a.py"}],
    )

    _carry_forward_kg_enrichment(kg, prior)

    core = next(layer for layer in kg.layers if layer["id"] == "layer:core")
    assert core["name"] == "Core Engine"
    assert core["description"] == "The ingestion and analysis engine."
    new = next(layer for layer in kg.layers if layer["id"] == "layer:new")
    assert new["name"] == "new"  # structural rename wins over stale prose
    assert kg.nodes[0]["summary"] == "Prior summary."
    assert kg.tour == prior.tour  # empty tour adopts the prior one


def test_carry_forward_never_overwrites_fresh_fields():
    kg = KnowledgeGraphResult(
        nodes=[{"id": "file:src/a.py", "summary": "Fresh summary."}],
        layers=[{"id": "layer:core", "name": "core", "description": "Fresh description."}],
        tour=[{"step": 1, "nodeId": "file:src/a.py"}],
    )
    prior = KnowledgeGraphResult(
        nodes=[{"id": "file:src/a.py", "summary": "Old."}],
        layers=[{"id": "layer:core", "name": "Old Name", "description": "Old."}],
        tour=[{"step": 1, "nodeId": "file:src/b.py"}],
    )

    _carry_forward_kg_enrichment(kg, prior)

    assert kg.layers[0]["name"] == "core"
    assert kg.layers[0]["description"] == "Fresh description."
    assert kg.nodes[0]["summary"] == "Fresh summary."
    assert kg.tour[0]["nodeId"] == "file:src/a.py"
