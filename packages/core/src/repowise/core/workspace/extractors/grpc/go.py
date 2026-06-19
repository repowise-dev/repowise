"""Go gRPC dialect — ``RegisterXxxServer`` providers / ``NewXxxClient`` consumers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import GO
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# pb.RegisterAuthServiceServer(grpcServer, &impl{})
_GO_PROVIDER_RE = re.compile(r"\.Register(\w+)Server\s*\(")
# pb.NewAuthServiceClient(conn)
_GO_CONSUMER_RE = re.compile(r"\.New(\w+)Client\s*\(")


class GoGrpcDialect:
    name = "go"
    extensions = GO

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _GO_PROVIDER_RE.finditer(ctx.content):
            svc = m.group(1)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="provider",
                    symbol_name=f"go:Register{svc}Server",
                    confidence=0.8,
                    meta={"service": svc, "source": "go_register"},
                )
            )
        for m in _GO_CONSUMER_RE.finditer(ctx.content):
            svc = m.group(1)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="consumer",
                    symbol_name=f"go:New{svc}Client",
                    confidence=0.7,
                    meta={"service": svc, "source": "go_client"},
                )
            )
        return out
