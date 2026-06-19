"""C# (gRPC-dotnet) dialect.

Providers come from explicit ``MapGrpcService<T>()`` registration and from the
generated server-base convention ``class Impl : ServiceName.ServiceNameBase``.
Consumers come from ``new XxxClient(...)`` generated stubs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import CSHARP
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# app.MapGrpcService<GreeterService>()
_CSHARP_GRPC_MAP_RE = re.compile(r"\.\s*MapGrpcService\s*<\s*(\w+)\s*>")
# class Impl : ServiceName.ServiceNameBase
_CSHARP_GRPC_BASE_RE = re.compile(r"class\s+\w+\s*:\s*(?:[\w.]+\.)?(\w+?)\s*\.\s*\1Base\b")
# new AuthServiceClient(channel) — generated stub class ending in "Client".
_CSHARP_GRPC_CLIENT_RE = re.compile(r"\bnew\s+(\w+)Client\s*\(")

# Generic-class false positives — test doubles and non-gRPC clients.
_CONSUMER_FALSE_PREFIXES = ("mock", "test", "fake", "http")


class CSharpGrpcDialect:
    name = "csharp"
    extensions = CSHARP

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _CSHARP_GRPC_MAP_RE.finditer(ctx.content):
            svc = m.group(1)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="provider",
                    symbol_name=f"cs:MapGrpcService<{svc}>",
                    confidence=0.8,
                    meta={"service": svc, "source": "csharp_mapgrpc"},
                )
            )
        for m in _CSHARP_GRPC_BASE_RE.finditer(ctx.content):
            svc = m.group(1)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="provider",
                    symbol_name=f"cs:extends {svc}.{svc}ServiceBase",
                    confidence=0.8,
                    meta={"service": svc, "source": "csharp_base"},
                )
            )
        for m in _CSHARP_GRPC_CLIENT_RE.finditer(ctx.content):
            svc = m.group(1)
            if svc.lower().startswith(_CONSUMER_FALSE_PREFIXES):
                continue
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="consumer",
                    symbol_name=f"cs:new {svc}Client",
                    confidence=0.65,
                    meta={"service": svc, "source": "csharp_client"},
                )
            )
        return out
