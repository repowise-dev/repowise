"""Data dialect protocol and the shared contract builders.

A *dialect* is one framework's or file format's view of where table ownership
(providers) or table access (consumers) is declared. Every dialect funnels raw
table tokens through the two builders here, so all data contracts share one id
scheme (``data::<normalized table>``) and the normalization rules stay in
:mod:`.names`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..base import line_at
from ..dialect import build_contract
from .names import normalize_table_name

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


def build_table_provider(
    ctx: ScanContext,
    *,
    table_raw: str,
    framework: str,
    line: int | None = None,
    confidence: float = 0.85,
    schema: str | None = None,
) -> Contract | None:
    """Build a provider contract for a declared table, or ``None``.

    Providers are declarations of ownership: a ``CREATE TABLE``, an ORM entity,
    a migration. ``None`` when the raw token does not normalize to a concrete
    table name. *schema* (``create`` / ``alter``) marks a declaration that
    defines the table's schema rather than modelling it, which is how the
    repo owning a shared table is told from the repos reading it.
    """
    table = normalize_table_name(table_raw)
    if table is None:
        return None
    return build_contract(
        ctx,
        contract_id=f"data::{table}",
        contract_type="data",
        role="provider",
        symbol_name=f"{framework}:{table_raw}",
        confidence=confidence,
        line=line,
        meta={"table": table, "framework": framework, **({"schema": schema} if schema else {})},
    )


def build_table_consumer(
    ctx: ScanContext,
    *,
    table_raw: str,
    verb: str,
    client: str,
    line: int | None = None,
    confidence: float = 0.7,
) -> Contract | None:
    """Build a consumer contract for a table referenced from app code.

    ``verb`` is the SQL operation that touched the table (``select`` /
    ``insert`` / ``update`` / ``delete`` / ``join``), carried in ``meta`` so
    downstream views can distinguish readers from writers.
    """
    table = normalize_table_name(table_raw)
    if table is None:
        return None
    return build_contract(
        ctx,
        contract_id=f"data::{table}",
        contract_type="data",
        role="consumer",
        symbol_name=f"{client}:{verb} {table}",
        confidence=confidence,
        line=line,
        meta={"table": table, "verb": verb, "client": client},
    )


def dedup_consumers(
    ctx: ScanContext, client: str, found: list[tuple[str, str, int]]
) -> list[Contract]:
    """One consumer per table and verb in *found*, ``(raw_table, verb, line)``."""
    out: list[Contract] = []
    seen: set[tuple[str, str]] = set()
    for raw, verb, line in found:
        key = (raw.lower(), verb)
        if key in seen:
            continue
        seen.add(key)
        contract = build_table_consumer(ctx, table_raw=raw, verb=verb, client=client, line=line)
        if contract is not None:
            out.append(contract)
    return out


EXPLICIT_CONFIDENCE = 0.85
CONVENTION_CONFIDENCE = 0.6
# A declaration that evolves a table (``ALTER TABLE``, ``Schema::table``).
ALTER_CONFIDENCE = 0.8


#: One table declaration: ``(raw_name, confidence, line)``.
_Found = tuple[str, float, int]


def found_names(
    pattern: re.Pattern[str],
    content: str,
    confidence: float,
    transform: Callable[[str], str] = str,
) -> list[_Found]:
    r"""Every match of *pattern*'s first group, with the line it sits on.

    The line comes from the group, not the match: three of these patterns open
    with ``^\s*`` under ``MULTILINE``, where ``^`` matches at a preceding blank
    line and ``\s*`` eats the newlines, putting ``m.start()`` above the
    declaration.
    """
    return [
        (transform(m.group(1)), confidence, line_at(content, m.start(1)))
        for m in pattern.finditer(content)
    ]


def dedup_emit(
    ctx: ScanContext,
    framework: str,
    found: list[_Found],
    *,
    schema: str | None = None,
    seen: set[str] | None = None,
) -> list[Contract]:
    """One provider per raw name in *found*, skipping names already in *seen*."""
    out: list[Contract] = []
    seen = set() if seen is None else seen
    for raw, confidence, line in found:
        if raw in seen:
            continue
        seen.add(raw)
        contract = build_table_provider(
            ctx,
            table_raw=raw,
            framework=framework,
            line=line,
            confidence=confidence,
            schema=schema,
        )
        if contract is not None:
            out.append(contract)
    return out
