"""Leiden runs on graspologic's native core without importing graspologic.

The full ``graspologic`` package costs tens of seconds to import (umap,
pynndescent, sklearn), and every update that detects communities paid it.
These tests pin the partition to what the graspologic wrapper returns and keep
the heavy package out of the CLI and community-detection import paths.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.analysis._leiden import leiden_partition

pytest.importorskip("graspologic_native")

_SRC_DIRS = [
    str(Path(__file__).resolve().parents[3] / "packages" / pkg / "src")
    for pkg in ("cli", "core", "server")
]


def _graphs() -> list[nx.Graph]:
    clustered = nx.connected_caveman_graph(6, 7)
    named = nx.relabel_nodes(clustered, {n: f"pkg{n % 4}/mod_{n}.py" for n in clustered})
    weighted = nx.les_miserables_graph()
    partly_weighted = nx.karate_club_graph()
    for i, (u, v) in enumerate(partly_weighted.edges()):
        if i % 3:
            partly_weighted[u][v]["weight"] = 0.5 + i % 4
        else:
            partly_weighted[u][v].pop("weight", None)
    with_self_loop = nx.Graph(named)
    with_self_loop.add_edge("pkg0/mod_0.py", "pkg0/mod_0.py")
    view = named.subgraph([n for n in named if not n.startswith("pkg3/")])
    return [named, weighted, partly_weighted, with_self_loop, view]


@pytest.mark.parametrize("graph", _graphs())
def test_partition_matches_the_graspologic_wrapper(graph: nx.Graph) -> None:
    partition_module = pytest.importorskip("graspologic.partition")
    expected = partition_module.leiden(graph, random_seed=42)
    assert leiden_partition(graph, seed=42) == expected


def test_directed_graphs_are_rejected() -> None:
    with pytest.raises(ValueError):
        leiden_partition(nx.DiGraph([("a", "b")]), seed=42)


def test_colliding_node_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        leiden_partition(nx.Graph([(1, "1"), ("1", "x")]), seed=42)


def test_cli_and_community_detection_do_not_import_graspologic() -> None:
    """A fresh interpreter, since ``sys.modules`` is shared across the test session."""
    probe = (
        "import sys, networkx as nx; "
        "import repowise.cli.main, repowise.cli.commands.update_cmd.command; "
        "from repowise.core.analysis.communities import _partition; "
        "assert _partition(nx.connected_caveman_graph(3, 4))[1] == 'leiden'; "
        "print(sorted({m.split('.')[0] for m in sys.modules} "
        "& {'graspologic', 'umap', 'pynndescent', 'sklearn'}))"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(_SRC_DIRS)}
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, env=env
    )
    assert out.stdout.strip().splitlines()[-1] == "[]"
