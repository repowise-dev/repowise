"""Protobuf IDL gRPC provider dialect.

Parses ``.proto`` service/rpc declarations into provider contracts. Uses a
brace-depth counter so braces inside comments, options, or message bodies don't
break service-block extraction.

Beyond service/method identity, this dialect also recovers the **message field
shape** of each rpc's request and response (name, type, field number, label) and
attaches it as a :class:`~repowise.core.workspace.contract_schema.ContractSchema`
so the breaking-change guard can diff field-level changes (a removed or retyped
field, a reused field number). Message parsing reuses the same brace-depth walk
as service parsing — no new parser.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from repowise.core.workspace.contract_schema import ContractSchema, SchemaField

from ..langs import PROTO
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


class ProtoMethod(NamedTuple):
    """One ``rpc`` declaration: method name + request/response message types."""

    name: str
    request_type: str
    response_type: str


def _strip_comments(content: str) -> str:
    """Remove ``//`` and ``/* */`` comments so field/rpc regexes stay simple.

    Block-comment removal preserves newlines so brace-depth walking over the
    original is unaffected; this stripped copy is only used for line-oriented
    field and rpc parsing.
    """
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    content = re.sub(r"//[^\n]*", "", content)
    return content


def _extract_blocks(content: str, keyword: str) -> list[tuple[str, str]]:
    """Extract ``(name, body)`` pairs for every ``<keyword> Name { ... }`` block.

    Uses brace-depth counting so nested braces in comments, options, or nested
    message bodies don't break parsing. Shared by service and message parsing.
    """
    results: list[tuple[str, str]] = []
    header_re = re.compile(rf"\b{keyword}\s+(\w+)\s*\{{")

    for header_match in header_re.finditer(content):
        name = header_match.group(1)
        body_start = header_match.end()
        depth = 1
        pos = body_start
        in_line_comment = False
        in_block_comment = False
        while pos < len(content) and depth > 0:
            ch = content[pos]
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
            elif in_block_comment:
                if ch == "*" and pos + 1 < len(content) and content[pos + 1] == "/":
                    in_block_comment = False
                    pos += 1  # skip the '/'
            elif ch == "/" and pos + 1 < len(content):
                if content[pos + 1] == "/":
                    in_line_comment = True
                elif content[pos + 1] == "*":
                    in_block_comment = True
                    pos += 1  # skip the '*'
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            pos += 1
        if depth != 0:
            continue  # incomplete/malformed block
        body = content[body_start : pos - 1]
        results.append((name, body))

    return results


def _extract_service_blocks(content: str) -> list[tuple[str, str]]:
    """Extract ``(service_name, body)`` pairs from a ``.proto`` file."""
    return _extract_blocks(content, "service")


#: A scalar/typed field: ``[label] <type> <name> = <number>;``. The type allows
#: dotted/qualified names (``foo.Bar``). Not line-anchored — multiple fields can
#: share a line. ``map<...>`` fields are matched separately so the ``<...>``
#: doesn't confuse the type capture.
_FIELD_RE = re.compile(
    r"(?:(repeated|optional|required)\s+)?([\w.]+)\s+(\w+)\s*=\s*(\d+)\s*;",
)
_MAP_FIELD_RE = re.compile(
    r"map\s*<[^>]+>\s+(\w+)\s*=\s*(\d+)\s*;",
)

#: Innermost nested ``message``/``enum`` block — removed so a parent message's
#: field scan never absorbs a nested type's fields. ``oneof`` is unwrapped (its
#: members ARE the message's fields) by :data:`_ONEOF_RE`.
_NESTED_BLOCK_RE = re.compile(r"\b(?:message|enum)\s+\w+\s*\{[^{}]*\}")
_ONEOF_RE = re.compile(r"\boneof\s+\w+\s*\{([^{}]*)\}")


def _flatten_message_body(body: str) -> str:
    """Strip comments, drop nested message/enum blocks, unwrap oneof groups.

    Leaves a flat body whose remaining ``;``-terminated statements are exactly
    the message's own fields (including oneof members), so the field regex never
    misattributes a nested type's fields or trips over block braces.
    """
    body = _strip_comments(body)
    prev = ""
    while prev != body:
        prev = body
        body = _NESTED_BLOCK_RE.sub(" ", body)
        body = _ONEOF_RE.sub(lambda m: f" {m.group(1)} ", body)
    return body


def _parse_message_fields(body: str) -> list[SchemaField]:
    """Parse the field declarations of a message body (excluding nested types)."""
    body = _flatten_message_body(body)
    fields: list[SchemaField] = []
    seen: set[str] = set()
    for m in _FIELD_RE.finditer(body):
        label, ftype, name, number = m.group(1), m.group(2), m.group(3), m.group(4)
        if name in seen:
            continue
        seen.add(name)
        fields.append(
            SchemaField(
                name=name,
                type=ftype,
                required=label == "required",
                number=int(number),
                repeated=label == "repeated",
            )
        )
    for m in _MAP_FIELD_RE.finditer(body):
        name, number = m.group(1), m.group(2)
        if name in seen:
            continue
        seen.add(name)
        fields.append(SchemaField(name=name, type="map", number=int(number)))
    return fields


def _parse_messages(content: str) -> dict[str, list[SchemaField]]:
    """Map every top-level/nested message name to its parsed fields."""
    messages: dict[str, list[SchemaField]] = {}
    for name, body in _extract_blocks(content, "message"):
        messages[name] = _parse_message_fields(body)
    return messages


def _short_type(type_name: str) -> str:
    """Reduce a possibly-qualified message type to its bare name for lookup."""
    return type_name.rsplit(".", 1)[-1]


def _parse_rpcs(body: str) -> list[ProtoMethod]:
    """Parse ``rpc Name (Req) returns (Res);`` declarations in a service body."""
    rpc_re = re.compile(
        r"rpc\s+(\w+)\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)\s*"
        r"returns\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)",
    )
    methods: list[ProtoMethod] = []
    for m in rpc_re.finditer(body):
        methods.append(
            ProtoMethod(name=m.group(1), request_type=m.group(2), response_type=m.group(3))
        )
    return methods


class _ParsedService(NamedTuple):
    name: str
    full_path: str
    methods: list[ProtoMethod]


def _parse_proto_file(
    content: str,
) -> tuple[str, list[_ParsedService], dict[str, list[SchemaField]]]:
    """Parse a proto file, returning ``(package, services, messages)``."""
    stripped = _strip_comments(content)
    pkg_match = re.search(r"^\s*package\s+([\w.]+)\s*;", stripped, re.MULTILINE)
    package = pkg_match.group(1) if pkg_match else ""

    messages = _parse_messages(content)

    services: list[_ParsedService] = []
    for svc_name, body in _extract_service_blocks(content):
        full_path = f"{package}.{svc_name}" if package else svc_name
        services.append(_ParsedService(svc_name, full_path, _parse_rpcs(body)))

    return package, services, messages


class ProtoDialect:
    name = "proto"
    extensions = PROTO

    def extract(self, ctx: ScanContext) -> list[Contract]:
        package, services, messages = _parse_proto_file(ctx.content)
        out: list[Contract] = []
        for svc in services:
            for method in svc.methods:
                contract = make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc.full_path}/{method.name}",
                    role="provider",
                    symbol_name=f"{svc.full_path}/{method.name}",
                    confidence=0.85,
                    meta={
                        "package": package,
                        "service": svc.name,
                        "method": method.name,
                        "source": "proto",
                    },
                )
                schema = ContractSchema(
                    source="proto",
                    request_fields=messages.get(_short_type(method.request_type), []),
                    response_fields=messages.get(_short_type(method.response_type), []),
                )
                if not schema.is_empty:
                    contract.schema = schema
                out.append(contract)
        return out
