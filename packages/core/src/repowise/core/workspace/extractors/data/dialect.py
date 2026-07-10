"""Data dialect protocol and the shared contract builders.

A *dialect* is one framework's or file format's view of where table ownership
(providers) or table access (consumers) is declared. Every dialect funnels raw
table tokens through the two builders here, so all data contracts share one id
scheme (``data::<normalized table>``) and the normalization rules stay in
:mod:`.names`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..base import ScanContext
from .names import normalize_table_name

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract


@runtime_checkable
class DataDialect(Protocol):
    """A table-ownership/access recogniser for a set of file extensions."""

    name: str
    extensions: frozenset[str]

    def extract(self, ctx: ScanContext) -> list[Contract]:
        """Return the contracts found in *ctx* (may be empty)."""
        ...


def build_table_provider(
    ctx: ScanContext,
    *,
    table_raw: str,
    framework: str,
    confidence: float = 0.85,
) -> Contract | None:
    """Build a provider contract for a declared table, or ``None``.

    Providers are declarations of ownership: a ``CREATE TABLE``, an ORM entity,
    a migration. ``None`` when the raw token does not normalize to a concrete
    table name.
    """
    from repowise.core.workspace.contracts import Contract

    table = normalize_table_name(table_raw)
    if table is None:
        return None
    return Contract(
        repo=ctx.repo_alias,
        contract_id=f"data::{table}",
        contract_type="data",
        role="provider",
        file_path=ctx.rel_path,
        symbol_name=f"{framework}:{table_raw}",
        confidence=confidence,
        service=None,
        meta={"table": table, "framework": framework},
    )


def build_table_consumer(
    ctx: ScanContext,
    *,
    table_raw: str,
    verb: str,
    client: str,
    confidence: float = 0.7,
) -> Contract | None:
    """Build a consumer contract for a table referenced from app code.

    ``verb`` is the SQL operation that touched the table (``select`` /
    ``insert`` / ``update`` / ``delete`` / ``join``), carried in ``meta`` so
    downstream views can distinguish readers from writers.
    """
    from repowise.core.workspace.contracts import Contract

    table = normalize_table_name(table_raw)
    if table is None:
        return None
    return Contract(
        repo=ctx.repo_alias,
        contract_id=f"data::{table}",
        contract_type="data",
        role="consumer",
        file_path=ctx.rel_path,
        symbol_name=f"{client}:{verb} {table}",
        confidence=confidence,
        service=None,
        meta={"table": table, "verb": verb, "client": client},
    )
