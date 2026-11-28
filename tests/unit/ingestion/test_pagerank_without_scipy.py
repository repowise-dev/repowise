"""PageRank runs on networkx's pure-Python power iteration, with no scipy.

The scores must match networkx's scipy solver (the one ``nx.pagerank``
dispatches to) on graphs with dangling nodes, personalization and weights,
and a PageRank call must never import scipy.
"""

from __future__ import annotations

import subprocess
import sys

import networkx as nx
import pytest

from repowise.core.ingestion.graph._pagerank import pagerank

_PARITY_ATOL = 1e-12


def _graphs() -> dict[str, nx.DiGraph]:
    ring = nx.DiGraph([(f"n{i}", f"n{(i * 7 + 3) % 61}") for i in range(61)])
    ring.add_edges_from((f"n{i}", f"n{(i * 11 + 5) % 61}") for i in range(0, 61, 2))

    dangling = nx.DiGraph([("a", "b"), ("b", "c"), ("a", "c"), ("d", "a")])
    dangling.add_edge("c", "sink")  # sink has no out-edges
    dangling.add_node("island")  # no edges at all

    weighted = nx.DiGraph()
    weighted.add_weighted_edges_from(
        [("a", "b", 3.0), ("b", "c", 0.5), ("c", "a", 1.0), ("a", "c", 2.0), ("c", "d", 4.0)]
    )
    return {"ring": ring, "dangling": dangling, "weighted": weighted}


@pytest.mark.parametrize("name", ["ring", "dangling", "weighted"])
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"alpha": 0.7},
        {"max_iter": 50, "tol": 1e-4},
        {"personalization": "first-two"},
        {"dangling": "first-two"},
        {"weight": None},
    ],
)
def test_matches_networkx_scipy_pagerank(name, kwargs):
    pytest.importorskip("scipy")
    from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_scipy as scipy_pagerank

    g = _graphs()[name]
    nodes = list(g)
    resolved = dict(kwargs)
    for key in ("personalization", "dangling"):
        if resolved.get(key) == "first-two":
            resolved[key] = {nodes[0]: 2.0, nodes[1]: 1.0}

    ours = pagerank(g, **resolved)
    theirs = scipy_pagerank(g, **resolved)

    assert list(ours) == list(theirs)
    assert max(abs(ours[n] - theirs[n]) for n in nodes) < _PARITY_ATOL


@pytest.mark.parametrize("name", ["ring", "dangling", "weighted"])
def test_matches_networkx_dense_eigen_solution(name):
    """Runs without scipy: networkx's numpy eigen solver is the exact answer."""
    pytest.importorskip("numpy")
    from networkx.algorithms.link_analysis.pagerank_alg import _pagerank_numpy

    g = _graphs()[name]
    personalization = {n: float(i % 3 + 1) for i, n in enumerate(g)}
    ours = pagerank(g, personalization=personalization, tol=1e-14, max_iter=2000)
    exact = _pagerank_numpy(g, personalization=personalization)

    assert max(abs(ours[n] - exact[n]) for n in g) < 1e-10


def test_defaults_match_nx_pagerank_signature():
    g = _graphs()["dangling"]
    assert pagerank(g) == pagerank(g, alpha=0.85, max_iter=100, tol=1.0e-6, weight="weight")
    assert pagerank(nx.DiGraph()) == {}


def test_non_convergence_raises_like_networkx():
    with pytest.raises(nx.PowerIterationFailedConvergence):
        pagerank(_graphs()["ring"], max_iter=1, tol=1e-15)


def test_pagerank_never_imports_scipy():
    probe = (
        "import sys, networkx as nx; "
        "from repowise.core.ingestion.graph._pagerank import pagerank; "
        "pagerank(nx.DiGraph([('a', 'b'), ('b', 'c'), ('c', 'a')])); "
        "print(sorted(m for m in sys.modules if m == 'scipy' or m.startswith('scipy.')))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_installed_networkx_still_ships_the_pure_python_solver():
    """A networkx release without it would send PageRank back through scipy."""
    from repowise.core.ingestion.graph import _pagerank

    assert _pagerank._power_iteration.__name__ == "_pagerank_python"
