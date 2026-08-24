"""Sealed contract tests for get_risk relationship meanings."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from repowise.core.persistence.models import GitMetadata, GraphEdge, GraphNode
from repowise.core.workspace.registry import RepoContext
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._enrichment import CrossRepoEnricher

_FIXTURE = Path(__file__).parents[3] / "fixtures" / "mcp" / "risk_relationships.json"
_TEST_IMPACT_FIXTURE = Path(__file__).parents[3] / "fixtures" / "mcp" / "pr_test_impact.json"


class _SealedRegistry:
    def __init__(self, context: RepoContext) -> None:
        self._context = context

    def resolve_repo_param(self, repo: str | None = None) -> str:
        return repo or self._context.alias

    async def get(self, alias: str) -> RepoContext:
        assert alias == self._context.alias
        return self._context


@pytest.fixture
async def relationship_payload(setup_mcp, factory, tmp_path):
    """Exact external payload from the sealed local + workspace relationship graph."""
    from sqlalchemy import delete, select

    from repowise.server.mcp_server import get_risk

    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    target = fixture["target"]
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        f"exclude_patterns:\n  - {fixture['excluded_path']}\n", encoding="utf-8"
    )

    async with factory() as session:
        await session.execute(delete(GraphEdge).where(GraphEdge.repository_id == setup_mcp))
        await session.execute(delete(GraphNode).where(GraphNode.repository_id == setup_mcp))
        for index, node_id in enumerate(fixture["nodes"]):
            session.add(
                GraphNode(
                    id=f"sealed-node-{index}",
                    repository_id=setup_mcp,
                    node_id=node_id,
                    node_type="file",
                    language="python",
                    pagerank=0.01 + index / 1000,
                )
            )
        for index, edge in enumerate(fixture["edges"]):
            session.add(
                GraphEdge(
                    id=f"sealed-edge-{index}",
                    repository_id=setup_mcp,
                    source_node_id=edge["source"],
                    target_node_id=edge["target"],
                    edge_type=edge["type"],
                    imported_names_json="[]",
                )
            )
        meta = (
            await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == setup_mcp,
                    GitMetadata.file_path == target,
                )
            )
        ).scalar_one()
        meta.co_change_partners_json = json.dumps(fixture["co_changes"])
        await session.commit()

    cross_path = tmp_path / "cross_repo_edges.json"
    contracts_path = tmp_path / "contracts.json"
    cross_path.write_text(json.dumps(fixture["cross_repo"]), encoding="utf-8")
    contracts_path.write_text(json.dumps(fixture["contracts"]), encoding="utf-8")
    context = RepoContext(
        alias="alpha",
        path=tmp_path,
        session_factory=_state._session_factory,
        fts=_state._fts,
        vector_store=_state._vector_store,
        decision_store=_state._decision_store,
        vector_store_ready=_state._vector_store_ready or asyncio.Event(),
        _engine=None,
    )
    previous = (_state._registry, _state._cross_repo_enricher)
    _state._registry = _SealedRegistry(context)
    _state._cross_repo_enricher = CrossRepoEnricher(cross_path, contracts_path)
    try:
        yield {
            "normal": await get_risk([target], repo="alpha", include=["graph"]),
            "pr": await get_risk([target], repo="alpha", changed_files=[target], include=["graph"]),
        }
    finally:
        _state._registry, _state._cross_repo_enricher = previous


def _card(payload: dict) -> dict:
    external = payload.get("normal", payload)
    return external["targets"]["src/auth/service.py"]


@pytest.mark.asyncio
async def test_directed_dependents_preserve_direction_distance_and_structural_claim(
    relationship_payload,
):
    card = _card(relationship_payload)
    rows = {row["file_path"]: row for row in card["dependents"]}

    assert "src/direct_0.py" in rows
    assert rows["src/direct_0.py"]["direct"] is True
    assert rows["src/direct_0.py"]["distance"] == 1
    assert rows["src/direct_0.py"]["evidence_kind"] == "structural"
    assert rows["src/direct_0.py"]["claim"] == "structural_reach"
    assert card["relationship_analysis"]["dependencies"]["runtime_breakage_claim"] is False
    assert "src/db/models.py" not in rows  # target depends on it: reverse direction
    assert "docs/cochange-only.md" not in rows  # temporal graph edge is not structural
    assert "src/unknown.py" not in rows  # untyped graph edge is not structural
    assert card["direct_dependents_total"] == 6
    assert card["transitive_dependents_total"] == 1
    assert "6 direct dependents" in card["risk_summary"]


@pytest.mark.asyncio
async def test_transitive_dependency_and_totals_are_exact_after_exclusion(
    relationship_payload,
):
    card = _card(relationship_payload)
    transitive = next(row for row in card["dependents"] if row["file_path"] == "src/transitive.py")

    assert transitive["direct"] is False
    assert transitive["distance"] == 2
    assert transitive["relationship_types"] == ["transitive_dependency"]
    assert transitive["via"] == "src/direct_0.py"
    assert card["dependents_count"] == 6
    assert card["dependents_total"] == 7
    assert card["dependents_emitted"] == len(card["dependents"]) == 5
    assert card["dependents_truncated"] is True
    assert all(row["file_path"] != "src/excluded.py" for row in card["dependents"])
    assert "src/excluded_child.py" not in {row["file_path"] for row in card["dependents"]}
    assert all(
        edge["source"] != "src/excluded.py" and edge["target"] != "src/excluded.py"
        for row in card["dependents"]
        for edge in row["path"]
    )
    assert card["impact_surface_total"] == 7
    assert card["impact_surface_emitted"] == len(card["impact_surface"]) == 3
    assert card["impact_surface_truncated"] is True


@pytest.mark.asyncio
async def test_co_change_is_historical_only_and_mixed_evidence_stays_separate(
    relationship_payload,
):
    card = _card(relationship_payload)
    partners = {row["file_path"]: row for row in card["co_change_partners"]}
    dependent_paths = {row["file_path"] for row in card["dependents"]}
    consumer_paths = {row["consumer_file"] for row in card["consumers"]}

    only = partners["docs/cochange-only.md"]
    assert only["relationship_type"] == "co_change"
    assert only["evidence_kind"] == "historical"
    assert only["provenance"] == "git_history"
    assert only["support"] == 7
    assert only["has_structural_link"] is False
    assert only["file_path"] not in dependent_paths | consumer_paths

    mixed = partners["src/direct_0.py"]
    assert mixed["has_structural_link"] is True
    assert mixed["structural_relationship_types"] == ["imports"]
    assert card["co_change_partners_total"] == 6
    assert card["co_change_partners_emitted"] == len(card["co_change_partners"]) == 5
    assert card["co_change_partners_truncated"] is True
    assert "src/excluded.py" not in partners


@pytest.mark.asyncio
async def test_typed_consumers_do_not_absorb_dependents_or_co_changes(relationship_payload):
    card = _card(relationship_payload)
    test_impact_contract = json.loads(_TEST_IMPACT_FIXTURE.read_text(encoding="utf-8"))[
        "relationship_semantics"
    ]["contract"]

    assert card["consumers_total"] == 2
    assert card["consumers_emitted"] == len(card["consumers"]) == 2
    assert card["consumers_truncated"] is False
    consumer = next(row for row in card["consumers"] if row["consumer_repository"] == "beta")
    assert consumer["provider_repository"] == "alpha"
    assert consumer["consumer_repository"] == "beta"
    assert consumer["direction"] == "provider_to_consumer"
    assert consumer["contract_type"] == "http"
    assert consumer["provider_repository"] == test_impact_contract["provider_repo"]
    assert consumer["consumer_repository"] == test_impact_contract["consumer_repo"]
    assert consumer["contract_type"] == test_impact_contract["type"]
    assert consumer["evidence_kind"] == "contract"
    assert {row["consumer_file"] for row in card["consumers"]} == {
        "src/auth/service.py",
        "src/local_consumer.py",
    }
    assert card["dependents_count"] == card["direct_dependents_total"] == 6


@pytest.mark.asyncio
async def test_cross_repo_links_keep_identity_direction_type_and_exact_totals(
    relationship_payload,
):
    card = _card(relationship_payload)
    links = card["cross_repo_links"]

    assert card["cross_repo_links_total"] == 9
    assert card["cross_repo_links_emitted"] == len(links) == 5
    assert card["cross_repo_links_truncated"] is True
    assert all(
        not (row.get("source_repository") == "alpha" and row.get("target_repository") == "alpha")
        for row in links
    )
    provider = next(row for row in links if row["relationship_type"] == "contract_consumer")
    assert (provider["provider_repository"], provider["consumer_repository"]) == (
        "alpha",
        "beta",
    )
    assert (provider["provider_file"], provider["consumer_file"]) == (
        "src/auth/service.py",
        "src/auth/service.py",
    )
    reverse = next(row for row in links if row["relationship_type"] == "contract_provider")
    assert (reverse["provider_repository"], reverse["consumer_repository"]) == (
        "gamma",
        "alpha",
    )
    co_change = next(row for row in links if row["relationship_type"] == "co_change")
    assert co_change["direction"] == "undirected"
    assert co_change["evidence_kind"] == "historical"
    assert co_change["source_repository"] != co_change["target_repository"]
    package = next(row for row in links if row["relationship_type"] == "package_dependency")
    assert (package["source_repository"], package["target_repository"]) == ("delta", "alpha")
    assert package["direction"] == "consumer_to_dependency"
    assert package["granularity"] == "repository"
    assert package["target_file"] is None
    assert card["relationship_analysis"]["cross_repo"]["status"] == "partial"
    assert (
        card["relationship_analysis"]["cross_repo"]["sources"]["cross_repo_overlay"][
            "source_truncated"
        ]
        is True
    )


@pytest.mark.asyncio
async def test_legacy_fields_are_labeled_and_do_not_contradict_typed_fields(
    relationship_payload,
):
    card = _card(relationship_payload)
    legacy = card["cross_repo_impact"]

    assert legacy["cross_repo_consumers_semantics"] == "historical_co_change_partners"
    assert legacy["cross_repo_consumers_deprecated"] is True
    assert legacy["cross_repo_consumers_total"] == 6
    assert legacy["cross_repo_consumers_emitted"] == len(legacy["cross_repo_consumers"]) == 5
    assert legacy["cross_repo_consumers_truncated"] is True
    assert all(
        row["relationship_type"] == "co_change" and row["evidence_kind"] == "historical"
        for row in legacy["cross_repo_consumers"]
    )
    assert legacy["contract_consumers_total"] == 1
    assert card["consumers_total"] == 2
    assert all(row["relationship_type"] == "contract_consumer" for row in card["consumers"])
    assert card["dependents_count"] == 6


@pytest.mark.asyncio
async def test_pr_exclusion_stops_traversal_before_nonexcluded_child(relationship_payload):
    blast = relationship_payload["pr"]["pr_blast_radius"]
    paths = {row["path"] for row in blast["transitive_affected"]}

    assert "src/excluded.py" not in paths
    assert "src/excluded_child.py" not in paths
    assert blast["transitive_affected_total"] == len(blast["transitive_affected"])


@pytest.mark.asyncio
async def test_pr_mode_updates_co_change_emitted_count_after_its_second_cap(setup_mcp, factory):
    from sqlalchemy import select

    from repowise.server.mcp_server import get_risk

    async with factory() as session:
        meta = (
            await session.execute(
                select(GitMetadata).where(GitMetadata.file_path == "src/auth/service.py")
            )
        ).scalar_one()
        partners = json.loads(meta.co_change_partners_json)
        partners.extend(
            {"file_path": f"src/history_{index}.py", "count": 10 - index} for index in range(4)
        )
        meta.co_change_partners_json = json.dumps(partners)
        await session.commit()

    payload = await get_risk(["src/auth/service.py"], changed_files=["src/auth/service.py"])
    card = _card(payload)
    assert card["co_change_partners_total"] == 6
    assert card["co_change_partners_emitted"] == len(card["co_change_partners"]) == 3
    assert card["co_change_partners_truncated"] is True


@pytest.mark.asyncio
async def test_valid_empty_workspace_analysis_is_not_reported_as_unknown(setup_mcp, tmp_path):
    from repowise.server.mcp_server import get_risk

    cross_path = tmp_path / "cross_repo_edges.json"
    contracts_path = tmp_path / "contracts.json"
    cross_path.write_text(
        json.dumps({"version": 2, "co_changes": [], "package_deps": []}),
        encoding="utf-8",
    )
    contracts_path.write_text(
        json.dumps({"version": 2, "contracts": [], "contract_links": []}),
        encoding="utf-8",
    )
    context = RepoContext(
        alias="alpha",
        path=tmp_path,
        session_factory=_state._session_factory,
        fts=_state._fts,
        vector_store=_state._vector_store,
        decision_store=_state._decision_store,
        vector_store_ready=_state._vector_store_ready or asyncio.Event(),
        _engine=None,
    )
    previous = (_state._registry, _state._cross_repo_enricher)
    _state._registry = _SealedRegistry(context)
    _state._cross_repo_enricher = CrossRepoEnricher(cross_path, contracts_path)
    try:
        card = _card(await get_risk(["src/auth/service.py"], repo="alpha", include=["graph"]))
    finally:
        _state._registry, _state._cross_repo_enricher = previous

    assert card["consumers"] == []
    assert card["consumers_total"] == card["consumers_emitted"] == 0
    assert card["relationship_analysis"]["consumers"]["status"] == "available"
    assert card["cross_repo_links"] == []
    assert card["cross_repo_links_total"] == card["cross_repo_links_emitted"] == 0
    assert card["relationship_analysis"]["cross_repo"]["status"] == "available"
