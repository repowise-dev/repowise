"""Bounded OpenAPI 3.x provider-schema extraction.

This module deliberately implements only the approved common subset. It reuses
the workspace's shared file walk, never dereferences the network, and refuses a
whole request or response side when a construct cannot be represented faithfully.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from repowise.core.workspace.contract_schema import (
    ContractSchema,
    SchemaField,
    SchemaIssue,
)

from .base import SourceFile, select_files
from .from_index import EXTRACTION_LAYER_KEY
from .http.paths import normalize_http_path

OPENAPI_SCHEMA_SOURCE = "openapi"
OPENAPI_COMPARISON_KEY = "openapi-wire-v1"
OPENAPI_EXTRACTION_LAYER = "openapi"

_EXTENSIONS = frozenset({".json", ".yaml", ".yml"})
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_PARAMETER_LOCATIONS = frozenset({"path", "query", "header", "cookie"})
_SUPPORTED_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean"})
_VERSION_RE = re.compile(r"^3\.(0|1|2)\.\d+(?:[-+].*)?$")
_SUCCESS_STATUS_RE = re.compile(r"^2\d\d$")
_MAX_SCHEMA_DEPTH = 32

_COMPOSITION_KEYS = frozenset({"allOf", "oneOf", "anyOf", "not"})
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "patternProperties",
        "prefixItems",
        "contains",
        "unevaluatedProperties",
        "unevaluatedItems",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "propertyNames",
        "discriminator",
        "readOnly",
        "writeOnly",
        "format",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)


class _Refusal(Exception):  # noqa: N818 - domain outcome, not a surfaced error
    def __init__(self, code: str, status: str, pointer: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.pointer = pointer
        self.detail = detail


def _increment(stats: dict[str, int] | None, key: str, amount: int = 1) -> None:
    if stats is not None:
        stats[key] = stats.get(key, 0) + amount


def _record_reason(stats: dict[str, int] | None, code: str) -> None:
    """Record refusal reasons separately from stable coverage counters."""
    _increment(stats, f"openapi_reason_{code.removeprefix('openapi_')}")


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _child_pointer(pointer: str, value: str) -> str:
    return f"{pointer}/{_pointer_token(value)}"


def _is_candidate(rel_path: str) -> bool:
    name = rel_path.rsplit("/", 1)[-1].lower()
    return name in {
        "openapi.json",
        "openapi.yaml",
        "openapi.yml",
        "swagger.json",
        "swagger.yaml",
        "swagger.yml",
    } or any(
        name.endswith(suffix)
        for suffix in (".openapi.json", ".openapi.yaml", ".openapi.yml")
    )


def _load_document(content: str, suffix: str, rel_path: str) -> Mapping[str, Any]:
    try:
        value = json.loads(content) if suffix == ".json" else yaml.safe_load(content)
    except (json.JSONDecodeError, yaml.YAMLError, RecursionError) as exc:
        raise _Refusal("openapi_parse_error", "unresolved", rel_path, str(exc)) from exc
    if not isinstance(value, Mapping):
        raise _Refusal(
            "openapi_document_not_mapping",
            "unresolved",
            rel_path,
            "document root must be a mapping",
        )
    return value


def _lookup_pointer(document: Mapping[str, Any], ref: str, source_pointer: str) -> Any:
    if not ref.startswith("#/"):
        raise _Refusal(
            "openapi_remote_ref",
            "unsupported",
            source_pointer,
            ref,
        )
    current: Any = document
    for encoded in unquote(ref[2:]).split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise _Refusal(
                "openapi_ref_unresolved",
                "unresolved",
                source_pointer,
                ref,
            )
    return current


def _resolve_object(
    document: Mapping[str, Any],
    value: Any,
    pointer: str,
    seen_refs: frozenset[str] = frozenset(),
    depth: int = 0,
) -> tuple[Mapping[str, Any], str, frozenset[str], int]:
    if depth > _MAX_SCHEMA_DEPTH:
        raise _Refusal("openapi_depth_exceeded", "unresolved", pointer)
    if not isinstance(value, Mapping):
        raise _Refusal("openapi_object_expected", "unresolved", pointer)
    if "$ref" not in value:
        return value, pointer, seen_refs, depth
    if len(value) != 1:
        raise _Refusal("openapi_ref_siblings_unsupported", "unsupported", pointer)
    ref = value.get("$ref")
    if not isinstance(ref, str):
        raise _Refusal("openapi_ref_invalid", "unresolved", pointer)
    if ref in seen_refs:
        raise _Refusal("openapi_ref_cycle", "unresolved", pointer, ref)
    resolved = _lookup_pointer(document, ref, pointer)
    return _resolve_object(document, resolved, ref, seen_refs | {ref}, depth + 1)


def _normalized_type(
    schema: Mapping[str, Any], version_family: str, pointer: str
) -> tuple[str, bool]:
    raw_type = schema.get("type")
    nullable = False
    if isinstance(raw_type, list):
        if version_family == "0":
            raise _Refusal("openapi_type_union_unsupported", "unsupported", pointer)
        if not all(isinstance(item, str) for item in raw_type):
            raise _Refusal("openapi_type_union_invalid", "unresolved", pointer)
        members = list(dict.fromkeys(raw_type))
        concrete = [item for item in members if item != "null"]
        if len(concrete) != 1 or len(concrete) == len(members):
            raise _Refusal("openapi_type_union_unsupported", "unsupported", pointer)
        raw_type = concrete[0]
        nullable = True
    if not isinstance(raw_type, str):
        raise _Refusal("openapi_type_missing", "unresolved", pointer)
    if raw_type not in _SUPPORTED_TYPES:
        raise _Refusal("openapi_type_unsupported", "unsupported", pointer, raw_type)
    if version_family == "0":
        raw_nullable = schema.get("nullable", False)
        if not isinstance(raw_nullable, bool):
            raise _Refusal("openapi_nullable_invalid", "unresolved", pointer)
        nullable = raw_nullable
    elif "nullable" in schema:
        raise _Refusal("openapi_nullable_keyword_unsupported", "unsupported", pointer)
    return raw_type, nullable


def _value_matches_type(value: Any, type_name: str) -> bool:
    if value is None:
        return False
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _enum_values(
    schema: Mapping[str, Any], type_name: str, nullable: bool, pointer: str
) -> list[Any] | None:
    if "enum" not in schema:
        return None
    raw = schema["enum"]
    if not isinstance(raw, list):
        raise _Refusal("openapi_enum_invalid", "unresolved", pointer)
    if type_name not in {"string", "integer", "number", "boolean"}:
        raise _Refusal("openapi_enum_unsupported", "unsupported", pointer)
    for value in raw:
        if value is None and nullable:
            continue
        if not _value_matches_type(value, type_name):
            raise _Refusal("openapi_enum_mixed_types", "unsupported", pointer)
    return list(raw)


def _parse_schema_node(
    document: Mapping[str, Any],
    value: Any,
    *,
    name: str,
    required: bool,
    location: str | None,
    pointer: str,
    version_family: str,
    seen_refs: frozenset[str] = frozenset(),
    depth: int = 0,
) -> SchemaField:
    schema, resolved_pointer, seen_refs, depth = _resolve_object(
        document, value, pointer, seen_refs, depth
    )
    composition = sorted(_COMPOSITION_KEYS.intersection(schema))
    if composition:
        raise _Refusal(
            "openapi_composition_unsupported",
            "unsupported",
            _child_pointer(resolved_pointer, composition[0]),
            composition[0],
        )
    unsupported = sorted(_UNSUPPORTED_SCHEMA_KEYS.intersection(schema))
    if unsupported:
        raise _Refusal(
            "openapi_schema_keyword_unsupported",
            "unsupported",
            _child_pointer(resolved_pointer, unsupported[0]),
            unsupported[0],
        )

    type_name, nullable = _normalized_type(schema, version_family, resolved_pointer)
    enum_values = _enum_values(schema, type_name, nullable, resolved_pointer)
    children: list[SchemaField] = []
    items: SchemaField | None = None

    if type_name == "object":
        properties = schema.get("properties", {})
        required_names = schema.get("required", [])
        if not isinstance(properties, Mapping):
            raise _Refusal("openapi_properties_invalid", "unresolved", resolved_pointer)
        if not isinstance(required_names, list) or not all(
            isinstance(item, str) for item in required_names
        ):
            raise _Refusal("openapi_required_invalid", "unresolved", resolved_pointer)
        unknown_required = set(required_names).difference(properties)
        if unknown_required:
            raise _Refusal(
                "openapi_required_property_unresolved",
                "unresolved",
                resolved_pointer,
                sorted(unknown_required)[0],
            )
        for child_name, child_schema in properties.items():
            if not isinstance(child_name, str):
                raise _Refusal("openapi_property_name_invalid", "unresolved", resolved_pointer)
            children.append(
                _parse_schema_node(
                    document,
                    child_schema,
                    name=child_name,
                    required=child_name in required_names,
                    location=None,
                    pointer=_child_pointer(
                        _child_pointer(resolved_pointer, "properties"), child_name
                    ),
                    version_family=version_family,
                    seen_refs=seen_refs,
                    depth=depth + 1,
                )
            )
    elif type_name == "array":
        if "items" not in schema:
            raise _Refusal("openapi_array_items_missing", "unresolved", resolved_pointer)
        items = _parse_schema_node(
            document,
            schema["items"],
            name="$items",
            required=True,
            location=None,
            pointer=_child_pointer(resolved_pointer, "items"),
            version_family=version_family,
            seen_refs=seen_refs,
            depth=depth + 1,
        )

    return SchemaField(
        name=name,
        type=type_name,
        required=required,
        nullable=nullable,
        enum_values=enum_values,
        location=location,
        source_pointer=resolved_pointer,
        children=children,
        items=items,
    )


def _parameter_entries(
    document: Mapping[str, Any],
    path_item: Mapping[str, Any],
    operation: Mapping[str, Any],
    pointer: str,
) -> list[tuple[Mapping[str, Any], str]]:
    merged: dict[tuple[str, str], tuple[Mapping[str, Any], str]] = {}
    for owner, owner_pointer in ((path_item, pointer.rsplit("/", 1)[0]), (operation, pointer)):
        raw_parameters = owner.get("parameters", [])
        if not isinstance(raw_parameters, list):
            raise _Refusal("openapi_parameters_invalid", "unresolved", owner_pointer)
        for index, raw in enumerate(raw_parameters):
            item_pointer = _child_pointer(_child_pointer(owner_pointer, "parameters"), str(index))
            parameter, resolved_pointer, _, _ = _resolve_object(document, raw, item_pointer)
            name = parameter.get("name")
            location = parameter.get("in")
            if not isinstance(name, str) or not isinstance(location, str):
                raise _Refusal("openapi_parameter_identity_invalid", "unresolved", resolved_pointer)
            merged[(location, name)] = (parameter, resolved_pointer)
    return list(merged.values())


def _build_request(
    document: Mapping[str, Any],
    path_item: Mapping[str, Any],
    operation: Mapping[str, Any],
    pointer: str,
    version_family: str,
) -> tuple[list[SchemaField], str | None]:
    fields: list[SchemaField] = []
    for parameter, parameter_pointer in _parameter_entries(document, path_item, operation, pointer):
        location = str(parameter["in"])
        if location not in _PARAMETER_LOCATIONS:
            raise _Refusal(
                "openapi_parameter_location_unsupported",
                "unsupported",
                parameter_pointer,
                location,
            )
        if "content" in parameter:
            raise _Refusal(
                "openapi_parameter_content_unsupported", "unsupported", parameter_pointer
            )
        if "schema" not in parameter:
            raise _Refusal("openapi_parameter_schema_missing", "unresolved", parameter_pointer)
        required = bool(parameter.get("required", False))
        if location == "path" and not required:
            raise _Refusal("openapi_path_parameter_optional", "unresolved", parameter_pointer)
        fields.append(
            _parse_schema_node(
                document,
                parameter["schema"],
                name=str(parameter["name"]),
                required=required,
                location=location,
                pointer=_child_pointer(parameter_pointer, "schema"),
                version_family=version_family,
            )
        )

    if "requestBody" not in operation:
        return fields, None
    body_pointer = _child_pointer(pointer, "requestBody")
    body, body_pointer, _, _ = _resolve_object(document, operation["requestBody"], body_pointer)
    content = body.get("content")
    if not isinstance(content, Mapping) or not content:
        raise _Refusal("openapi_request_content_missing", "unresolved", body_pointer)
    if "application/json" not in content:
        raise _Refusal("openapi_media_type_unsupported", "unsupported", body_pointer)
    if len(content) != 1:
        raise _Refusal("openapi_media_type_ambiguous", "unsupported", body_pointer)
    media = content["application/json"]
    if not isinstance(media, Mapping) or "schema" not in media:
        raise _Refusal("openapi_request_schema_missing", "unresolved", body_pointer)
    fields.append(
        _parse_schema_node(
            document,
            media["schema"],
            name="$body",
            required=bool(body.get("required", False)),
            location="body",
            pointer=_child_pointer(
                _child_pointer(_child_pointer(body_pointer, "content"), "application/json"),
                "schema",
            ),
            version_family=version_family,
        )
    )
    return fields, "application/json"


def _build_response(
    document: Mapping[str, Any], operation: Mapping[str, Any], pointer: str, version_family: str
) -> tuple[list[SchemaField], str | None, str]:
    responses = operation.get("responses")
    if not isinstance(responses, Mapping):
        raise _Refusal("openapi_responses_missing", "unresolved", pointer)
    successes = [
        (str(code), value)
        for code, value in responses.items()
        if _SUCCESS_STATUS_RE.fullmatch(str(code))
    ]
    if len(successes) != 1:
        code = (
            "openapi_success_response_missing"
            if not successes
            else "openapi_success_response_ambiguous"
        )
        raise _Refusal(code, "unresolved", _child_pointer(pointer, "responses"))
    status_code, raw_response = successes[0]
    response_pointer = _child_pointer(_child_pointer(pointer, "responses"), status_code)
    response, response_pointer, _, _ = _resolve_object(document, raw_response, response_pointer)
    content = response.get("content")
    if not isinstance(content, Mapping) or not content:
        raise _Refusal("openapi_response_content_missing", "unresolved", response_pointer)
    if "application/json" not in content:
        raise _Refusal("openapi_media_type_unsupported", "unsupported", response_pointer)
    if len(content) != 1:
        raise _Refusal("openapi_media_type_ambiguous", "unsupported", response_pointer)
    media = content["application/json"]
    if not isinstance(media, Mapping) or "schema" not in media:
        raise _Refusal("openapi_response_schema_missing", "unresolved", response_pointer)
    root = _parse_schema_node(
        document,
        media["schema"],
        name="$response",
        required=True,
        location="body",
        pointer=_child_pointer(
            _child_pointer(_child_pointer(response_pointer, "content"), "application/json"),
            "schema",
        ),
        version_family=version_family,
    )
    return [root], "application/json", status_code


def _capture_side(
    side: str,
    builder: Callable[[], tuple[list[SchemaField], ...]],
    stats: dict[str, int] | None,
) -> tuple[tuple[Any, ...], str, list[SchemaIssue]]:
    try:
        result = builder()
    except _Refusal as exc:
        _increment(stats, f"openapi_{side}_{exc.status}")
        _record_reason(stats, exc.code)
        issue = SchemaIssue(exc.code, side, exc.pointer, exc.detail)
        return ([],), exc.status, [issue]
    _increment(stats, f"openapi_{side}_complete")
    return result, "complete", []


def _operation_contract(
    document: Mapping[str, Any],
    version: str,
    version_family: str,
    rel_path: str,
    repo_alias: str,
    path: str,
    method: str,
    path_item: Mapping[str, Any],
    operation: Mapping[str, Any],
    stats: dict[str, int] | None,
) -> Any:
    from repowise.core.workspace.contracts import Contract

    operation_pointer = f"#/paths/{_pointer_token(path)}/{method}"
    request_result, request_state, request_issues = _capture_side(
        "request",
        lambda: _build_request(document, path_item, operation, operation_pointer, version_family),
        stats,
    )
    response_result, response_state, response_issues = _capture_side(
        "response",
        lambda: _build_response(document, operation, operation_pointer, version_family),
        stats,
    )
    request_fields = request_result[0]
    request_media_type = request_result[1] if len(request_result) > 1 else None
    response_fields = response_result[0]
    response_media_type = response_result[1] if len(response_result) > 1 else None
    response_status_code = response_result[2] if len(response_result) > 2 else None
    normalized_path = normalize_http_path(path)
    return Contract(
        repo=repo_alias,
        contract_id=f"http::{method.upper()}::{normalized_path}",
        contract_type="http",
        role="provider",
        file_path=rel_path,
        symbol_name=f"openapi:{method.upper()} {path}",
        confidence=0.98,
        meta={
            EXTRACTION_LAYER_KEY: OPENAPI_EXTRACTION_LAYER,
            "openapi_version": version,
            "schema_source_file": rel_path,
            "schema_operation_pointer": operation_pointer,
        },
        schema=ContractSchema(
            source=OPENAPI_SCHEMA_SOURCE,
            request_fields=request_fields,
            response_fields=response_fields,
            source_version=version,
            comparison_key=OPENAPI_COMPARISON_KEY,
            comparison_ready=False,
            request_state=request_state,
            response_state=response_state,
            request_media_type=request_media_type,
            response_media_type=response_media_type,
            response_status_code=response_status_code,
            issues=request_issues + response_issues,
        ),
    )


class OpenApiExtractor:
    """Extract HTTP provider contracts and wire shapes from OpenAPI 3.x files."""

    @classmethod
    def source_extensions(cls) -> frozenset[str]:
        return _EXTENSIONS

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
        files: Sequence[SourceFile] | None = None,
        stats: dict[str, int] | None = None,
    ) -> list[Any]:
        contracts: list[Any] = []
        for rel_path, suffix, content in select_files(repo_path, _EXTENSIONS, exclude, files):
            if not _is_candidate(rel_path):
                continue
            _increment(stats, "openapi_documents")
            try:
                document = _load_document(content, suffix, rel_path)
                version = document.get("openapi")
                match = _VERSION_RE.fullmatch(version) if isinstance(version, str) else None
                if match is None:
                    raise _Refusal(
                        "openapi_version_unsupported",
                        "unsupported",
                        f"{rel_path}#/openapi",
                        str(version or "missing"),
                    )
                paths = document.get("paths")
                if not isinstance(paths, Mapping):
                    raise _Refusal("openapi_paths_missing", "unresolved", f"{rel_path}#/paths")
            except _Refusal as exc:
                _increment(stats, "openapi_documents_unresolved")
                _record_reason(stats, exc.code)
                continue

            _increment(stats, "openapi_documents_parsed")
            for path, raw_path_item in paths.items():
                if not isinstance(path, str) or not path.startswith("/"):
                    _record_reason(stats, "openapi_path_unsupported")
                    continue
                path_pointer = f"#/paths/{_pointer_token(path)}"
                try:
                    path_item, _, _, _ = _resolve_object(document, raw_path_item, path_pointer)
                except _Refusal as exc:
                    _record_reason(stats, exc.code)
                    continue
                for method, raw_operation in path_item.items():
                    method_text = str(method).lower()
                    if method_text not in _HTTP_METHODS:
                        continue
                    if not isinstance(raw_operation, Mapping):
                        _record_reason(stats, "openapi_operation_invalid")
                        continue
                    contracts.append(
                        _operation_contract(
                            document,
                            version,
                            match.group(1),
                            rel_path,
                            repo_alias,
                            path,
                            method_text,
                            path_item,
                            raw_operation,
                            stats,
                        )
                    )
                    _increment(stats, "openapi_operations")
        _increment(stats, "openapi_providers", len(contracts))
        return contracts


def merge_openapi_providers(contracts: list[Any], stats: dict[str, int]) -> list[Any]:
    """Attach each unambiguous spec schema to one matching code provider."""
    from repowise.core.workspace.contracts import normalize_contract_id

    def spec_key(contract: Any) -> tuple[str, str | None, str]:
        return (contract.repo, contract.service, normalize_contract_id(contract.contract_id))

    def route_key(contract: Any) -> tuple[str, str]:
        return (contract.repo, normalize_contract_id(contract.contract_id))

    source_rows: list[Any] = []
    spec_groups: dict[tuple[str, str | None, str], list[Any]] = defaultdict(list)
    for contract in contracts:
        if contract.schema is not None and contract.schema.source == OPENAPI_SCHEMA_SOURCE:
            spec_groups[spec_key(contract)].append(contract)
        else:
            source_rows.append(contract)

    providers: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for contract in source_rows:
        if contract.role == "provider" and contract.contract_type == "http":
            providers[route_key(contract)].append(contract)

    retained_specs: list[Any] = []
    for group_key, rows in spec_groups.items():
        selected = rows[0]
        if len(rows) > 1:
            _increment(stats, "openapi_duplicate_operation", len(rows) - 1)
            shapes = {json.dumps(row.schema.to_dict(), sort_keys=True) for row in rows}
            if len(shapes) > 1:
                selected.schema = ContractSchema(
                    source=OPENAPI_SCHEMA_SOURCE,
                    source_version=selected.schema.source_version,
                    comparison_key=OPENAPI_COMPARISON_KEY,
                    comparison_ready=False,
                    request_state="unresolved",
                    response_state="unresolved",
                    issues=[
                        SchemaIssue(
                            "openapi_duplicate_operation_conflict",
                            "both",
                            str(selected.meta.get("schema_operation_pointer", "")),
                        )
                    ],
                )
                _record_reason(stats, "openapi_duplicate_operation_conflict")

        repo, service, normalized_id = group_key
        targets = providers.get((repo, normalized_id), [])
        if service is not None:
            targets = [target for target in targets if target.service == service]
        if not targets:
            retained_specs.append(selected)
            _increment(stats, "openapi_spec_only_providers")
            continue
        if len(targets) > 1:
            retained_specs.append(selected)
            _record_reason(stats, "openapi_provider_ambiguous")
            continue
        target = targets[0]
        if target.schema is not None:
            retained_specs.append(selected)
            _record_reason(stats, "openapi_existing_schema_conflict")
            continue
        target.schema = selected.schema
        target.meta["schema_source_file"] = selected.file_path
        target.meta["schema_operation_pointer"] = selected.meta.get(
            "schema_operation_pointer", ""
        )
        target.meta["openapi_version"] = selected.meta.get("openapi_version", "")
        _increment(stats, "openapi_schemas_merged")
    return source_rows + retained_specs


__all__ = [
    "OPENAPI_COMPARISON_KEY",
    "OPENAPI_SCHEMA_SOURCE",
    "OpenApiExtractor",
    "merge_openapi_providers",
]
