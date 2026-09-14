"""Focused coverage for bounded OpenAPI 3.x schema extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace import contracts as contracts_module
from repowise.core.workspace.config import ContractConfig, RepoEntry, WorkspaceConfig
from repowise.core.workspace.contract_schema import ContractSchema, SchemaField
from repowise.core.workspace.contracts import Contract, run_contract_extraction
from repowise.core.workspace.diagnostics import ExtractionDiagnostics, OpenApiCoverage
from repowise.core.workspace.extractors.openapi import (
    OpenApiExtractor,
    merge_openapi_providers,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "openapi_contracts"


def _extract(name: str) -> tuple[list[Contract], dict[str, int]]:
    content = (FIXTURES / name).read_text(encoding="utf-8")
    stats: dict[str, int] = {}
    rows = OpenApiExtractor().extract(
        Path("."), "api", files=[("openapi.yaml", ".yaml", content)], stats=stats
    )
    return rows, stats


def _field(fields: list[SchemaField], name: str) -> SchemaField:
    return next(item for item in fields if item.name == name)


def test_openapi_30_recovers_recursive_shapes_enums_and_provenance() -> None:
    rows, stats = _extract("compatible-3.0-before.yaml")

    assert len(rows) == 1
    row = rows[0]
    assert row.contract_id == "http::POST::/orders"
    assert row.meta["schema_operation_pointer"] == "#/paths/~1orders/post"
    assert row.schema is not None
    assert row.schema.source_version == "3.0.4"
    assert row.schema.comparison_ready is True
    assert row.schema.request_state == row.schema.response_state == "complete"

    body = row.schema.request_fields[0]
    assert body.type == "object"
    assert _field(body.children, "sku").required is True
    assert _field(body.children, "priority").enum_values == ["standard", "express"]
    assert _field(row.schema.response_fields[0].children, "note").nullable is True
    assert stats == {
        "openapi_documents": 1,
        "openapi_documents_parsed": 1,
        "openapi_request_complete": 1,
        "openapi_response_complete": 1,
        "openapi_operations": 1,
        "openapi_providers": 1,
    }


def test_openapi_31_normalizes_union_nullability() -> None:
    rows, _ = _extract("compatible-3.1-after.yaml")

    schema = rows[0].schema
    assert schema is not None
    note = _field(schema.request_fields[0].children, "note")
    assert (note.type, note.nullable) == ("string", True)


def test_parameters_merge_by_location_and_operation_overrides_path_item() -> None:
    rows, _ = _extract("parameters-3.0.yaml")

    schema = rows[0].schema
    assert schema is not None
    assert [(field.location, field.name) for field in schema.request_fields] == [
        ("path", "userId"),
        ("header", "trace"),
        ("query", "verbose"),
    ]
    assert _field(schema.request_fields, "trace").required is True
    response = schema.response_fields[0]
    assert response.type == "array"
    assert response.items is not None
    assert _field(response.items.children, "id").type == "integer"


@pytest.mark.parametrize(
    ("serialization", "expected_state"),
    [
        ("style: form\n          explode: true\n          allowReserved: false", "complete"),
        ("style: pipeDelimited", "unsupported"),
        ("style: form\n          explode: false", "unsupported"),
        ("style: form\n          allowReserved: true", "unsupported"),
    ],
)
def test_parameter_serialization_is_complete_only_for_modeled_defaults(
    serialization: str, expected_state: str
) -> None:
    document = f"""
openapi: 3.0.4
paths:
  /search:
    get:
      parameters:
        - in: query
          name: tags
          {serialization}
          schema:
            type: array
            items: {{type: string}}
      responses:
        '204': {{description: empty}}
"""
    rows = OpenApiExtractor().extract(Path("."), "api", files=[("openapi.yaml", ".yaml", document)])

    assert rows[0].schema is not None
    assert rows[0].schema.request_state == expected_state
    if expected_state == "unsupported":
        assert rows[0].schema.issues[0].code == "openapi_parameter_serialization_unsupported"


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
def test_non_finite_numeric_enums_are_unresolved(value: str) -> None:
    document = f"""
openapi: 3.0.4
paths:
  /numbers:
    get:
      parameters:
        - in: query
          name: value
          schema:
            type: number
            enum: [{value}]
      responses:
        '204': {{description: empty}}
