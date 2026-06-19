"""gRPC dialect protocol and the shared contract builder.

Each language (and the ``.proto`` IDL) is one dialect keyed on its own file
extensions, so exactly one dialect runs per file. Service identity is carried in
the ``contract_id`` (``grpc::<service>/<method-or-*>``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..base import ScanContext

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract


@runtime_checkable
class GrpcDialect(Protocol):
    """A gRPC recogniser for a set of file extensions."""

    name: str
    extensions: frozenset[str]

    def extract(self, ctx: ScanContext) -> list[Contract]:
        """Return the gRPC contracts found in *ctx* (may be empty)."""
        ...


def make_grpc_contract(
    ctx: ScanContext,
    *,
    contract_id: str,
    role: str,
    symbol_name: str,
    confidence: float,
    meta: dict,
) -> Contract:
    """Build a gRPC :class:`Contract` with the common fields filled in."""
    from repowise.core.workspace.contracts import Contract

    return Contract(
        repo=ctx.repo_alias,
        contract_id=contract_id,
        contract_type="grpc",
        role=role,
        file_path=ctx.rel_path,
        symbol_name=symbol_name,
        confidence=confidence,
        service=None,
        meta=meta,
    )
