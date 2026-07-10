"""DDL provider dialect: tables declared in ``.sql`` files.

A ``CREATE TABLE`` / ``CREATE VIEW`` in a repo's schema files or migrations is
the strongest ownership signal there is. Parsed with sqlglot in permissive
mode (same engine as the ingestion SQL handler): a statement that fails to
parse contributes nothing, never a guess. ``ALTER TABLE`` in migrations also
asserts ownership (the repo evolves that table) at slightly lower confidence.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import ScanContext
from .dialect import build_table_provider

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

# CREATE kinds that declare a relation worth a provider contract. INDEX and
# TRIGGER attach to a table but don't declare one.
_CREATE_KINDS = frozenset({"TABLE", "VIEW", "MATERIALIZED VIEW"})


def _qualified_name(node: object) -> str | None:
    """``schema.table`` (or bare ``table``) for a sqlglot Table-ish node."""
    name = getattr(node, "name", "") or ""
    if not name:
        return None
    db = getattr(node, "db", "") or ""
    return f"{db}.{name}" if db else name


class DdlDialect:
    """Providers from ``CREATE``/``ALTER`` statements in ``.sql`` files."""

    name = "ddl"
    extensions = frozenset({".sql"})

    def extract(self, ctx: ScanContext) -> list[Contract]:
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return []
        logging.getLogger("sqlglot").setLevel(logging.ERROR)

        try:
            statements = sqlglot.parse(ctx.content, error_level=sqlglot.ErrorLevel.IGNORE)
        except Exception:
            return []

        out: list[Contract] = []
        seen: set[str] = set()
        for stmt in statements:
            if stmt is None:
                continue
            raw: str | None = None
            confidence = 0.85
            if isinstance(stmt, exp.Create) and (stmt.kind or "").upper() in _CREATE_KINDS:
                target = stmt.this
                if isinstance(target, exp.Schema):
                    target = target.this
                raw = _qualified_name(target)
            elif isinstance(stmt, exp.Alter) and (stmt.args.get("kind") or "").upper() == "TABLE":
                raw = _qualified_name(stmt.this)
                confidence = 0.8
            if raw is None or raw in seen:
                continue
            seen.add(raw)
            contract = build_table_provider(
                ctx, table_raw=raw, framework="sql-ddl", confidence=confidence
            )
            if contract is not None:
                out.append(contract)
        return out
