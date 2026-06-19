"""Python gRPC dialect — ``add_XxxServicer_to_server`` / ``XxxStub``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..langs import PYTHON
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# add_AuthServiceServicer_to_server(servicer, server)
_PY_PROVIDER_RE = re.compile(r"add_(\w+?)Servicer_to_server\s*\(")
# AuthServiceStub(channel)
_PY_CONSUMER_RE = re.compile(r"(\w+)Stub\s*\(")

# Common stub false positives — test doubles, not real clients.
_CONSUMER_FALSE_PREFIXES = ("mock", "test", "fake")


class PythonGrpcDialect:
    name = "python"
    extensions = PYTHON

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in _PY_PROVIDER_RE.finditer(ctx.content):
            svc = m.group(1)
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="provider",
                    symbol_name=f"py:add_{svc}Servicer_to_server",
                    confidence=0.8,
                    meta={"service": svc, "source": "py_servicer"},
                )
            )
        for m in _PY_CONSUMER_RE.finditer(ctx.content):
            svc = m.group(1)
            if svc.lower().startswith(_CONSUMER_FALSE_PREFIXES):
                continue
            out.append(
                make_grpc_contract(
                    ctx,
                    contract_id=f"grpc::{svc}/*",
                    role="consumer",
                    symbol_name=f"py:{svc}Stub",
                    confidence=0.7,
                    meta={"service": svc, "source": "py_stub"},
                )
            )
        return out
