"""The same graph gets the same communities in every process.

A ``subgraph`` view that keeps under half its parent's nodes iterates them in
set order, which follows the process's hash seed, and Leiden and Louvain both
depend on node and edge order even when seeded. Symbol graphs are mostly
isolates, so every update re-partitioned an unchanged graph differently.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC_DIRS = [
    str(Path(__file__).resolve().parents[3] / "packages" / pkg / "src")
    for pkg in ("cli", "core", "server")
]

_SCRIPT = """
import networkx as nx
from repowise.core.analysis.communities import detect_file_communities, detect_symbol_communities

karate = nx.karate_club_graph()
graph = nx.DiGraph()
for n in range(80):
    graph.add_node(f"src/m{n}.py", node_type="file")
    graph.add_node(f"src/m{n}.py::f", node_type="symbol")
for u, v in karate.edges():
    graph.add_edge(f"src/m{u}.py::f", f"src/m{v}.py::f", edge_type="calls")
    graph.add_edge(f"src/m{u}.py", f"src/m{v}.py", edge_type="imports")

def groups(assignment):
    out = {}
    for node, cid in assignment.items():
        out.setdefault(cid, []).append(node)
    return sorted(sorted(members) for members in out.values())

print(groups(detect_symbol_communities(graph)))
print(groups(detect_file_communities(graph)[0]))
"""


def _partition_under_seed(seed: str) -> str:
    env = {
        **os.environ,
        "PYTHONHASHSEED": seed,
        "PYTHONPATH": os.pathsep.join(_SRC_DIRS),
    }
    out = subprocess.run(
        [sys.executable, "-c", _SCRIPT], env=env, capture_output=True, text=True, check=True
    )
    # Log lines carry timestamps; the partitions are the lines that are lists.
    return "\n".join(line for line in out.stdout.splitlines() if line.startswith("[["))


def test_communities_do_not_depend_on_the_hash_seed() -> None:
    first = _partition_under_seed("1")
    assert all(_partition_under_seed(seed) == first for seed in ("2", "3", "4"))
