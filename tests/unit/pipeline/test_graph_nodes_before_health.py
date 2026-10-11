"""Every update path writes graph nodes before health.

Plan annotations, stored when health is persisted, read each symbol's
``public_api`` from ``graph_nodes``; written after, they would read the
previous run's flags (none at all on the first update after an upgrade).
"""

from __future__ import annotations

import inspect

import pytest

from repowise.cli.commands.update_cmd import persistence as cli_update
from repowise.core.pipeline import incremental


@pytest.mark.parametrize(
    ("function", "health_call"),
    [
        (incremental.persist_incremental_index, "await persist_partial_health("),
        (cli_update._persist_full_update_async, "await _persist_partial_health("),
        (cli_update._rescore_health_from_db, "await save_full_health_report("),
    ],
)
def test_graph_nodes_persist_before_health(function, health_call: str) -> None:
    source = inspect.getsource(function)
    assert "await persist_graph_nodes(" in source and health_call in source
    assert source.index("await persist_graph_nodes(") < source.index(health_call)
