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
        assert cfg.detect_graphql is True
        assert cfg.detect_socket is True
        assert cfg.detect_topics is True
        assert cfg.manual_links == []

    def test_from_dict_partial_overrides(self) -> None:
        cfg = ContractConfig.from_dict({"detect_http": False})
        assert cfg.detect_http is False
        assert cfg.detect_grpc is True
        assert cfg.detect_socket is True
        assert cfg.detect_topics is True

    def test_round_trip(self) -> None:
        cfg = ContractConfig(
            detect_http=True,
            detect_grpc=False,
            detect_graphql=False,
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
        assert loaded.detect_graphql is False
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


class TestContractProjectPattern:
    def test_default_is_omitted_from_the_dict(self) -> None:
        assert "contract_project_pattern" not in ContractConfig().to_dict()

    def test_round_trip(self) -> None:
        cfg = ContractConfig(contract_project_pattern=r".+\.messages")
        loaded = ContractConfig.from_dict(cfg.to_dict())
        assert loaded.contract_project_pattern == r".+\.messages"

    def test_pattern_changes_the_fingerprinted_dict(self) -> None:
        changed = ContractConfig(contract_project_pattern="messages")
        assert ContractConfig().to_dict() != changed.to_dict()

    def test_invalid_regex_falls_back_to_the_default(self) -> None:
        cfg = ContractConfig.from_dict({"contract_project_pattern": "(unclosed"})
        assert cfg.contract_project_pattern == ContractConfig().contract_project_pattern


class TestSettingStages:
    """Each contract setting says whether it feeds extraction or only matching."""

    def test_every_setting_declares_its_stage(self) -> None:
        from dataclasses import fields

        from repowise.core.workspace.config import STAGE_EXTRACTION, STAGE_MATCHING

        undeclared = [
            f.name
            for f in fields(ContractConfig)
            if f.metadata.get("stage") not in {STAGE_EXTRACTION, STAGE_MATCHING}
        ]
        assert undeclared == []

    def test_matching_settings_stay_out_of_the_extraction_half(self) -> None:
        from repowise.core.workspace.config import STAGE_EXTRACTION, STAGE_MATCHING

        cfg = ContractConfig(
            service_bases={"API_BASE": "api"},
            manual_links=[ManualContractLink("a", "b", "http", "http::GET::/x")],
        )
        assert cfg.stage_dict(STAGE_EXTRACTION) == ContractConfig().to_dict()
        assert set(cfg.stage_dict(STAGE_MATCHING)) == {"service_bases", "manual_links"}

    def test_a_lone_non_default_setting_survives_save(self, tmp_path) -> None:
        ws = WorkspaceConfig(
            repos=[RepoEntry(path="a", alias="a")],
            contracts=ContractConfig(detect_code_api=False),
        )
        ws.save(tmp_path)
        assert WorkspaceConfig.load(tmp_path).contracts.detect_code_api is False

    def test_analysis_fingerprint_follows_settings_not_repos(self) -> None:
        base = WorkspaceConfig(repos=[RepoEntry(path="a", alias="a")])
        moved = WorkspaceConfig(repos=[RepoEntry(path="a", alias="a", last_commit_at_index="f00")])
        edited = WorkspaceConfig(
            repos=[RepoEntry(path="a", alias="a")],
            contracts=ContractConfig(service_bases={"API_BASE": "a"}),
        )
        assert base.analysis_fingerprint() == moved.analysis_fingerprint()
        assert base.analysis_fingerprint() != edited.analysis_fingerprint()

    def test_analysis_fingerprint_follows_declared_groups_only(self) -> None:
        import hashlib
        import json

        def ws(*tags: str) -> WorkspaceConfig:
            return WorkspaceConfig(repos=[RepoEntry(path="a", alias="a", tags=list(tags))])

        base = ws()
        # Without a group tag the payload is the one stamped before groups
        # counted, so existing stamps (conformance tags included) stay valid.
        old = [base.contracts.to_dict(), base.conformance.to_dict()]
        expected = hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()[:16]
        assert base.analysis_fingerprint() == expected
        assert ws("backend", "team:x").analysis_fingerprint() == expected
        assert ws("group:core").analysis_fingerprint() != expected
        assert ws("backend", "group:core").analysis_fingerprint() == ws(
            "group:core", "python"
        ).analysis_fingerprint()
        assert ws("group:core").analysis_fingerprint() != ws("group:edge").analysis_fingerprint()


class TestDetectGraphql:
    def test_an_older_config_without_the_key_detects_graphql(self) -> None:
        assert ContractConfig.from_dict({"detect_http": False}).detect_graphql is True

    def test_detect_graphql_is_an_extraction_setting(self) -> None:
        # Switching it off must re-extract every repo, not just re-link.
        assert "detect_graphql" in ContractConfig().stage_dict("extraction")
        assert "detect_graphql" not in ContractConfig().stage_dict("matching")

    def test_the_extractor_follows_the_switch(self) -> None:
        from repowise.core.workspace.contracts import _enabled_extractors
        from repowise.core.workspace.extractors import GraphqlExtractor

        def names(cfg: ContractConfig) -> list[str]:
            return [type(e).__name__ for e in _enabled_extractors(cfg)]

        assert GraphqlExtractor.__name__ in names(ContractConfig())
        assert GraphqlExtractor.__name__ not in names(ContractConfig(detect_graphql=False))
