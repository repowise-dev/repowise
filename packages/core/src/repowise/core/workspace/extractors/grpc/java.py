"""Java gRPC dialect — ``extends XxxGrpc.XxxImplBase`` / ``XxxGrpc.newStub``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import JAVA
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# extends AuthServiceGrpc.AuthServiceImplBase
_JAVA_PROVIDER_RE = re.compile(r"extends\s+(\w+)Grpc\.(\w+)ImplBase")
# AuthServiceGrpc.newBlockingStub(channel)
_JAVA_CONSUMER_RE = re.compile(r"(\w+)Grpc\.new(?:Blocking|Future)?Stub\s*\(")


class JavaGrpcDialect:
    name = "java"
    extensions = JAVA

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _JAVA_PROVIDER_RE.finditer(ctx.content):
            svc = m.group(1)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="provider",
                    symbol_name=f"java:extends {svc}Grpc.ImplBase",
                    confidence=0.8,
                    meta={"service": svc, "source": "java_extends"},
                )
            )
        for m in _JAVA_CONSUMER_RE.finditer(ctx.content):
            svc = m.group(1)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="consumer",
                    symbol_name=f"java:{svc}Grpc.newStub",
                    confidence=0.7,
                    meta={"service": svc, "source": "java_stub"},
                )
            )
        return out
