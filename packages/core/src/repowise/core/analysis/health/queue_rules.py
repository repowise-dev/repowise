"""Queue filter, sort and facet rules as data, read over rows in memory.

A materialized queue (refactoring, performance) declares its filters as
:class:`FilterRule` rows and its orders as ``(field, descending)`` keys. The
performance store turns the same tables into SQL
(``persistence.sql.rule_predicate`` and ``persistence.sql.order_by``); the
functions here read them over rows already in memory, so the two cannot
disagree about what a queue holds or its order.

Rows and params may be ORM rows, dataclasses or mappings; every read goes
through ``rows.field``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from repowise.core.analysis.health.rows import field

FilterOp = Literal["eq", "in", "contains", "prefix", "positive", "is", "eq_or_null"]

#: How ``eq_or_null`` spells a NULL column in a caller's vocabulary.
NULL_VALUE = "none"

SortKeys = tuple[tuple[str, bool], ...]

#: A facet: its name, the field it counts, and the filter parameter that
#: narrows that field (``None``: none does).
Facet = tuple[str, str, str | None]


@dataclass(frozen=True, slots=True)
class FilterRule:
    """One queue filter: the parameter it reads, the field it tests, and how.

    ``applies`` is when the parameter narrows at all: ``set`` for anything but
    ``None`` (an empty ``in`` list is a scope that matches nothing), ``truthy``
    when an empty value means "no filter".
    """

    param: str
    field: str
    op: FilterOp
    applies: Literal["always", "set", "truthy"]


def active_filters(rules: Iterable[FilterRule], params: Any) -> list[tuple[FilterRule, Any]]:
    """The *rules* that *params* (a query or a mapping) narrows by, with values."""
    active: list[tuple[FilterRule, Any]] = []
    for rule in rules:
        value = field(params, rule.param)
        if rule.applies == "set" and value is None:
            continue
        if rule.applies == "truthy" and not value:
            continue
        active.append((rule, value))
    return active


def matches(rule: FilterRule, value: Any, actual: Any) -> bool:
    """Whether a row's *actual* field value passes *rule* set to *value*."""
    if rule.op == "eq":
        return actual == value
    if rule.op == "in":
        return actual in value
    if rule.op == "eq_or_null":
        return actual is None if value == NULL_VALUE else actual == value
    if rule.op == "contains":
        # Ceiling: SQLite folds ASCII case only, Python folds Unicode; the two
        # differ only on a non-ASCII path fragment.
        return actual is not None and value.lower() in actual.lower()
    if rule.op == "prefix":
        # Case-sensitive, as PostgreSQL's LIKE is. SQLite's LIKE folds ASCII
        # case; paths differing only in case are not a case the queue meets.
        return actual is not None and actual.startswith(value)
    if rule.op == "positive":
        return (actual or 0) > 0
    # ``is``: SQL ``IS true/false``, so a NULL never matches either.
    return actual is not None and bool(actual) is bool(value)


def keep(rules: Iterable[FilterRule], row: Any, params: Any) -> bool:
    """Whether *row* passes every one of *rules* that *params* sets."""
    return all(
        matches(rule, value, field(row, rule.field))
        for rule, value in active_filters(rules, params)
    )


def sort_key(keys: SortKeys, row: Any) -> tuple[Any, ...]:
    """The sort key for *row* under *keys*; a descending field must be numeric."""
    return tuple(-field(row, name) if descending else field(row, name) for name, descending in keys)


def fold_facets(
    groups: Iterable[Sequence[Any]],
    facets: Sequence[Facet],
    *,
    rules: Iterable[FilterRule] = (),
    selection: Mapping[str, Any] | None = None,
    null: str | None = None,
) -> dict[str, dict[str, int]]:
    """Fold ``(*facet fields, count)`` groups into ``{facet: {value: count}}``.

    With a *selection*, each facet is cross-filtered by the *other* selections
    under *rules*: counting a facet under its own filter would leave every
    alternative at zero, so choosing one value would erase the others from the
    control. A NULL (or, with *null* set, any empty) value counts as *null*;
    without *null* it is skipped. A string *null* relabels any falsy value
    (``""`` and ``0`` too); ``None`` skips NULLs only.
    """
    fields = tuple(column for _, column, _ in facets)
    rows = [dict(zip((*fields, "count"), group, strict=True)) for group in groups]
    facet_rules = tuple(rule for rule in rules if rule.field in fields)
    folded: dict[str, dict[str, int]] = {}
    for name, column, own in facets:
        others = {param: value for param, value in (selection or {}).items() if param != own}
        counts: dict[str, int] = {}
        for row in rows:
            value = row[column] if null is None else (row[column] or null)
            if value is None or not keep(facet_rules, row, others):
                continue
            counts[str(value)] = counts.get(str(value), 0) + int(row["count"])
        folded[name] = counts
    return folded


__all__ = [
    "NULL_VALUE",
    "Facet",
    "FilterOp",
    "FilterRule",
    "SortKeys",
    "active_filters",
    "fold_facets",
    "keep",
    "matches",
    "sort_key",
]
