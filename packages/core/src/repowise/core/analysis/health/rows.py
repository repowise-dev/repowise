"""Reading one health finding whatever shape it arrives in.

A finding reaches this package as an analyzer dataclass before persistence, as
an ORM row after it, and as a plain dict in tests and fixtures. Everything that
reads one goes through these two adapters so no consumer has to know which.
"""

from __future__ import annotations

import json
from typing import Any


def field(row: Any, name: str, default: Any = None) -> Any:
    """Read one attribute from a dataclass, an ORM row, or a dict."""
    if isinstance(row, dict):
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

    *default* stands in for an absent, empty or malformed cell.
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


__all__ = ["detail_map", "field", "json_field"]
