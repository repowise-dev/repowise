"""Tests for CrossRepoEnricher contract loading (Phase 4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.server.mcp_server._enrichment import CrossRepoEnricher
from repowise.server.mcp_server.tool_risk.enrichment import _combined_analysis


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture()
def contracts_json(tmp_path: Path) -> Path:
    """Write a minimal contracts.json and return its path."""
    path = tmp_path / "contracts.json"
    _write_json(path, {
        "version": 2,
        "generated_at": "2026-04-12T12:00:00Z",
        "contracts": [
            {
                "repo": "backend",
                "contract_id": "http::GET::/api/users",
                "contract_type": "http",
                "role": "provider",
                "file_path": "routes.py",
                "symbol_name": "get_users",
                "confidence": 0.85,
            },
            {
                "repo": "frontend",
                "contract_id": "http::GET::/api/users",
                "contract_type": "http",
                "role": "consumer",
                "file_path": "client.ts",
                "symbol_name": "fetchUsers",
                "confidence": 0.75,
            },
        ],
        "contract_links": [
            {
                "contract_id": "http::GET::/api/users",
                "contract_type": "http",
                "match_type": "exact",
                "confidence": 0.75,
                "provider_repo": "backend",
                "provider_file": "routes.py",
                "provider_symbol": "get_users",
                "consumer_repo": "frontend",
                "consumer_file": "client.ts",
                "consumer_symbol": "fetchUsers",
            },
        ],
    })
    return path


@pytest.fixture()
def empty_cross_repo(tmp_path: Path) -> Path:
    """Empty cross_repo_edges.json (no co-changes or deps)."""
    path = tmp_path / "cross_repo_edges.json"
    _write_json(path, {"version": 2, "co_changes": [], "package_deps": []})
    return path


class TestEnricherContractLoading:
    def test_no_contracts_path(self, empty_cross_repo: Path) -> None:
        enricher = CrossRepoEnricher(empty_cross_repo)
        assert enricher.has_contract_data is False
        assert enricher.cross_repo_analysis["status"] == "available"
        assert enricher.contract_analysis["status"] == "unavailable"

    def test_missing_contracts_file(self, empty_cross_repo: Path, tmp_path: Path) -> None:
        enricher = CrossRepoEnricher(
            empty_cross_repo,
            contracts_path=tmp_path / "nonexistent.json",
        )
        assert enricher.has_contract_data is False
        assert enricher.contract_analysis == {
            "status": "unavailable",
            "reason": "artifact_missing",
        }

    def test_valid_empty_contract_artifact_is_available(
        self, empty_cross_repo: Path, tmp_path: Path
    ) -> None:
        contracts = tmp_path / "contracts.json"
        _write_json(contracts, {"version": 2, "contracts": [], "contract_links": []})
        enricher = CrossRepoEnricher(empty_cross_repo, contracts_path=contracts)

        assert enricher.has_contract_data is False
        assert enricher.contract_analysis["status"] == "available"

    def test_malformed_artifacts_are_degraded(self, tmp_path: Path) -> None:
        cross_repo = tmp_path / "cross_repo_edges.json"
        contracts = tmp_path / "contracts.json"
        cross_repo.write_text("{", encoding="utf-8")
        contracts.write_text("{", encoding="utf-8")
        enricher = CrossRepoEnricher(cross_repo, contracts_path=contracts)

        assert enricher.cross_repo_analysis["status"] == "degraded"
        assert enricher.contract_analysis["status"] == "degraded"

    def test_malformed_rows_make_otherwise_valid_artifacts_partial(self, tmp_path: Path) -> None:
        cross_repo = tmp_path / "cross_repo_edges.json"
        contracts = tmp_path / "contracts.json"
        _write_json(
            cross_repo,
            {"version": 2, "co_changes": [{"source_repo": "alpha"}], "package_deps": []},
        )
        _write_json(
            contracts,
            {"version": 2, "contracts": [], "contract_links": [{"provider_repo": "alpha"}]},
        )
        enricher = CrossRepoEnricher(cross_repo, contracts_path=contracts)

        assert enricher.cross_repo_analysis["status"] == "partial"
        assert enricher.cross_repo_analysis["malformed_co_changes_skipped"] == 1
        assert enricher.contract_analysis["status"] == "partial"
        assert enricher.contract_analysis["malformed_contract_links_skipped"] == 1

    @pytest.mark.parametrize(
        ("cross_state", "contract_state"),
        [
            ({"status": "available"}, {"status": "unavailable"}),
            ({"status": "unavailable"}, {"status": "available"}),
        ],
    )
    def test_mixed_source_availability_is_partial(
        self, cross_state: dict, contract_state: dict
    ) -> None:
        analysis = _combined_analysis(cross_state, contract_state, [])

        assert analysis["status"] == "partial"
        assert analysis["sources"]["cross_repo_overlay"] == cross_state
        assert analysis["sources"]["contracts"] == contract_state

    def test_loads_contract_links(
        self, empty_cross_repo: Path, contracts_json: Path
    ) -> None:
        enricher = CrossRepoEnricher(empty_cross_repo, contracts_path=contracts_json)
        assert enricher.has_contract_data is True
        assert enricher.has_data is True

    def test_provider_index(
        self, empty_cross_repo: Path, contracts_json: Path
    ) -> None:
        enricher = CrossRepoEnricher(empty_cross_repo, contracts_path=contracts_json)
        links = enricher.get_contract_links_as_provider("backend", "routes.py")
        assert len(links) == 1
        assert links[0]["consumer_repo"] == "frontend"

    def test_consumer_index(
        self, empty_cross_repo: Path, contracts_json: Path
    ) -> None:
        enricher = CrossRepoEnricher(empty_cross_repo, contracts_path=contracts_json)
        links = enricher.get_contract_links_as_consumer("frontend", "client.ts")
        assert len(links) == 1
        assert links[0]["provider_repo"] == "backend"

    def test_missing_file_returns_empty(
        self, empty_cross_repo: Path, contracts_json: Path
    ) -> None:
        enricher = CrossRepoEnricher(empty_cross_repo, contracts_path=contracts_json)
        assert enricher.get_contract_links_as_provider("backend", "nonexistent.py") == []
        assert enricher.get_contract_links_as_consumer("frontend", "nonexistent.ts") == []

    def test_contract_summary(
        self, empty_cross_repo: Path, contracts_json: Path
    ) -> None:
        enricher = CrossRepoEnricher(empty_cross_repo, contracts_path=contracts_json)
        summary = enricher.get_contract_summary()
        assert summary["total_contracts"] == 2
        assert summary["total_links"] == 1
        assert summary["by_type"]["http"] == 2

    def test_affected_repos_includes_contracts(
        self, empty_cross_repo: Path, contracts_json: Path
    ) -> None:
        enricher = CrossRepoEnricher(empty_cross_repo, contracts_path=contracts_json)
        affected = enricher.get_affected_repos("backend", "routes.py")
        assert "frontend" in affected

    def test_has_data_with_only_contracts(self, tmp_path: Path, contracts_json: Path) -> None:
        # No cross_repo_edges.json at all
        missing_path = tmp_path / "missing_cross_repo.json"
        enricher = CrossRepoEnricher(missing_path, contracts_path=contracts_json)
        assert enricher.has_data is True
        assert enricher.has_contract_data is True
