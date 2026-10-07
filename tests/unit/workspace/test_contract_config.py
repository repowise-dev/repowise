"""Tests for ContractConfig and ManualContractLink in workspace config."""

from __future__ import annotations

from repowise.core.workspace.config import (
    ContractConfig,
    ManualContractLink,
    RepoEntry,
    WorkspaceConfig,
)


class TestContractConfig:
    def test_default_values(self) -> None:
        cfg = ContractConfig()
        assert cfg.detect_http is True
        assert cfg.detect_grpc is True
        assert cfg.detect_socket is True
        assert cfg.detect_topics is True
        assert cfg.manual_links == []

    def test_from_dict_partial_overrides(self) -> None:
        cfg = ContractConfig.from_dict({"detect_http": False})
        assert cfg.detect_http is False
        assert cfg.detect_grpc is True
        assert cfg.detect_socket is True
        assert cfg.detect_topics is True

    def test_api_client_bases_default_to_empty_and_round_trip(self) -> None:
        assert ContractConfig().api_client_bases == []
        assert "api_client_bases" not in ContractConfig().to_dict()
        cfg = ContractConfig(api_client_bases=["Acme.Http.ApiClient", "Acme.Other.BaseClient"])
        loaded = ContractConfig.from_dict(cfg.to_dict())
        assert loaded.api_client_bases == ["Acme.Http.ApiClient", "Acme.Other.BaseClient"]

    def test_api_client_bases_change_the_fingerprinted_dict(self) -> None:
        # contracts.py hashes ContractConfig.to_dict() into the extraction fingerprint.
        assert ContractConfig(api_client_bases=["A.B"]).to_dict() != ContractConfig().to_dict()

    def test_workspace_config_keeps_the_contracts_section_when_bases_are_set(self) -> None:
        ws = WorkspaceConfig(contracts=ContractConfig(api_client_bases=["Acme.Http.ApiClient"]))
        assert ws.to_dict()["contracts"]["api_client_bases"] == ["Acme.Http.ApiClient"]

    def test_round_trip(self) -> None:
        cfg = ContractConfig(
            detect_http=True,
            detect_grpc=False,
            detect_socket=True,
            detect_topics=True,
            manual_links=[
                ManualContractLink(
                    from_repo="worker",
                    to_repo="api",
                    contract_type="http",
                    contract_id="http::GET::/api/jobs",
                    from_role="consumer",
                ),
            ],
        )
        d = cfg.to_dict()
        loaded = ContractConfig.from_dict(d)
        assert loaded.detect_grpc is False
        assert len(loaded.manual_links) == 1
        assert loaded.manual_links[0].from_repo == "worker"


class TestManualContractLink:
    def test_from_dict(self) -> None:
        data = {
            "from_repo": "frontend",
            "to_repo": "backend",
            "contract_type": "http",
            "contract_id": "http::POST::/api/auth",
            "from_role": "consumer",
        }
        ml = ManualContractLink.from_dict(data)
        assert ml.from_repo == "frontend"
        assert ml.to_repo == "backend"
        assert ml.from_role == "consumer"

    def test_default_role(self) -> None:
        ml = ManualContractLink.from_dict({
            "from_repo": "a",
            "to_repo": "b",
            "contract_type": "topic",
            "contract_id": "topic::events",
        })
        assert ml.from_role == "consumer"

    def test_round_trip(self) -> None:
        ml = ManualContractLink(
            from_repo="a", to_repo="b",
            contract_type="grpc", contract_id="grpc::Auth/*",
            from_role="provider",
        )
        loaded = ManualContractLink.from_dict(ml.to_dict())
        assert loaded.from_repo == "a"
        assert loaded.from_role == "provider"


class TestWorkspaceConfigWithContracts:
    def test_from_dict_no_contracts_key(self) -> None:
        data = {
            "version": 1,
            "repos": [{"path": "api", "alias": "api"}],
            "default_repo": "api",
        }
        cfg = WorkspaceConfig.from_dict(data)
        assert cfg.contracts.detect_http is True
        assert cfg.contracts.detect_grpc is True

    def test_from_dict_with_contracts(self) -> None:
        data = {
            "version": 1,
            "repos": [{"path": "api", "alias": "api"}],
            "default_repo": "api",
            "contracts": {
                "detect_http": True,
                "detect_grpc": False,
                "detect_socket": False,
                "detect_topics": True,
            },
        }
        cfg = WorkspaceConfig.from_dict(data)
        assert cfg.contracts.detect_grpc is False
        assert cfg.contracts.detect_socket is False

    def test_round_trip_with_manual_links(self, tmp_path) -> None:
        cfg = WorkspaceConfig(
            version=1,
            repos=[RepoEntry(path="api", alias="api")],
            default_repo="api",
            contracts=ContractConfig(
                detect_http=True,
                detect_grpc=True,
                detect_socket=False,
                detect_topics=False,
                manual_links=[
                    ManualContractLink(
                        from_repo="worker",
                        to_repo="api",
                        contract_type="http",
                        contract_id="http::GET::/jobs",
                    ),
                ],
            ),
        )
        cfg.save(tmp_path)
        loaded = WorkspaceConfig.load(tmp_path)
        assert loaded.contracts.detect_socket is False
        assert loaded.contracts.detect_topics is False
        assert len(loaded.contracts.manual_links) == 1
        assert loaded.contracts.manual_links[0].contract_id == "http::GET::/jobs"
