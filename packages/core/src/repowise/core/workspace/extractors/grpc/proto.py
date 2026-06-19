"""Protobuf IDL gRPC provider dialect.

Parses ``.proto`` service/rpc declarations into provider contracts. Uses a
brace-depth counter so braces inside comments, options, or message bodies don't
break service-block extraction.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import PROTO
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


def _extract_service_blocks(content: str) -> list[tuple[str, str]]:
    """Extract ``(service_name, body)`` pairs from a ``.proto`` file.

    Uses brace-depth counting so nested braces in comments, options, or
    message bodies don't break parsing.
    """
    results: list[tuple[str, str]] = []
    header_re = re.compile(r"service\s+(\w+)\s*\{")

    for header_match in header_re.finditer(content):
        service_name = header_match.group(1)
        body_start = header_match.end()
        depth = 1
        pos = body_start
        in_line_comment = False
        in_block_comment = False
        while pos < len(content) and depth > 0:
            ch = content[pos]
            # Track comment state to ignore braces inside comments
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
            continue  # incomplete/malformed service block
        body = content[body_start : pos - 1]
        results.append((service_name, body))

    return results


def _parse_proto_file(content: str) -> tuple[str, list[tuple[str, str, list[str]]]]:
    """Parse a proto file, returning ``(package, services)``.

    Each service is ``(service_name, full_service_path, [method_names])``.
    """
    pkg_match = re.search(r"^\s*package\s+([\w.]+)\s*;", content, re.MULTILINE)
    package = pkg_match.group(1) if pkg_match else ""

    services: list[tuple[str, str, list[str]]] = []
    rpc_re = re.compile(r"rpc\s+(\w+)\s*\(")

    for svc_name, body in _extract_service_blocks(content):
        full_path = f"{package}.{svc_name}" if package else svc_name
        methods = [m.group(1) for m in rpc_re.finditer(body)]
        services.append((svc_name, full_path, methods))

    return package, services


class ProtoDialect:
    name = "proto"
    extensions = PROTO

    def extract(self, ctx: ScanContext) -> list[Contract]:
        package, services = _parse_proto_file(ctx.content)
        out: list[Contract] = []
        for svc_name, full_path, methods in services:
            for method in methods:
                out.append(
                    make_grpc_contract(
                        ctx,
                        contract_id=f"grpc::{full_path}/{method}",
                        role="provider",
                        symbol_name=f"{full_path}/{method}",
                        confidence=0.85,
                        meta={
                            "package": package,
                            "service": svc_name,
                            "method": method,
                            "source": "proto",
                        },
                    )
                )
        return out
