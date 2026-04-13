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
        assert cfg.detect_topics is True
        assert cfg.manual_links == []

    def test_from_dict_partial_overrides(self) -> None:
        cfg = ContractConfig.from_dict({"detect_http": False})
        assert cfg.detect_http is False
        assert cfg.detect_grpc is True
        assert cfg.detect_topics is True

    def test_round_trip(self) -> None:
        cfg = ContractConfig(
            detect_http=True,
            detect_grpc=False,
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
                "detect_topics": True,
            },
        }
        cfg = WorkspaceConfig.from_dict(data)
        assert cfg.contracts.detect_grpc is False

    def test_round_trip_with_manual_links(self, tmp_path) -> None:
        cfg = WorkspaceConfig(
            version=1,
            repos=[RepoEntry(path="api", alias="api")],
            default_repo="api",
            contracts=ContractConfig(
                detect_http=True,
                detect_grpc=True,
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
        assert loaded.contracts.detect_topics is False
        assert len(loaded.contracts.manual_links) == 1
        assert loaded.contracts.manual_links[0].contract_id == "http::GET::/jobs"
