"""One row in each code table doctor's index-data row expects a full index to fill."""

from __future__ import annotations

from typing import Any

CODE_SETS = ("graph", "symbol", "health")


def code_rows(repository_id: str, sets: tuple[str, ...] = CODE_SETS) -> list[Any]:
    """ORM rows filling the named code table *sets* for *repository_id*."""
    from repowise.core.persistence.models import GraphNode, HealthFileMetric, WikiSymbol

    rows = {
        "graph": GraphNode(repository_id=repository_id, node_id="kept.py"),
        "symbol": WikiSymbol(
            repository_id=repository_id,
            file_path="kept.py",
            symbol_id="kept.py::main",
            name="main",
            qualified_name="main",
            kind="function",
        ),
        "health": HealthFileMetric(repository_id=repository_id, file_path="kept.py"),
    }
    return [rows[name] for name in sets]
