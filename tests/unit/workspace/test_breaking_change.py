"""Tests for the breaking-change guard — diff rules, impact, negatives, IO.

The negative cases (added optional field, widened set, brand-new endpoint) are
the load-bearing ones: the guard must stay silent on non-breaking changes.
"""

from __future__ import annotations

from repowise.core.workspace.breaking_change import (
    SEVERITY_BREAKING,
    SEVERITY_WARNING,
    BreakingChangeReport,
    detect_breaking_changes,
    load_breaking_change_report,
    run_breaking_change_detection,
    save_breaking_change_report,
)
from repowise.core.workspace.contract_schema import ContractSchema, SchemaField
from repowise.core.workspace.contracts import Contract, ContractLink, ContractStore

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _provider(cid, ctype="http", repo="api", file="src/handler.py", service=None, schema=None):
    return Contract(
        repo=repo,
        contract_id=cid,
        contract_type=ctype,
        role="provider",
        file_path=file,
        symbol_name="handler",
        confidence=0.9,
        service=service,
        schema=schema,
    )


def _link(cid, ctype="http", c_repo="web", c_file="src/client.ts", c_service=None, match="exact"):
    return ContractLink(
        contract_id=cid,
        contract_type=ctype,
        match_type=match,
        confidence=0.9,
        provider_repo="api",
        provider_file="src/handler.py",
        provider_symbol="handler",
        provider_service=None,
        consumer_repo=c_repo,
        consumer_file=c_file,
        consumer_symbol="call",
        consumer_service=c_service,
    )


def _store(contracts=None, links=None):
    return ContractStore(contracts=contracts or [], contract_links=links or [])


def _schema(request=None, response=None):
    return ContractSchema(
        source="proto", request_fields=request or [], response_fields=response or []
    )


def _kinds(report):
    return sorted(c.kind for c in report.changes)


# ---------------------------------------------------------------------------
# Contract-level rule: removed endpoint
# ---------------------------------------------------------------------------


def test_removed_endpoint_with_consumer_is_breaking():
    prev = _store([_provider("http::GET::/users")], [_link("http::GET::/users")])
    report = detect_breaking_changes(prev, _store())
    assert _kinds(report) == ["removed_endpoint"]
    change = report.changes[0]
    assert change.severity == SEVERITY_BREAKING
    assert change.contract_type == "http"
    assert [c.file for c in change.impacted_consumers] == ["src/client.ts"]
    assert change.impacted_consumers[0].node_id == "web"


def test_removed_endpoint_without_consumer_still_flags_but_no_impact():
    prev = _store([_provider("http::GET::/internal")])  # no links
    report = detect_breaking_changes(prev, _store())
    assert _kinds(report) == ["removed_endpoint"]
    assert report.changes[0].impacted_consumers == []


def test_added_endpoint_does_not_flag():
    curr = _store([_provider("http::GET::/new")], [_link("http::GET::/new")])
    report = detect_breaking_changes(_store(), curr)
    assert report.changes == []


def test_unchanged_endpoint_does_not_flag():
    store = _store([_provider("http::GET::/users")], [_link("http::GET::/users")])
    # Distinct objects, same content.
    other = _store([_provider("http::GET::/users")], [_link("http::GET::/users")])
    report = detect_breaking_changes(store, other)
    assert report.changes == []


def test_grpc_removed_method_is_breaking():
    prev = _store(
        [_provider("grpc::auth.AuthService/Login", ctype="grpc")],
        [_link("grpc::auth.AuthService/Login", ctype="grpc")],
    )
    report = detect_breaking_changes(prev, _store())
    assert _kinds(report) == ["removed_endpoint"]


# ---------------------------------------------------------------------------
# Field-level rules
# ---------------------------------------------------------------------------


def test_added_optional_field_does_not_flag():
    prev_s = _schema(request=[SchemaField("a", "string", number=1)])
    curr_s = _schema(
        request=[SchemaField("a", "string", number=1), SchemaField("b", "int32", number=2)]
    )
    prev = _store([_provider("grpc::S/M", "grpc", schema=prev_s)])
    curr = _store([_provider("grpc::S/M", "grpc", schema=curr_s)])
    report = detect_breaking_changes(prev, curr)
    assert report.changes == []


def test_added_required_field_is_breaking():
    prev_s = _schema(request=[SchemaField("a", "string", number=1)])
    curr_s = _schema(
        request=[
            SchemaField("a", "string", number=1),
            SchemaField("b", "int32", number=2, required=True),
        ]
    )
    report = detect_breaking_changes(
        _store([_provider("grpc::S/M", "grpc", schema=prev_s)]),
        _store([_provider("grpc::S/M", "grpc", schema=curr_s)]),
    )
    assert _kinds(report) == ["field_required"]
    assert report.changes[0].field_name == "b"
    assert report.changes[0].severity == SEVERITY_BREAKING


def test_optional_to_required_is_breaking():
    prev_s = _schema(request=[SchemaField("a", "string", number=1)])
    curr_s = _schema(request=[SchemaField("a", "string", number=1, required=True)])
    report = detect_breaking_changes(
        _store([_provider("grpc::S/M", "grpc", schema=prev_s)]),
        _store([_provider("grpc::S/M", "grpc", schema=curr_s)]),
    )
    assert _kinds(report) == ["field_required"]


