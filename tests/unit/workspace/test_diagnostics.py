"""Tests for extraction diagnostics — provider/consumer counts, unmatched reasons, orphans."""

from __future__ import annotations

from repowise.core.workspace.contracts import Contract, ContractLink
from repowise.core.workspace.diagnostics import (
    WEAK_LINK_CONFIDENCE_THRESHOLD,
    UnmatchedReason,
    build_diagnostics,
)
from repowise.core.workspace.extractors.from_index import (
    EXTRACTION_LAYER_KEY,
    LAYER_INDEX,
    LAYER_REGEX,
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


def test_in_service_consumer_is_counted_apart_not_unmatched():
    # Provider and consumer live in the same repo + service: the call is served,
    # so it is not unmatched, and it is not a cross-repo link either.
    contracts = [
        _provider("api", "http::GET::/users", service="services/x"),
        _consumer("api", "http::GET::/users", service="services/x"),
    ]
    diag = build_diagnostics(contracts, [])
    assert diag.unmatched_consumers == []
    assert diag.unmatched_by_reason == {}
    assert [u.reason for u in diag.in_service_consumers] == [UnmatchedReason.INTERNAL_ONLY]


def test_old_artifact_internal_only_rows_move_to_in_service():
    from repowise.core.workspace.diagnostics import ExtractionDiagnostics

    row = {
        "repo": "api",
        "file_path": "c.py",
        "contract_id": "data::t",
        "contract_type": "data",
        "reason": "internal_only",
    }
    restored = ExtractionDiagnostics.from_dict(
        {"unmatched_consumers": [row], "unmatched_by_reason": {"internal_only": 1}}
    )
    assert restored.unmatched_consumers == []
    assert restored.unmatched_by_reason == {}
    assert [u.contract_id for u in restored.in_service_consumers] == ["data::t"]


def test_orphan_provider_detected():
    contracts = [_provider("api", "http::GET::/unused")]
    diag = build_diagnostics(contracts, [])
    assert len(diag.orphan_providers) == 1
    assert diag.orphan_providers[0].contract_id == "http::GET::/unused"


def test_orphan_declared_in_two_files_is_one_orphan():
    """A table in its model and its migration is one endpoint nobody reads."""
    contracts = [
        _provider("api", "data::payouts", ctype="data", file="app/Models/Payout.php"),
        _provider("api", "data::payouts", ctype="data", file="db/migrations/1_create.php"),
        _provider("worker", "data::payouts", ctype="data", file="m.py"),
    ]
    diag = build_diagnostics(contracts, [])
    by_repo = {o.repo: o for o in diag.orphan_providers}
    assert len(diag.orphan_providers) == 2
    assert by_repo["api"].file_path == "app/Models/Payout.php"
    assert by_repo["api"].other_files == ["db/migrations/1_create.php"]
    assert by_repo["worker"].other_files == []


def test_orphan_copies_split_by_service():
    contracts = [
        _provider("mono", "http::GET::/h", file="a/h.py", service="a"),
        _provider("mono", "http::GET::/h", file="b/h.py", service="b"),
    ]
    assert len(build_diagnostics(contracts, []).orphan_providers) == 2


def test_an_endpoint_linked_through_one_copy_is_not_an_orphan():
    contracts = [
        _provider("api", "http::GET::/u", file="h.py"),
        _provider("api", "http::GET::/u", file="h2.py"),
        _consumer("web", "http::GET::/u"),
    ]
    links = [_link("http::GET::/u", "api", "web")]
    assert build_diagnostics(contracts, links).orphan_providers == []


def test_link_coverage_counts_linked_calls_not_read_ones():
    """Read 3 calls of 4 seen, linked 1: 25% linked, 75% read."""
    contracts = [
        _provider("api", "http::GET::/u"),
        _consumer("web", "http::GET::/u"),
        _consumer("web", "http::GET::/nowhere", file="d.py"),
        _consumer("web", "http::GET::/gone", file="e.py"),
    ]
    links = [_link("http::GET::/u", "api", "web")]
    diag = build_diagnostics(contracts, links, {"web": {"call_files_unrecognised": 1}})
    assert diag.http_consumers_linked == 1
    assert diag.http_consumer_coverage == 0.75
    assert diag.http_link_coverage == 0.25
    from repowise.core.workspace.diagnostics import ExtractionDiagnostics

    restored = ExtractionDiagnostics.from_dict(diag.to_dict())
    assert restored.to_dict() == diag.to_dict()


def test_link_coverage_unknown_on_an_artifact_without_the_count():
    from repowise.core.workspace.diagnostics import ExtractionDiagnostics

    diag = build_diagnostics([_consumer("web", "http::GET::/u")], [], {"web": {"call_files_unrecognised": 0}})
    data = diag.to_dict()
    del data["http_consumers_linked"]
    restored = ExtractionDiagnostics.from_dict(data)
    assert restored.http_consumer_coverage == 1.0
    assert restored.http_link_coverage is None


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


# ---------------------------------------------------------------------------
# Extraction coverage — the by-layer split and the unresolved denominator
# ---------------------------------------------------------------------------


def test_layer_split_reads_the_provenance_each_contract_carries():
    index_provider = _provider("api", "http::GET::/a")
    index_provider.meta[EXTRACTION_LAYER_KEY] = LAYER_INDEX
    regex_provider = _provider("api", "http::GET::/b")
    regex_provider.meta[EXTRACTION_LAYER_KEY] = LAYER_REGEX

    diag = build_diagnostics([index_provider, regex_provider], [])

    assert diag.providers_by_layer == {LAYER_INDEX: 1, LAYER_REGEX: 1}
    assert diag.repo_breakdown[0].providers_by_layer == {LAYER_INDEX: 1, LAYER_REGEX: 1}


def test_contract_with_no_recorded_layer_counts_as_regex():
    """Absence of provenance must not vanish from the totals."""
    diag = build_diagnostics([_provider("api", "http::GET::/a")], [])
    assert diag.providers_by_layer == {LAYER_REGEX: 1}


def test_unresolved_calls_reach_the_diagnostics_from_the_extraction_stats():
    contracts = [_consumer("web", "http::GET::/u")]
    stats = {"web": {"http_consumer_unresolved": 3, "call_files_unrecognised": 0}}

    diag = build_diagnostics(contracts, [], stats)

    assert diag.http_consumers_unresolved == 3
    assert diag.repo_breakdown[0].http_consumers_unresolved == 3
    # 1 resolved of 4 located.
    assert diag.http_consumer_coverage == 0.25


def test_unresolved_calls_counted_for_a_repo_with_no_contracts():
    """A repo where every call failed to resolve has no breakdown row, and is
    exactly the repo whose misses must not disappear."""
    stats = {"ghost": {"http_consumer_unresolved": 9}}
    diag = build_diagnostics([_consumer("web", "http::GET::/u")], [], stats)
    assert diag.http_consumers_unresolved == 9
    assert [r.repo for r in diag.repo_breakdown] == ["web"]


def test_coverage_is_none_rather_than_perfect_when_nothing_was_located():
    """0/0 is not 100%."""
    assert build_diagnostics([], []).http_consumer_coverage is None
    assert build_diagnostics([_provider("api", "http::GET::/a")], []).http_consumer_coverage is None


def test_coverage_ignores_non_http_consumers():
    """The unresolved counter only counts HTTP calls, so the denominator must
    not be padded with grpc/topic consumers."""
    contracts = [
        _consumer("web", "http::GET::/u", ctype="http"),
        _consumer("web", "grpc::Svc::M", ctype="grpc"),
    ]
    diag = build_diagnostics(
        contracts, [], {"web": {"http_consumer_unresolved": 1, "call_files_unrecognised": 0}}
    )
    assert diag.http_consumer_coverage == 0.5


def test_coverage_survives_a_serialization_round_trip():
    from repowise.core.workspace.diagnostics import ExtractionDiagnostics

    diag = build_diagnostics(
        [_consumer("web", "http::GET::/u")],
        [],
        {"web": {"http_consumer_unresolved": 1, "call_files_unrecognised": 2}},
    )
    restored = ExtractionDiagnostics.from_dict(diag.to_dict())
    assert restored.http_consumers_unresolved == 1
    assert restored.unrecognised_call_files == {"web": 2}
    assert restored.http_consumer_coverage == 0.25
    assert restored.to_dict() == diag.to_dict()


def test_unrecognised_call_files_lower_the_coverage():
    """1 located and resolved is not 100% when 3 files call out unread."""
    stats = {"web": {"call_files_unrecognised": 3}}
    diag = build_diagnostics([_consumer("web", "http::GET::/u")], [], stats)
    assert diag.unrecognised_call_file_count == 3
    assert diag.http_consumer_coverage == 0.25


def test_unrecognised_calls_in_a_repo_with_no_contracts_still_count():
    """Nothing located anywhere, but files call out: 0%, not "undefined"."""
    diag = build_diagnostics([], [], {"web": {"call_files_unrecognised": 4}})
    assert diag.unrecognised_call_files == {"web": 4}
    assert diag.http_consumer_coverage == 0.0


def test_coverage_is_not_stated_before_the_probe_ran():
    """A ratio over located calls alone reads 100% whatever was missed."""
    diag = build_diagnostics([_consumer("web", "http::GET::/u")], [], {"web": {}})
    assert diag.unrecognised_call_files is None
    assert diag.http_consumer_coverage is None
