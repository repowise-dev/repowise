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


def test_no_report_is_reported_as_silence_not_as_an_all_clear(tmp_path: Path):
    """Nothing has looked, so the block must not say nothing was found."""
    block = _block(_enricher(tmp_path, [_link()]), [PROVIDER_FILE])
    assert block["breaking_changes_available"] is False
    assert block["breaking_changes_as_of"] is None
    assert block["summary"] == (
        "1 consumer link(s) in 1 other repo(s) touch the files this change edits"
        "; no breaking-change report has been built for them."
    )


def test_a_clean_report_is_reported_as_an_all_clear(tmp_path: Path):
    clean = BreakingChangeReport(generated_at="2026-08-23T00:00:00+00:00")
    block = _block(_enricher(tmp_path, [_link()], clean), [PROVIDER_FILE])
    assert block["breaking_changes_available"] is True
    assert block["summary"].endswith("; the last workspace update found no break in them.")


def test_an_unstamped_report_is_silence_not_an_all_clear(tmp_path: Path):
    """A file exists but no pass produced it; zero changes prove nothing."""
    never_ran = BreakingChangeReport(generated_at=None)
    block = _block(_enricher(tmp_path, [_link()], never_ran), [PROVIDER_FILE])
    assert block["breaking_changes_available"] is False
    assert block["breaking_changes_as_of"] is None
    assert block["summary"].endswith("; no breaking-change report has been built for them.")


def test_carries_the_break_attributed_to_that_file(tmp_path: Path):
    """Driven through real detection: a removal leaves no *current* link."""
    from repowise.core.workspace.breaking_change import detect_breaking_changes
    from repowise.core.workspace.contracts import Contract, ContractStore

    previous = ContractStore(
        contracts=[
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
        ],
        contract_links=[_link()],
    )
    report = detect_breaking_changes(
        previous, ContractStore(), generated_at="2026-08-23T00:00:00+00:00"
    )
    # The contract is gone, so the current store has no link for it.
    block = _block(_enricher(tmp_path, [], report), [PROVIDER_FILE])
    assert len(block["breaking_changes"]) == 1
    entry = block["breaking_changes"][0]
    assert entry["type"] == "code"
    assert entry["kind"] == "removed_endpoint"
    assert entry["impacted_repos"] == ["frontend"]
    assert entry["provider_symbol_id"] == f"{PROVIDER_FILE}::Order"
    assert block["breaking_changes_as_of"] == "2026-08-23T00:00:00+00:00"
    # consumers is empty, but the repo count must still come from the break.
    assert block["consumers"] == []
    assert block["consumer_repos"] == ["frontend"]
    assert block["summary"] == (
        "0 consumer link(s) in 1 other repo(s) touch the files this change edits"
        "; 1 of the changed contracts broke them."
    )


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


def test_two_contracts_between_the_same_files_stay_two_rows(tmp_path: Path):
    """Collapsing them would silently drop a contract_id."""
    other = _link()
    other.contract_id = "code::@acme/types::Invoice"
    block = _block(_enricher(tmp_path, [_link(), other]), [PROVIDER_FILE])
    assert [c["contract_id"] for c in block["consumers"]] == [
        "code::@acme/types::Order",
        "code::@acme/types::Invoice",
    ]


def test_consumer_list_is_capped_and_says_by_how_much(tmp_path: Path):
    from repowise.server.mcp_server.tool_change_risk import _CROSS_REPO_CONSUMER_LIMIT

    over = _CROSS_REPO_CONSUMER_LIMIT + 3
    links = [_link(consumer_file=f"src/api{i}.ts") for i in range(over)]
    block = _block(_enricher(tmp_path, links), [PROVIDER_FILE])
    assert len(block["consumers"]) == _CROSS_REPO_CONSUMER_LIMIT
    assert block["consumers_truncated"] == 3
    assert f"{over} consumer link(s)" in block["summary"]


def test_breaking_list_is_capped_and_says_by_how_much(tmp_path: Path):
    from repowise.core.workspace.breaking_change import detect_breaking_changes
    from repowise.core.workspace.contracts import Contract, ContractStore
    from repowise.server.mcp_server.tool_change_risk import _CROSS_REPO_BREAKING_LIMIT

    over = _CROSS_REPO_BREAKING_LIMIT + 2
    names = [f"Type{i}" for i in range(over)]
    previous = ContractStore(
        contracts=[
            Contract(
                repo="api",
                contract_id=f"code::@acme/types::{n}",
                contract_type="code",
                role="provider",
                file_path=PROVIDER_FILE,
                symbol_name=n,
                confidence=0.9,
                symbol_id=f"{PROVIDER_FILE}::{n}",
            )
            for n in names
        ],
        contract_links=[
            ContractLink(
                contract_id=f"code::@acme/types::{n}",
                contract_type="code",
                match_type="exact",
                confidence=0.9,
                provider_repo="api",
                provider_file=PROVIDER_FILE,
                provider_symbol=n,
                provider_service=None,
                consumer_repo="frontend",
                consumer_file="src/api.ts",
                consumer_symbol=f"@acme/types:{n}",
                consumer_service=None,
            )
            for n in names
        ],
    )
    report = detect_breaking_changes(previous, ContractStore(), generated_at="t")
    block = _block(_enricher(tmp_path, [], report), [PROVIDER_FILE])
    assert len(block["breaking_changes"]) == _CROSS_REPO_BREAKING_LIMIT
    assert block["breaking_changes_truncated"] == 2
    assert f"{over} of the changed contracts broke them" in block["summary"]


def test_cross_repo_participates_in_the_response_ceiling(tmp_path: Path):
    """A payload block outside the shed order can never be shed (#1876)."""
    from repowise.server.mcp_server._budget import OmissionCollector, fit_to_budget
    from repowise.server.mcp_server.tool_change_risk import _SHED_ORDER

    assert "cross_repo" in _SHED_ORDER
    # Shed after fix_history's rows, before the run-list: an agent keeps the
    # tests to run for longer than the list of downstream consumers.
    assert _SHED_ORDER.index("fix_history.files") < _SHED_ORDER.index("cross_repo")
    assert _SHED_ORDER.index("cross_repo") < _SHED_ORDER.index("impacted_tests")

    payload = {
        "score": 7.0,
        "fix_history": {"density": 1.0, "files": ["f.py"]},
        "impacted_tests": {"tests_to_run": ["t.py"]},
        "cross_repo": {"consumers": [{"repo": "frontend", "pad": "x" * 400}] * 200},
    }
    collector = OmissionCollector("get_change_risk", repo_root=tmp_path)
    fit_to_budget(payload, _SHED_ORDER, collector)
    assert "cross_repo" not in payload
    assert payload["truncated"] is True
    # The tests to run outlive both the consumer list and the fix record.
    assert payload["impacted_tests"]["tests_to_run"] == ["t.py"]
    assert "fix_history" not in payload
