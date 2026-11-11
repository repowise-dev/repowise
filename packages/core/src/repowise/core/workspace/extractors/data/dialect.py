"""Data dialect protocol and the shared contract builders.

A *dialect* is one framework's or file format's view of where table ownership
(providers) or table access (consumers) is declared. Every dialect funnels raw
table tokens through the two builders here, so all data contracts share one id
scheme (``data::<normalized table>``) and the normalization rules stay in
:mod:`.names`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..dialect import build_contract
from .names import is_system_table, normalize_table_name

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


def build_table_provider(
    ctx: ScanContext,
    *,
    table_raw: str,
    framework: str,
    line: int | None = None,
    origin: ContractOrigin = ContractOrigin.EXPLICIT_TABLE,
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
        origin=origin,
        line=line,
        meta={
            "table": table,
            "framework": framework,
            **({"schema": schema} if schema else {}),
            **_embedded_meta(ctx),
        },
    )


#: Driver and connection-string tokens that place a file's tables in an
#: embedded database, one row per engine. An embedded database is a file the
#: program opens itself, so its tables are never another repo's server tables.
#: Deliberate ceiling: server engines (Postgres vs MySQL) are not told apart;
#: upgrade path is a row per server engine and comparing both ends' engines.
_EMBEDDED_ENGINES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "sqlite",
        (
            "sqlite3",
            "SQLite3",
            "aiosqlite",
            "bun:sqlite",
            "modernc.org/sqlite",
            "jdbc:sqlite",
            "sqlite://",
            "PDO('sqlite:",
            'PDO("sqlite:',
            "Microsoft.Data.Sqlite",
            "UseSqlite",
        ),
    ),
)


#: Tokens naming a server database: a driver, a dialect or a URL scheme, in
#: lower case (matched against the lower-cased file). A repo that names one may
#: run its tables on a shared server, so only the file that opens an embedded
#: engine is read as embedded (see :func:`repo_embedded_engine`). A stray hit
#: (a comment) only keeps that per-file reading.
_SERVER_ENGINE_TOKENS: tuple[str, ...] = (
    "postgres",
    "pgsql",
    "psycopg",
    "asyncpg",
    "npgsql",
    "lib/pq",
    "jackc/pgx",
    "'pg'",
    '"pg"',
    "pg-promise",
    "pg::",
    "mysql",
    "mariadb",
    "sqlserver",
    "sqlsrv",
    "mssql",
    "tedious",
    "oracle",
    "cockroach",
    "redshift",
    "snowflake",
    "clickhouse",
)


def embedded_engine(ctx: ScanContext) -> str | None:
    """The embedded engine *ctx*'s file opens (``sqlite``), or ``None``."""
    for engine, tokens in _EMBEDDED_ENGINES:
        if ctx.has_any(tokens):
            return engine
    return None


def repo_embedded_engine(contents: Iterable[str]) -> str | None:
    """The embedded engine a repo runs on, when it names one and no server database.

    Apps open SQLite in one module (``db.py``, ``config.py``) and query it in
    others, so the file-level tag misses most readers. A repo whose files name
    an embedded engine and no server driver keeps all its tables in that file.
    Deliberate ceiling: one engine per repo; a monorepo naming both a server
    and an embedded engine keeps the per-file tag. Upgrade path: the same test
    per package directory.
    """
    found = None
    for content in contents:
        lowered = content.lower()
        if any(token in lowered for token in _SERVER_ENGINE_TOKENS):
            return None
        if found is None:
            found = next(
                (e for e, tokens in _EMBEDDED_ENGINES if any(t in content for t in tokens)),
                None,
            )
    return found


def _embedded_meta(ctx: ScanContext) -> dict[str, str]:
    """``{"embedded": engine}`` when *ctx*'s file opens an embedded engine."""
    engine = embedded_engine(ctx)
    return {"embedded": engine} if engine else {}


def build_table_consumer(
    ctx: ScanContext,
    *,
    table_raw: str,
    verb: str,
    client: str,
    line: int | None = None,
    origin: ContractOrigin = ContractOrigin.CLIENT_CALL,
) -> Contract | None:
    """Build a consumer contract for a table referenced from app code.

    ``verb`` is the SQL operation that touched the table (``select`` /
    ``insert`` / ``update`` / ``delete`` / ``join``), carried in ``meta`` so
    downstream views can distinguish readers from writers. ``embedded``
    names the embedded engine the file opens, when it opens one.
    """
    table = normalize_table_name(table_raw)
    if table is None or is_system_table(table):
        return None
    return build_contract(
        ctx,
        contract_id=f"data::{table}",
        contract_type="data",
        role="consumer",
        symbol_name=f"{client}:{verb} {table}",
        origin=origin,
        line=line,
        meta={
            "table": table,
            "verb": verb,
            "client": client,
            **_embedded_meta(ctx),
        },
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


#: One table declaration: ``(raw_name, origin, line)``.
_Found = tuple[str, ContractOrigin, int]


def found_names(
    pattern: re.Pattern[str],
    content: str,
    origin: ContractOrigin,
    transform: Callable[[str], str] = str,
) -> list[_Found]:
    r"""Every match of *pattern*'s first group, with the line it sits on.

    The line comes from the group, not the match: three of these patterns open
    with ``^\s*`` under ``MULTILINE``, where ``^`` matches at a preceding blank
    line and ``\s*`` eats the newlines, putting ``m.start()`` above the
    declaration.
    """
    return [
        (transform(m.group(1)), origin, line_at(content, m.start(1)))
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
    for raw, origin, line in found:
        if raw in seen:
            continue
        seen.add(raw)
        contract = build_table_provider(
            ctx,
            table_raw=raw,
            framework=framework,
            line=line,
            origin=origin,
            schema=schema,
        )
        if contract is not None:
            out.append(contract)
    return out
