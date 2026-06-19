"""TypeScript / JavaScript gRPC dialect — NestJS ``@GrpcMethod`` providers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import JS_TS
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# @GrpcMethod('AuthService', 'Login')
_TS_PROVIDER_RE = re.compile(r"@GrpcMethod\s*\(\s*'(\w+)'\s*,\s*'(\w+)'\s*\)")


class TypeScriptGrpcDialect:
    name = "typescript"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _TS_PROVIDER_RE.finditer(ctx.content):
            svc = m.group(1)
            method = m.group(2)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/{method}",
                    role="provider",
                    symbol_name=f"ts:@GrpcMethod('{svc}', '{method}')",
                    confidence=0.8,
                    meta={"service": svc, "method": method, "source": "ts_decorator"},
                )
            )
        return out
