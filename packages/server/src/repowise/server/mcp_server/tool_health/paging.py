"""Collection accounting for get_health: every emitted list states its total."""

from __future__ import annotations

from typing import Any


def _stamp_collection(
    result: dict[str, Any],
    key: str,
    *,
    total: int | None = None,
    reason: str = "limit",
) -> None:
    """Attach complete-population accounting to one emitted collection."""
    rows = result.get(key)
    if not isinstance(rows, list):
        return
    eligible = len(rows) if total is None else total
    emitted = len(rows)
    result[f"{key}_total"] = eligible
    result[f"{key}_emitted"] = emitted
    if emitted < eligible:
        result[f"{key}_reduced_reason"] = reason


def _stamp_nested_collections(value: Any) -> None:
    """Give every nested list an emitted count and an honest eligible total."""
    if isinstance(value, list):
        for item in value:
            _stamp_nested_collections(item)
        return
    if not isinstance(value, dict):
        return
    for key, child in list(value.items()):
        if isinstance(child, list):
            total_key = f"{key}_total"
            emitted_key = f"{key}_emitted"
            total = int(value.get(total_key, len(child)) or 0)
            value.setdefault(total_key, total)
            value.setdefault(emitted_key, len(child))
            if len(child) < total:
                value.setdefault(f"{key}_reduced_reason", "limit")
        _stamp_nested_collections(child)
