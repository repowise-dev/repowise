"""The cross-repo consequence get_change_risk attaches to a scored commit."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.workspace.breaking_change import (
    BreakingChange,
    BreakingChangeReport,
    ImpactedConsumer,
)
from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._enrichment import CrossRepoEnricher
from repowise.server.mcp_server.tool_change_risk import _cross_repo_block

PROVIDER_FILE = "packages/types/src/order.ts"


def _link(consumer_repo: str = "frontend", consumer_file: str = "src/api.ts") -> ContractLink:
    return ContractLink(
        contract_id="code::@acme/types::Order",
        contract_type="code",
        match_type="exact",
        confidence=0.9,
        provider_repo="api",
        provider_file=PROVIDER_FILE,
        provider_symbol="Order",
        provider_service="packages/types",
        consumer_repo=consumer_repo,
        consumer_file=consumer_file,
        consumer_symbol="@acme/types:Order",
        consumer_service=None,
        provider_symbol_id=f"{PROVIDER_FILE}::Order",
    )


def _report(consumer_repo: str = "frontend") -> BreakingChangeReport:
    return BreakingChangeReport(
        generated_at="2026-08-23T00:00:00+00:00",
        changes=[
            BreakingChange(
                kind="removed_endpoint",
                severity="breaking",
                contract_id="code::@acme/types::Order",
                contract_type="code",
                provider_repo="api",
                provider_file=PROVIDER_FILE,
                provider_symbol="Order",
                provider_symbol_id=f"{PROVIDER_FILE}::Order",
                provider_service="packages/types",
                detail="code::@acme/types::Order was removed",
                impacted_consumers=[
                    ImpactedConsumer(
                        repo=consumer_repo,
                        service=None,
                        node_id=consumer_repo,
                        file="src/api.ts",
                        symbol="@acme/types:Order",
                        match_type="exact",
                        confidence=0.9,
                    )
                ],
            )
        ],
    )


def _enricher(
    tmp_path: Path,
    links: list[ContractLink],
    report: BreakingChangeReport | None = None,
) -> CrossRepoEnricher:
    contracts = [
        Contract(
            repo="api",
            contract_id="code::@acme/types::Order",
            contract_type="code",
            role="provider",
            file_path=PROVIDER_FILE,
            symbol_name="Order",
            confidence=0.9,
            service="packages/types",
            symbol_id=f"{PROVIDER_FILE}::Order",
        )
    ]
    (tmp_path / "contracts.json").write_text(
        json.dumps(
            {
                "contracts": [c.to_dict() for c in contracts],
                "contract_links": [lk.to_dict() for lk in links],
            }
        ),
        encoding="utf-8",
    )
    bc_path = None
    if report is not None:
        bc_path = tmp_path / "breaking_changes.json"
        bc_path.write_text(json.dumps(report.to_dict()), encoding="utf-8")
    return CrossRepoEnricher(
        tmp_path / "cross_repo_edges.json",
        contracts_path=tmp_path / "contracts.json",
        breaking_changes_path=bc_path,
    )


def _block(enricher: CrossRepoEnricher | None, changed: list[str], *, workspace: bool = True):
    prev_registry = _state._registry
    prev_enricher = _state._cross_repo_enricher
    _state._registry = object() if workspace else None
    _state._cross_repo_enricher = enricher
    try:
        return _cross_repo_block("api", changed)
    finally:
        _state._registry = prev_registry
        _state._cross_repo_enricher = prev_enricher


def test_names_the_consumers_of_a_touched_provider_file(tmp_path: Path):
    block = _block(_enricher(tmp_path, [_link()]), [PROVIDER_FILE])
    assert block["consumer_repos"] == ["frontend"]
    assert block["consumers"] == [
        {
            "provider_file": PROVIDER_FILE,
            "repo": "frontend",
            "file": "src/api.ts",
            "contract_id": "code::@acme/types::Order",
            "contract_type": "code",
            "match_type": "exact",
            "provider_symbol_id": f"{PROVIDER_FILE}::Order",
        }
    ]
    assert block["breaking_changes"] == []
    assert "no break in them" in block["summary"]


def test_carries_the_break_attributed_to_that_file(tmp_path: Path):
    block = _block(_enricher(tmp_path, [_link()], _report()), [PROVIDER_FILE])
    assert len(block["breaking_changes"]) == 1
    entry = block["breaking_changes"][0]
    assert entry["type"] == "code"
    assert entry["kind"] == "removed_endpoint"
    assert entry["impacted_repos"] == ["frontend"]
    assert entry["provider_symbol_id"] == f"{PROVIDER_FILE}::Order"
    assert block["as_of"] == "2026-08-23T00:00:00+00:00"
    assert "1 of the changed contracts broke them" in block["summary"]


def test_absent_when_the_commit_touches_no_published_file(tmp_path: Path):
    assert _block(_enricher(tmp_path, [_link()]), ["src/unrelated.ts"]) is None


def test_absent_for_a_same_repo_consumer(tmp_path: Path):
    """An in-repo consumer is not a cross-repo consequence."""
    enricher = _enricher(tmp_path, [_link(consumer_repo="api")], _report(consumer_repo="api"))
    assert _block(enricher, [PROVIDER_FILE]) is None


def test_absent_outside_workspace_mode(tmp_path: Path):
    enricher = _enricher(tmp_path, [_link()])
    assert _block(enricher, [PROVIDER_FILE], workspace=False) is None


def test_absent_without_an_enricher():
    assert _block(None, [PROVIDER_FILE]) is None


def test_one_row_per_consumer_file_not_per_link(tmp_path: Path):
    """Two links to the same consumer file collapse; two files stay two rows."""
    links = [_link(), _link(), _link(consumer_file="src/other.ts")]
    block = _block(_enricher(tmp_path, links), [PROVIDER_FILE])
    assert [c["file"] for c in block["consumers"]] == ["src/api.ts", "src/other.ts"]
