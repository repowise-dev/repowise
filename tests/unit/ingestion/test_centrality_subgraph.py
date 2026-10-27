"""PageRank and betweenness ignore test-to-test edges; degrees keep them."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file
from repowise.core.test_paths import is_test_related_path

_SOURCES = {
    "app/__init__.py": "",
    "app/util.py": "def clamp(x):\n    return x\n",
    "app/core.py": "from app.util import clamp\n\ndef run(x):\n    return clamp(x)\n",
    "app/api.py": "from app.core import run\n\ndef serve():\n    return run(1)\n",
    "app/cli.py": "from app.api import serve\n\ndef main():\n    return serve()\n",
}
# Every test pulls fixtures from conftest, so it collects many in-edges.
_TESTS = {
    "tests/__init__.py": "",
    "tests/conftest.py": "from app.core import run\n\ndef client():\n    return run\n",
    **{
        f"tests/test_{name}.py": (
            f"from tests.conftest import client\nfrom app.{name} import *\n\n"
            f"def test_{name}():\n    assert client()\n"
        )
        for name in ("util", "core", "api", "cli")
    },
}


def _parsed(root: Path, rel: str, content: str):
    abs_ = root / rel
    abs_.parent.mkdir(parents=True, exist_ok=True)
    abs_.write_text(content)
    fi = FileInfo(
        path=rel,
        abs_path=str(abs_),
        language="python",  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=is_test_related_path(rel, "python"),
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return parse_file(fi, content.encode("utf-8"))


@pytest.fixture
def pytest_conftest_hub(tmp_path: Path) -> GraphBuilder:
    gb = GraphBuilder(str(tmp_path))
    for rel, content in {**_SOURCES, **_TESTS}.items():
        gb.add_file(_parsed(tmp_path, rel, content))
    gb.build()
    return gb


def test_conftest_ranks_below_every_source_module(pytest_conftest_hub: GraphBuilder) -> None:
    gb = pytest_conftest_hub
    # The fixture only proves something if conftest really is a test-import hub.
    assert gb.file_subgraph().in_degree("tests/conftest.py") == 4

    pr = gb.pagerank()
    sources = [p for p in _SOURCES if p != "app/__init__.py"]
    assert pr["tests/conftest.py"] < min(pr[p] for p in sources)


def test_only_edges_between_two_tests_are_hidden(pytest_conftest_hub: GraphBuilder) -> None:
    gb = pytest_conftest_hub
    view = gb.centrality_subgraph()
    assert not view.has_edge("tests/test_core.py", "tests/conftest.py")
    assert view.has_edge("tests/test_core.py", "app/core.py")
    assert view.has_edge("tests/conftest.py", "app/core.py")
    assert set(view.nodes) == set(gb.file_subgraph().nodes)


def test_degrees_and_file_subgraph_keep_test_edges(pytest_conftest_hub: GraphBuilder) -> None:
    gb = pytest_conftest_hub
    assert gb.file_subgraph().has_edge("tests/test_core.py", "tests/conftest.py")
    assert gb.in_degree()["tests/conftest.py"] == 4


def test_view_is_cached_and_reset_with_the_graph(pytest_conftest_hub: GraphBuilder) -> None:
    gb = pytest_conftest_hub
    view = gb.centrality_subgraph()
    assert gb.centrality_subgraph() is view
    gb._invalidate_subgraph_caches()
    assert gb.centrality_subgraph() is not view


def test_builder_with_built_views_still_pickles(pytest_conftest_hub: GraphBuilder) -> None:
    import pickle

    gb = pytest_conftest_hub
    gb.centrality_subgraph()
    gb.cycle_subgraph()
    reloaded = pickle.loads(pickle.dumps(gb))
    assert reloaded.pagerank() == gb.pagerank()
    assert set(reloaded.centrality_subgraph().edges) == set(gb.centrality_subgraph().edges)


def test_graph_store_scores_the_same_view(pytest_conftest_hub: GraphBuilder) -> None:
    from repowise.core.persistence.stores import InProcessGraphStore

    gb = pytest_conftest_hub
    store = InProcessGraphStore.from_graph(gb.graph())
    assert store.pagerank() == pytest.approx(gb.pagerank())
