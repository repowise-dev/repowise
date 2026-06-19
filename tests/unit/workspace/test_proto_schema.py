"""Tests for proto message-field schema capture and Contract schema round-trip."""

from __future__ import annotations

from repowise.core.workspace.contract_schema import ContractSchema, SchemaField
from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.grpc.proto import ProtoDialect, _parse_proto_file

PROTO = """
syntax = "proto3";
package auth;

message LoginRequest {
  string username = 1;
  int32 attempts = 2;
  repeated string scopes = 3;
  message Nested { string ignore_me = 9; }
  oneof credential { string password = 4; string token = 5; }
}

message LoginResponse {
  string session = 1;
}

service AuthService {
  rpc Login (LoginRequest) returns (auth.LoginResponse);
}
"""


def test_parse_messages_captures_fields_and_excludes_nested():
    _pkg, _svcs, messages = _parse_proto_file(PROTO)
    req = {f.name: f for f in messages["LoginRequest"]}
    assert set(req) == {"username", "attempts", "scopes", "password", "token"}
    assert req["username"].type == "string"
    assert req["username"].number == 1
    assert req["scopes"].repeated is True
    # The nested message's field must not leak into the parent.
    assert "ignore_me" not in req
    # Nested message is still parsed as its own entry.
    assert [f.name for f in messages["Nested"]] == ["ignore_me"]


def test_parse_rpc_resolves_request_and_response_types():
    _pkg, svcs, _messages = _parse_proto_file(PROTO)
    method = svcs[0].methods[0]
    assert method.name == "Login"
    assert method.request_type == "LoginRequest"
    # Qualified response type (auth.LoginResponse) is preserved.
    assert method.response_type == "auth.LoginResponse"


def test_dialect_attaches_schema_to_contract():
    ctx = ScanContext(repo_alias="api", rel_path="proto/auth.proto", suffix=".proto", content=PROTO)
    contracts = ProtoDialect().extract(ctx)
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract.contract_id == "grpc::auth.AuthService/Login"
    assert contract.schema is not None
    assert {f.name for f in contract.schema.request_fields} == {
        "username",
        "attempts",
        "scopes",
        "password",
        "token",
    }
    # Qualified response type resolves to the bare message name's fields.
    assert [f.name for f in contract.schema.response_fields] == ["session"]


def test_no_message_means_no_schema():
    proto = """
    syntax = "proto3";
    service Bare { rpc Ping (Missing) returns (AlsoMissing); }
    """
    ctx = ScanContext(repo_alias="api", rel_path="bare.proto", suffix=".proto", content=proto)
    contract = ProtoDialect().extract(ctx)[0]
    # Unknown messages → empty schema → not attached.
    assert contract.schema is None


def test_contract_schema_round_trip():
    schema = ContractSchema(
        source="proto",
        request_fields=[SchemaField("a", "string", required=True, number=1)],
        response_fields=[SchemaField("b", "int32", number=2, repeated=True)],
    )
    contract = Contract(
        repo="api",
        contract_id="grpc::S/M",
        contract_type="grpc",
        role="provider",
        file_path="s.proto",
        symbol_name="S/M",
        confidence=0.85,
        schema=schema,
    )
    restored = Contract.from_dict(contract.to_dict())
    assert restored.schema is not None
    assert restored.schema.request_fields[0].required is True
    assert restored.schema.request_fields[0].number == 1
    assert restored.schema.response_fields[0].repeated is True


def test_contract_without_schema_omits_key():
    contract = Contract(
        repo="api",
        contract_id="http::GET::/x",
        contract_type="http",
        role="provider",
        file_path="r.py",
        symbol_name="h",
        confidence=0.9,
    )
    assert "schema" not in contract.to_dict()
    assert Contract.from_dict(contract.to_dict()).schema is None
