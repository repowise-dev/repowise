"""Directional compatibility coverage for complete OpenAPI wire schemas."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.breaking_change import (
    SEVERITY_BREAKING,
    SEVERITY_WARNING,
    detect_breaking_changes,
)
from repowise.core.workspace.contract_schema import ContractSchema, SchemaField, SchemaIssue
from repowise.core.workspace.contracts import Contract, ContractLink, ContractStore
from repowise.core.workspace.extractors.openapi import OpenApiExtractor

FIXTURES = Path(__file__).parents[2] / "fixtures" / "openapi_contracts"
CONTRACT_ID = "http::POST::/orders"


def _extract(name: str) -> Contract:
    content = (FIXTURES / name).read_text(encoding="utf-8")
    return OpenApiExtractor().extract(Path("."), "api", files=[("openapi.yaml", ".yaml", content)])[
        0
    ]


def _link(contract_id: str = CONTRACT_ID) -> ContractLink:
    return ContractLink(
        contract_id=contract_id,
        contract_type="http",
        match_type="exact",
        confidence=0.9,
        provider_repo="api",
        provider_file="openapi.yaml",
        provider_symbol="openapi:POST /orders",
        provider_service=None,
        consumer_repo="web",
        consumer_file="src/consumer.ts",
        consumer_symbol="createOrder",
        consumer_service=None,
    )


def _report(before: Contract, after: Contract):
    return detect_breaking_changes(
        ContractStore(contracts=[before], contract_links=[_link(before.contract_id)]),
        ContractStore(contracts=[after], contract_links=[_link(after.contract_id)]),
    )


def _wire_schema(
    *,
    request=None,
    response=None,
    request_state="complete",
    response_state="complete",
    **kwargs,
) -> ContractSchema:
    return ContractSchema(
        source="openapi",
        comparison_key="openapi-wire-v1",
        request_state=request_state,
        response_state=response_state,
        request_fields=request or [],
        response_fields=response or [],
        **kwargs,
    )


def _provider(schema: ContractSchema) -> Contract:
    return Contract(
        repo="api",
        contract_id=CONTRACT_ID,
        contract_type="http",
        role="provider",
        file_path="openapi.yaml",
        symbol_name="openapi:POST /orders",
        confidence=0.98,
        schema=schema,
    )


def test_accepted_30_widening_and_narrowing_pair_is_compatible() -> None:
    assert (
        _report(
            _extract("compatible-3.0-before.yaml"),
            _extract("compatible-3.0-after.yaml"),
        ).changes
        == []
    )


def test_accepted_31_nullability_reversals_are_compatible() -> None:
    assert (
        _report(
            _extract("compatible-3.1-before.yaml"),
            _extract("compatible-3.1-after.yaml"),
        ).changes
        == []
    )


def test_incompatible_fixture_reports_all_six_directional_changes_and_exposure() -> None:
    report = _report(
        _extract("incompatible-3.0-before.yaml"),
        _extract("incompatible-3.0-after.yaml"),
    )

    assert [(change.kind, change.side, change.field_name) for change in report.changes] == [
        ("field_enum_changed", "request", "body.priority"),
        ("field_enum_changed", "response", "response.status"),
        ("field_nullability_changed", "request", "body.note"),
        ("field_required", "request", "body.region"),
        ("field_required_relaxed", "response", "response.id"),
        ("removed_field", "response", "response.customer.email"),
    ]
    assert all(change.severity == SEVERITY_BREAKING for change in report.changes)
    assert all(change.comparison_source == "openapi" for change in report.changes)
    assert all(change.comparison_key == "openapi-wire-v1" for change in report.changes)
    assert all(change.impacted_consumers[0].file == "src/consumer.ts" for change in report.changes)


@pytest.mark.parametrize(
    ("side", "before", "after"),
    [
        ("request", SchemaField("value", "string", required=True), SchemaField("value", "string")),
        ("response", SchemaField("value", "string"), SchemaField("value", "string", required=True)),
        (
            "request",
            SchemaField("value", "string", nullable=False),
            SchemaField("value", "string", nullable=True),
        ),
        (
            "response",
            SchemaField("value", "string", nullable=True),
            SchemaField("value", "string", nullable=False),
        ),
        (
            "request",
            SchemaField("value", "string", enum_values=["a"]),
            SchemaField("value", "string", enum_values=["a", "b"]),
        ),
        (
            "response",
            SchemaField("value", "string", enum_values=["a", "b"]),
            SchemaField("value", "string", enum_values=["a"]),
        ),
    ],
)
def test_directional_reverse_is_compatible(
    side: str, before: SchemaField, after: SchemaField
) -> None:
    before_schema = _wire_schema(**{side: [before]})
    after_schema = _wire_schema(**{side: [after]})
    assert _report(_provider(before_schema), _provider(after_schema)).changes == []


def test_array_items_recurse_with_the_same_directional_rules() -> None:
    before = SchemaField(
        "orders",
        "array",
        items=SchemaField(
            "$items",
            "object",
            children=[SchemaField("id", "string", required=True)],
        ),
    )
    after = SchemaField(
        "orders",
        "array",
        items=SchemaField(
            "$items",
            "object",
            children=[SchemaField("id", "string")],
        ),
    )
    report = _report(
        _provider(_wire_schema(response=[before])),
        _provider(_wire_schema(response=[after])),
    )
    assert [(c.kind, c.field_name) for c in report.changes] == [
        ("field_required_relaxed", "orders[].id")
    ]


def test_incomplete_side_change_is_uncertainty_not_field_removal() -> None:
    before = _wire_schema(response=[SchemaField("id", "string")])
    after = _wire_schema(
        response_state="unresolved",
        issues=[SchemaIssue("openapi_ref_cycle", "response", "#/components/schemas/Node")],
    )

    report = _report(_provider(before), _provider(after))

    assert [(change.kind, change.severity, change.side) for change in report.changes] == [
        ("schema_comparison_uncertain", SEVERITY_WARNING, "response")
    ]


def test_response_selection_change_is_uncertainty_not_a_field_diff() -> None:
    field = SchemaField("id", "string", required=True)
    before = _wire_schema(
        response=[field], response_media_type="application/json", response_status_code="200"
    )
    after = _wire_schema(
        response=[field], response_media_type="application/json", response_status_code="201"
    )

    report = _report(_provider(before), _provider(after))

    assert [(change.kind, change.side) for change in report.changes] == [
        ("schema_comparison_uncertain", "response")
    ]


def test_schema_source_transition_is_uncertainty_not_field_changes() -> None:
    signature = ContractSchema(
        source="signature", request_fields=[SchemaField("id", "str", required=True)]
    )
    openapi = _wire_schema(request=[SchemaField("id", "integer")])

    report = _report(_provider(signature), _provider(openapi))

    assert [change.kind for change in report.changes] == ["schema_comparison_uncertain"]


@pytest.mark.parametrize(
    ("side", "before_values", "after_values"),
    [
        ("request", None, ["a"]),
        ("response", ["a"], None),
    ],
)
def test_enum_constraint_boundary_detects_incompatible_direction(
    side: str, before_values: list[str] | None, after_values: list[str] | None
) -> None:
    before = SchemaField("value", "string", enum_values=before_values)
    after = SchemaField("value", "string", enum_values=after_values)

    report = _report(
        _provider(_wire_schema(**{side: [before]})),
        _provider(_wire_schema(**{side: [after]})),
    )

    assert [(change.kind, change.side) for change in report.changes] == [
        ("field_enum_changed", side)
    ]


@pytest.mark.parametrize(
    ("side", "before_values", "after_values"),
    [
        ("request", ["a"], None),
        ("response", None, ["a"]),
    ],
)
def test_enum_constraint_boundary_allows_compatible_direction(
    side: str, before_values: list[str] | None, after_values: list[str] | None
) -> None:
    before = SchemaField("value", "string", enum_values=before_values)
    after = SchemaField("value", "string", enum_values=after_values)
    assert (
        _report(
            _provider(_wire_schema(**{side: [before]})),
            _provider(_wire_schema(**{side: [after]})),
        ).changes
        == []
    )


def test_number_enum_uses_json_numeric_equality() -> None:
    before = SchemaField("value", "number", enum_values=[1])
    after = SchemaField("value", "number", enum_values=[1.0])
    assert (
        _report(
            _provider(_wire_schema(request=[before])),
            _provider(_wire_schema(request=[after])),
        ).changes
        == []
    )
