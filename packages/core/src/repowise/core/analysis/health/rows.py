"""Reading one row whatever shape it arrives in.

A health finding, git row or decision reaches a fold as an analyzer dataclass,
an ORM or SQL row, or a plain dict. Everything that reads one goes through
these adapters so no consumer has to know which.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


def field(row: Any, name: str, default: Any = None) -> Any:
    """Read one attribute from a dataclass, an ORM row, or a mapping."""
    # Exact-type fast paths first: the ``Mapping`` check is an ABC lookup,
    # and a fold over a repository's rows calls this a hundred thousand times.
    kind = type(row)
    if kind is dict:
        return row.get(name, default)
    if isinstance(row, tuple):
        return getattr(row, name, default)
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def detail_map(row: Any) -> dict[str, Any]:
    """The finding's open ``details`` payload, whether stored or in memory."""
    value = field(row, "details", None)
    if isinstance(value, dict):
        return value
    raw = field(row, "details_json", None)
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def json_field(row: Any, name: str, default: Any) -> Any:
    """A JSON-text column decoded, or the value as given when already decoded.

    *default* stands in for an absent, empty or unparseable cell; valid JSON of
    another type is returned as is.
    """
    value = field(row, name, None)
    if isinstance(value, (str, bytes)):
        if not value:
            return default
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default if value is None else value


def split_tests(rows: Iterable[Any]) -> tuple[list[Any], list[Any]]:
    """Partition per-file rows into ``(production, tests)`` by their ``is_test``.

    Ranked "worst file" answers name production files and report tests apart;
    a surface falls back to the tests only when there is no production row.
    """
    production: list[Any] = []
    tests: list[Any] = []
    for row in rows:
        (tests if field(row, "is_test", False) else production).append(row)
    return production, tests


def scored_rows(rows: Iterable[Any]) -> list[Any]:
    """The per-file rows that carry a score.

    A file whose language health has no dialect for is stored with no score
    (``has_health_dialect``). It is a file nobody measured, so every average,
    "worst file" and count of scored files leaves it out rather than reading
    it as a perfect 10.0.
    """
    return [row for row in rows if field(row, "score", None) is not None]


def split_unscored(rows: Iterable[Any]) -> tuple[list[Any], int]:
    """``(scored rows, how many were left out)``, for a figure that says both."""
    rows = list(rows)
    scored = scored_rows(rows)
    return scored, len(rows) - len(scored)


__all__ = ["detail_map", "field", "json_field", "scored_rows", "split_tests", "split_unscored"]