"""
    rows = OpenApiExtractor().extract(Path("."), "api", files=[("openapi.yaml", ".yaml", document)])

    assert rows[0].schema is not None
    assert rows[0].schema.request_state == "unresolved"
    assert rows[0].schema.issues[0].code == "openapi_enum_number_non_finite"


def test_unsupported_constructs_refuse_only_the_affected_side() -> None:
    rows, stats = _extract("unresolved-3.2.yaml")

    assert len(rows) == 4
    by_path = {row.contract_id: row.schema for row in rows}
    assert by_path["http::GET::/remote"].response_state == "unsupported"
    assert by_path["http::POST::/choice"].request_state == "unsupported"
    assert by_path["http::POST::/choice"].response_state == "unresolved"
    assert by_path["http::GET::/tree"].response_state == "unresolved"
    assert by_path["http::GET::/xml"].response_state == "unsupported"
    assert {issue.code for schema in by_path.values() if schema for issue in schema.issues} == {
        "openapi_remote_ref",
        "openapi_composition_unsupported",
        "openapi_response_content_missing",
        "openapi_ref_cycle",
        "openapi_media_type_unsupported",
    }
    coverage = OpenApiCoverage.from_stats({"api": stats})
    assert coverage.operations == 4
    assert coverage.response_states == {
        "complete": 0,
        "partial": 0,
        "unsupported": 2,
        "unresolved": 2,
    }
    assert coverage.refusal_reasons["remote_ref"] == 1
    payload = ExtractionDiagnostics(openapi=coverage).to_dict()
    assert ExtractionDiagnostics.from_dict(payload).openapi == coverage


def test_legacy_schema_serialization_is_unchanged_and_new_shape_round_trips() -> None:
    legacy = ContractSchema(
        source="proto", request_fields=[SchemaField("id", "string", required=True)]
    )
    assert legacy.to_dict() == {
        "source": "proto",
        "request_fields": [{"name": "id", "type": "string", "required": True}],
        "response_fields": [],
    }

    rows, _ = _extract("compatible-3.0-before.yaml")
    schema_payload = rows[0].schema.to_dict()
    assert ContractSchema.from_dict(schema_payload).to_dict() == schema_payload
    contract_payload = rows[0].to_dict()
    assert Contract.from_dict(contract_payload).to_dict() == contract_payload


def test_merge_enriches_code_provider_and_keeps_spec_only_operations() -> None:
    spec_rows, _ = _extract("compatible-3.0-before.yaml")
    spec = spec_rows[0]
    code = Contract(
        repo="api",
        contract_id=spec.contract_id,
        contract_type="http",
        role="provider",
        file_path="src/orders.py",
        symbol_name="create_order",
        confidence=0.9,
    )
    extra = Contract.from_dict(spec.to_dict())
    extra.contract_id = "http::GET::/health"
    stats: dict[str, int] = {}

    merged = merge_openapi_providers([code, spec, extra], stats)

    assert merged == [code, extra]
    assert code.schema == spec.schema
    assert code.meta["schema_source_file"] == "openapi.yaml"
    assert stats == {"openapi_schemas_merged": 1, "openapi_spec_only_providers": 1}


def test_merge_retains_spec_when_multiple_code_providers_are_ambiguous() -> None:
    spec = _extract("compatible-3.0-before.yaml")[0][0]
    providers = [
        Contract(
            repo="api",
            contract_id=spec.contract_id,
            contract_type="http",
            role="provider",
            file_path=f"services/{name}/orders.py",
            symbol_name="create_order",
            confidence=0.9,
            service=name,
        )
        for name in ("one", "two")
    ]
    stats: dict[str, int] = {}

    merged = merge_openapi_providers([*providers, spec], stats)

    assert merged == [*providers, spec]
    assert all(provider.schema is None for provider in providers)
    assert stats == {"openapi_reason_provider_ambiguous": 1}


def test_non_candidates_and_openapi_2_are_not_extracted() -> None:
    stats: dict[str, int] = {}
    rows = OpenApiExtractor().extract(
        Path("."),
        "api",
        files=[
            ("schema.yaml", ".yaml", "openapi: 3.0.0\npaths: {}\n"),
            ("swagger.json", ".json", '{"swagger": "2.0", "paths": {}}'),
        ],
        stats=stats,
    )

    assert rows == []
    assert stats["openapi_documents"] == 1
    assert stats["openapi_documents_unresolved"] == 1
    assert stats["openapi_reason_version_unsupported"] == 1


def test_malformed_union_and_excessive_parse_depth_become_diagnostics() -> None:
    malformed_union = """
openapi: 3.1.0
paths:
  /bad:
    get:
      responses:
        '200':
          description: bad union
          content:
            application/json:
              schema: {type: [string, {bad: value}]}
"""
    stats: dict[str, int] = {}
    rows = OpenApiExtractor().extract(
        Path("."),
        "api",
        files=[("openapi.yaml", ".yaml", malformed_union)],
        stats=stats,
    )
    assert rows[0].schema.response_state == "unresolved"
    assert rows[0].schema.issues[0].code == "openapi_type_union_invalid"

    deep_stats: dict[str, int] = {}
    deep_yaml = "openapi: 3.1.0\npaths: " + "[" * 3000 + "]" * 3000
    assert (
        OpenApiExtractor().extract(
            Path("."),
            "api",
            files=[("openapi.yaml", ".yaml", deep_yaml)],
            stats=deep_stats,
        )
        == []
    )
    assert deep_stats["openapi_reason_parse_error"] == 1


@pytest.mark.asyncio
async def test_workspace_orchestrator_extracts_openapi_in_its_single_repo_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = tmp_path / "api"
    web = tmp_path / "web"
    for repo in (api, web):
        (repo / ".repowise").mkdir(parents=True)
    (api / "openapi.yaml").write_text(
        (FIXTURES / "compatible-3.0-before.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = WorkspaceConfig(
        repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")],
        contracts=ContractConfig(),
    )
    monkeypatch.setattr(contracts_module, "save_contract_store", lambda store, root: root)

    store = await run_contract_extraction(config, tmp_path, [])

    provider = next(row for row in store.contracts if row.contract_id == "http::POST::/orders")
    assert provider.schema is not None
    assert provider.schema.source == "openapi"
    assert store.extraction_stats["api"]["walks"] == 1
    assert store.extraction_stats["api"]["openapi_documents_parsed"] == 1
