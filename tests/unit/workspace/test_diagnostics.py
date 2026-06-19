"""Tests for extraction diagnostics — provider/consumer counts, unmatched reasons, orphans."""

from __future__ import annotations

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.diagnostics import (
    WEAK_LINK_CONFIDENCE_THRESHOLD,
    UnmatchedReason,
    build_diagnostics,
)


def _provider(
    repo: str, cid: str, ctype: str = "http", file: str = "h.py", service=None
) -> Contract:
    return Contract(
        repo=repo,
        contract_id=cid,
        contract_type=ctype,
        role="provider",
        file_path=file,
        symbol_name="handler",
        confidence=0.9,
        service=service,
    )


def _consumer(
    repo: str, cid: str, ctype: str = "http", file: str = "c.py", service=None
) -> Contract:
    return Contract(
        repo=repo,
        contract_id=cid,
        contract_type=ctype,
        role="consumer",
        file_path=file,
        symbol_name="call",
        confidence=0.8,
        service=service,
    )


def _link(
    cid: str, p_repo: str, c_repo: str, confidence: float = 0.72, ctype: str = "http"
) -> ContractLink:
    return ContractLink(
        contract_id=cid,
        contract_type=ctype,
        match_type="exact",
        confidence=confidence,
        provider_repo=p_repo,
        provider_file="h.py",
        provider_symbol="handler",
        provider_service=None,
        consumer_repo=c_repo,
        consumer_file="c.py",
        consumer_symbol="call",
        consumer_service=None,
    )


def test_empty_workspace_is_all_zero():
    diag = build_diagnostics([], [])
    assert diag.total_providers == 0
    assert diag.total_consumers == 0
    assert diag.total_links == 0
    assert diag.repo_breakdown == []
    assert diag.unmatched_consumers == []
    assert diag.orphan_providers == []


def test_provider_consumer_counts_by_repo_and_type():
    contracts = [
        _provider("api", "http::GET::/users"),
        _provider("api", "grpc::svc/M", ctype="grpc"),
        _consumer("web", "http::GET::/users"),
    ]
    diag = build_diagnostics(contracts, [])
    assert diag.total_providers == 2
    assert diag.total_consumers == 1

    by_repo = {r.repo: r for r in diag.repo_breakdown}
    assert by_repo["api"].provider_count == 2
    assert by_repo["api"].providers_by_type == {"grpc": 1, "http": 1}
    assert by_repo["web"].consumer_count == 1
    assert by_repo["web"].consumers_by_type == {"http": 1}


def test_matched_consumer_is_not_unmatched():
    contracts = [
        _provider("api", "http::GET::/users"),
        _consumer("web", "http::GET::/users"),
    ]
    links = [_link("http::GET::/users", "api", "web")]
    diag = build_diagnostics(contracts, links)
    assert diag.unmatched_consumers == []
    assert diag.orphan_providers == []
    assert diag.total_links == 1


def test_unmatched_no_provider():
    contracts = [_consumer("web", "http::GET::/missing")]
    diag = build_diagnostics(contracts, [])
    assert len(diag.unmatched_consumers) == 1
    assert diag.unmatched_consumers[0].reason == UnmatchedReason.NO_PROVIDER
    assert diag.unmatched_by_reason == {UnmatchedReason.NO_PROVIDER: 1}


def test_unmatched_internal_only():
    # Provider and consumer live in the same repo + service: intra-service call,
    # intentionally not surfaced as a cross-repo link.
    contracts = [
        _provider("api", "http::GET::/users", service="services/x"),
        _consumer("api", "http::GET::/users", service="services/x"),
    ]
    diag = build_diagnostics(contracts, [])
    assert len(diag.unmatched_consumers) == 1
    assert diag.unmatched_consumers[0].reason == UnmatchedReason.INTERNAL_ONLY


def test_orphan_provider_detected():
    contracts = [_provider("api", "http::GET::/unused")]
    diag = build_diagnostics(contracts, [])
    assert len(diag.orphan_providers) == 1
    assert diag.orphan_providers[0].contract_id == "http::GET::/unused"


def test_weak_link_counted_at_threshold():
    contracts = [
        _provider("api", "http::GET::/u"),
        _consumer("web", "http::GET::/u"),
    ]
    weak = [_link("http::GET::/u", "api", "web", confidence=WEAK_LINK_CONFIDENCE_THRESHOLD)]
    assert build_diagnostics(contracts, weak).weak_link_count == 1
    strong = [_link("http::GET::/u", "api", "web", confidence=0.9)]
    assert build_diagnostics(contracts, strong).weak_link_count == 0


def test_round_trip_serialization():
    contracts = [
        _provider("api", "http::GET::/users"),
        _consumer("web", "http::GET::/users"),
        _consumer("web", "http::GET::/missing"),
    ]
    links = [_link("http::GET::/users", "api", "web")]
    from repowise.core.workspace.diagnostics import ExtractionDiagnostics

    diag = build_diagnostics(contracts, links)
    restored = ExtractionDiagnostics.from_dict(diag.to_dict())
    assert restored.to_dict() == diag.to_dict()
