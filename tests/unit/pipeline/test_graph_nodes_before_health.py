"""Every update path writes graph nodes before health.

Plan annotations, stored when health is persisted, read each symbol's
``public_api`` from ``graph_nodes``; written after, they would read the
previous run's flags (none at all on the first update after an upgrade).
"""

from __future__ import annotations

import inspect

import pytest

from repowise.cli.commands.update_cmd import full_persist
from repowise.cli.commands.update_cmd import persistence as cli_update
from repowise.core.pipeline.incremental import persist_index


@pytest.mark.parametrize(
    ("function", "health_call"),
    [
        (cli_update._rescore_health_from_db, "await save_full_health_report("),
    ],
)
def test_graph_nodes_persist_before_health(function, health_call: str) -> None:
    source = inspect.getsource(function)
    assert "await persist_graph_nodes(" in source and health_call in source
    assert source.index("await persist_graph_nodes(") < source.index(health_call)


@pytest.mark.parametrize(
    "steps", [persist_index.INCREMENTAL_STEPS, full_persist.FULL_UPDATE_STEPS]
)
def test_update_steps_persist_graph_nodes_before_health(steps) -> None:
    names = [step.name for step in steps]
    assert names.index("Graph nodes persist") < names.index("Health persist")
    by_name = {step.name: step.run for step in steps}
    assert "await persist_graph_nodes(" in inspect.getsource(by_name["Graph nodes persist"])
    assert "await persist_partial_health(" in inspect.getsource(by_name["Health persist"])
