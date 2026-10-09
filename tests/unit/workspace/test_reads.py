"""The workspace read views, called on plain artifact payloads.

The API routes are covered through the router; these pin what a caller holding
only the parsed JSON gets, and the one rule every view shares: a link's
consumer end is named by ``consumer_contract_id or contract_id``.
"""

from __future__ import annotations

from repowise.core.workspace.reads import (
    breaking_changes_view,
    co_change_structure,
    conformance_view,
    contract_detail,
    list_co_changes,
    list_contracts,
)

PROVIDER = {
    "contract_id": "http::GET::/users",
    "contract_type": "http",
    "role": "provider",
    "repo": "backend",
    "file_path": "api/users.py",
    "symbol_name": "list_users",
    "schema": {"type": "array"},
}
CONSUMER = {
    "contract_id": "http::GET::/api/users",
    "contract_type": "http",
    "role": "consumer",
    "repo": "frontend",
    "file_path": "src/api.ts",
    "symbol_name": "getUsers",
}
LINK = {
    "contract_id": "http::GET::/users",
    "consumer_contract_id": "http::GET::/api/users",
    "contract_type": "http",
    "match_type": "candidate",
    "confidence": 0.6,
    "provider_repo": "backend",
    "provider_file": "api/users.py",
    "consumer_repo": "frontend",
    "consumer_file": "src/api.ts",
}


def test_both_ends_of_a_differently_spelled_link_find_it():
    for contract in (PROVIDER, CONSUMER):
        detail = contract_detail(
            [PROVIDER, CONSUMER],
            [LINK],
            None,
            repo=contract["repo"],
            file_path=contract["file_path"],
            contract_id=contract["contract_id"],
        )
        assert detail is not None
        assert [lk["contract_id"] for lk in detail["links"]] == ["http::GET::/users"]
        assert detail["unmatched_reason"] is None


def test_detail_carries_the_schema_the_list_drops():
    detail = contract_detail(
        [PROVIDER], [], None, repo="backend", file_path="api/users.py", contract_id=PROVIDER["contract_id"]
    )
    assert detail["contract_schema"] == {"type": "array"}
    assert "schema" not in detail["contract"]
    assert contract_detail([PROVIDER], [], None, repo="x", file_path="y", contract_id="z") is None


def test_an_unmatched_consumer_names_its_reason():
    diagnostics = {
        "unmatched_consumers": [
            {**{k: CONSUMER[k] for k in ("repo", "file_path", "contract_id")}, "reason": "no_provider"}
        ]
    }
    detail = contract_detail(
        [CONSUMER],
        [],
        diagnostics,
        repo="frontend",
        file_path="src/api.ts",
        contract_id=CONSUMER["contract_id"],
    )
    assert detail["unmatched_reason"] == "no_provider"


def test_linked_filter_counts_the_consumer_under_its_own_id():
    linked = list_contracts([PROVIDER, CONSUMER], [LINK], linked=True)
    assert linked["total_contracts"] == 2
    assert list_contracts([PROVIDER, CONSUMER], [LINK], linked=False)["total_contracts"] == 0


def test_contract_list_pages_contracts_and_can_omit_links():
    page = list_contracts([PROVIDER, CONSUMER], [LINK], limit=1, offset=1, include_links=False)
    assert [c["repo"] for c in page["contracts"]] == ["frontend"]
    assert page["links"] == []
    assert page["total_links"] == 1
    assert page["by_type"] == {"http": 2}


def test_co_changes_report_which_cap_trimmed_the_overlay():
    pairs = [
        {"source_repo": "a", "source_file": "x", "target_repo": "b", "target_file": "y", "strength": 0.3},
        {"source_repo": "a", "source_file": "z", "target_repo": "c", "target_file": "w", "strength": 0.9},
    ]
    view = list_co_changes(pairs, 5, repo="a", limit=1)
    assert view["total"] == 2
    assert [p["strength"] for p in view["co_changes"]] == [0.9]
    assert view["truncated_by"] == "per_repo_pair"
    assert list_co_changes(pairs)["truncated_by"] is None


def test_co_change_structure_reads_the_pair_from_the_links():
    view = co_change_structure(
        [LINK],
        source_repo="frontend",
        source_file="src/api.ts",
        target_repo="backend",
        target_file="api/users.py",
    )
    assert len(view["pair_links"]) == 1
    assert view["repo_links_by_type"] == {"http": 1}
    assert view["source_file_links"] == view["target_file_links"] == 1


def test_a_filtered_breaking_report_recomputes_its_rollups():
    report = {
        "generated_at": "t",
        "changes": [
            {"provider_repo": "a", "severity": "breaking", "impacted_consumers": [{"repo": "b", "node_id": "b"}]},
            {"provider_repo": "c", "severity": "warning", "impacted_consumers": []},
        ],
        "total": 2,
    }
    assert breaking_changes_view(report) == report
    view = breaking_changes_view(report, repo="a")
    assert (view["total"], view["breaking_count"], view["impacted_repos"]) == (1, 1, ["b"])


def test_a_scoped_conformance_view_keeps_the_workspace_cycle_total():
    report = {
        "generated_at": "t",
        "violations": [{"source": "web::ui", "target": "db"}],
        "cycles": [{"nodes": ["api", "worker"]}],
        "total_cycles": 4,
    }
    view = conformance_view(report, repo="web")
    assert view["violating_repos"] == ["db", "web"]
    assert view["cycles"] == []
    assert view["total_cycles"] == 4
    assert conformance_view({"cycles": [{"nodes": ["a"]}]})["total_cycles"] == 1