def test_removed_response_field_is_breaking_request_field_is_warning():
    prev_s = _schema(
        request=[SchemaField("a", "string", number=1)],
        response=[SchemaField("r", "string", number=1)],
    )
    curr_s = _schema(request=[], response=[])
    report = detect_breaking_changes(
        _store([_provider("grpc::S/M", "grpc", schema=prev_s)]),
        _store([_provider("grpc::S/M", "grpc", schema=curr_s)]),
    )
    by_sev = {c.severity for c in report.changes}
    assert _kinds(report) == ["removed_field", "removed_field"]
    assert by_sev == {SEVERITY_BREAKING, SEVERITY_WARNING}


def test_field_type_change_is_breaking():
    prev_s = _schema(request=[SchemaField("a", "string", number=1)])
    curr_s = _schema(request=[SchemaField("a", "int64", number=1)])
    report = detect_breaking_changes(
        _store([_provider("grpc::S/M", "grpc", schema=prev_s)]),
        _store([_provider("grpc::S/M", "grpc", schema=curr_s)]),
    )
    assert _kinds(report) == ["field_type_changed"]
    assert report.changes[0].old_value == "string"
    assert report.changes[0].new_value == "int64"


def test_field_number_change_is_breaking():
    prev_s = _schema(request=[SchemaField("a", "string", number=1)])
    curr_s = _schema(request=[SchemaField("a", "string", number=7)])
    report = detect_breaking_changes(
        _store([_provider("grpc::S/M", "grpc", schema=prev_s)]),
        _store([_provider("grpc::S/M", "grpc", schema=curr_s)]),
    )
    assert _kinds(report) == ["field_number_changed"]


def test_no_schema_means_no_field_rules():
    # Both sides present but neither carries a schema → only contract-level rules.
    prev = _store([_provider("http::GET::/users")], [_link("http::GET::/users")])
    curr = _store([_provider("http::GET::/users")], [_link("http::GET::/users")])
    report = detect_breaking_changes(prev, curr)
    assert report.changes == []


# ---------------------------------------------------------------------------
# Impact resolution + rollups
# ---------------------------------------------------------------------------


def test_impact_resolves_service_node_id_from_consumer_service():
    prev = _store(
        [_provider("http::GET::/users")],
        [
            _link(
                "http::GET::/users", c_repo="web", c_file="apps/store/c.ts", c_service="apps/store"
            )
        ],
    )
    report = detect_breaking_changes(prev, _store())
    consumer = report.changes[0].impacted_consumers[0]
    assert consumer.node_id == "web::apps/store"
    assert consumer.service == "apps/store"


def test_report_rollups_count_breaking_and_impact():
    prev = _store(
        [_provider("http::GET::/a"), _provider("http::GET::/b")],
        [_link("http::GET::/a", c_repo="web"), _link("http::GET::/b", c_repo="mobile")],
    )
    report = detect_breaking_changes(prev, _store())
    d = report.to_dict()
    assert d["total"] == 2
    assert d["breaking_count"] == 2
    assert d["warning_count"] == 0
    assert d["impacted_repos"] == ["mobile", "web"]
    assert d["total_impacted_consumers"] == 2


def test_changes_sorted_breaking_before_warning():
    prev_s = _schema(
        request=[SchemaField("req", "string", number=1)],
        response=[SchemaField("res", "string", number=1)],
    )
    curr_s = _schema(request=[], response=[])  # removed both → 1 warning + 1 breaking
    report = detect_breaking_changes(
        _store([_provider("grpc::S/M", "grpc", schema=prev_s)]),
        _store([_provider("grpc::S/M", "grpc", schema=curr_s)]),
    )
    assert report.changes[0].severity == SEVERITY_BREAKING
    assert report.changes[-1].severity == SEVERITY_WARNING


def test_empty_stores_produce_empty_report():
    report = detect_breaking_changes(_store(), _store())
    assert not report.has_changes
    assert report.to_dict()["total"] == 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_report_round_trips_through_dict():
    prev = _store([_provider("http::GET::/users")], [_link("http::GET::/users")])
    report = detect_breaking_changes(prev, _store(), generated_at="2026-06-19T00:00:00Z")
    restored = BreakingChangeReport.from_dict(report.to_dict())
    assert restored.generated_at == "2026-06-19T00:00:00Z"
    assert _kinds(restored) == _kinds(report)
    assert restored.changes[0].impacted_consumers[0].node_id == "web"
    assert restored.changes[0].provider_node_id == "api"


def test_save_and_load_report(tmp_path):
    prev = _store([_provider("http::GET::/users")], [_link("http::GET::/users")])
    run_breaking_change_detection(tmp_path, prev, _store())
    assert (tmp_path / ".repowise-workspace" / "breaking_changes.json").is_file()
    loaded = load_breaking_change_report(tmp_path)
    assert loaded is not None
    assert _kinds(loaded) == ["removed_endpoint"]


def test_load_missing_report_returns_none(tmp_path):
    assert load_breaking_change_report(tmp_path) is None


def test_save_returns_path(tmp_path):
    out = save_breaking_change_report(BreakingChangeReport(), tmp_path)
    assert out.name == "breaking_changes.json"
