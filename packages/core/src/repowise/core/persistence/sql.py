"""Small SQL helpers shared by every layer that builds queries by hand.

Kept at the persistence root (rather than inside ``crud``) because the server
routers and MCP tools build their own ``select`` statements and need the same
escaping the CRUD layer uses.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import String, literal
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

#: The escape character to pass as ``escape=`` on every ``like``/``ilike``
#: paired with :func:`escape_like`. SQLite has no default LIKE escape
#: character, so it has to be declared on the call for the escaping below to
#: mean anything.
LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """Escape LIKE metacharacters (``%``, ``_``) and the escape char itself.

    Always pair with ``escape=LIKE_ESCAPE`` on the ``like``/``ilike`` call.
    Without this, a value containing ``_`` — which is most Python filenames —
    matches any single character instead of an underscore, so the query
    quietly returns rows the caller never asked for.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class json_text(FunctionElement):  # noqa: N801 - reads as the SQL function it renders
    """One top-level key of a JSON text column, as text, in SQL.

    SQLite and PostgreSQL spell it differently and neither indexes it, so it
    narrows a read the caller already scoped; it never drives a filter.
    """

    type = String()
    inherit_cache = True

    def __init__(self, column: Any, key: str) -> None:
        super().__init__(column, literal(key, String()))


@compiles(json_text)
def _json_text_sqlite(element: json_text, compiler: Any, **kw: Any) -> str:
    column, key = (compiler.process(c, **kw) for c in element.clauses)
    return f"json_extract({column}, '$.' || {key})"


@compiles(json_text, "postgresql")
def _json_text_postgresql(element: json_text, compiler: Any, **kw: Any) -> str:
    column, key = (compiler.process(c, **kw) for c in element.clauses)
    return f"(CAST({column} AS JSON) ->> {key})"


def rule_predicate(model: Any, rule: Any, value: Any) -> Any:
    """One ``analysis.health.queue_rules.FilterRule`` as a SQL predicate on *model*.

    ``queue_rules.matches`` is the same rule read over a row in memory.
    """
    # Deferred: the analysis package imports persistence.
    from repowise.core.analysis.health.queue_rules import NULL_VALUE

    column = getattr(model, rule.field)
    if rule.op == "eq":
        return column == value
    if rule.op == "in":
        # One value is still one equality; an empty list matches nothing.
        values = list(value)
        return column == values[0] if len(values) == 1 else column.in_(values)
    if rule.op == "eq_or_null":
        return column.is_(None) if value == NULL_VALUE else column == value
    if rule.op == "contains":
        return column.ilike(f"%{escape_like(value)}%", escape=LIKE_ESCAPE)
    if rule.op == "prefix":
        return column.like(f"{escape_like(value)}%", escape=LIKE_ESCAPE)
    if rule.op == "positive":
        return column > 0
    return column.is_(value)


def order_by(model: Any, keys: Iterable[tuple[str, bool]]) -> tuple[Any, ...]:
    """``(field, descending)`` sort keys as an ``ORDER BY`` on *model*."""
    return tuple(
        getattr(model, name).desc() if descending else getattr(model, name).asc()
        for name, descending in keys
    )


def is_missing_table(exc: Exception) -> bool:
    """Whether *exc* is "that table is not there" rather than a real failure.

    Backend-specific wording, so this is a substring check and not a code. It
    fails toward ``unavailable``: mistaking a missing table for a failure costs
    a visible block that should have been silent, while the reverse would let a
    genuine failure render as a clean bill.

    Every clause is table-scoped for that reason. Postgres says "does not
    exist" for a missing column, database, function or role too, and each of
    those is real schema drift or misconfiguration -- swallowing them here
    would rebuild the exact silence this check exists to break.
    """
    text = str(getattr(exc, "orig", "") or exc).lower()
    if "no such table" in text or "undefined table" in text:
        return True
    return "does not exist" in text and ("relation" in text or "table" in text)
