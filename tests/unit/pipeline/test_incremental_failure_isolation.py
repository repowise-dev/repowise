"""A degraded persist step must be recorded precisely enough to be repaired.

``persist_incremental_index`` degrades every step it can, and the caller then
advances ``last_sync_commit`` regardless. Which failures are worth a repair
marker is therefore not a detail: a step whose input was this commit range
strands data when the pointer moves past it, and a step that re-derives the
whole repo every run does not. Marking the second kind would let one
permanently broken step pin the repair window open forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.pipeline.incremental import persist_incremental_index


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    return tmp_path


async def test_range_scoped_failure_is_marked_and_repo_wide_failure_is_not(repo, monkeypatch):
    from repowise.core.persistence import crud

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(crud, "upsert_git_metadata_bulk", _boom)

    degraded: list[str] = []
    failed_steps: list[str] = []
    await persist_incremental_index(
        repo,
        # No graph builder worth the name: the repo-wide steps that consume it
        # fail on their own, which is exactly the contrast under test.
        object(),
        {"src/a.py": {"file_path": "src/a.py"}},
        None,
        None,
        ["src/a.py"],
        log=lambda _msg: None,
        degraded=degraded,
        failed_steps=failed_steps,
    )

    # The forced failure was scoped to this run's changed files, so it strands
    # data the pointer is about to skip: it has to be repairable.
    assert "Git persist" in failed_steps
    assert any(entry.startswith("Git persist:") for entry in degraded)

    # The graph node persist failed too (no real builder), and it is repo-wide:
    # the next update rebuilds it from scratch, so it is reported and not
    # marked for repair.
    assert any(entry.startswith("Graph nodes persist:") for entry in degraded)
    assert "Graph nodes persist" not in failed_steps


async def test_prune_spares_every_file_node_the_rebuilt_graph_still_holds(repo, monkeypatch):
    """Nodes that name no file survive, including the ones with no prefix to spot.

    ``external:`` and ``framework:`` nodes are recognisable, but Spring's SPI
    source is minted as the bare path ``META-INF/services/<iface>`` with
    ``node_type="file"`` (ingestion/framework_edges/spring.py). It is on no
    disk and in no git index, so a prune that asked only those two witnesses
    would delete it and every framework edge hanging off it, and only
    *changed* files' edges are rebuilt afterwards.

    The invariant that covers all three without a list to keep in sync: a node
    this run's graph build still contains is live, whatever it names.
    """
    import networkx as nx

    from repowise.core.pipeline import persist as persist_mod

    graph = nx.DiGraph()
    graph.add_node("META-INF/services/com.example.Spi", node_type="file")
    graph.add_node("external:github.com/spf13/cobra", node_type="file")
    graph.add_node("src/kept.py", node_type="file")
    graph.add_node("src/kept.py::main", node_type="symbol", file_path="src/kept.py")
    # "src/gone.py" is deliberately absent: a deleted file drops out of the
    # rebuilt graph, which is what leaves it a prune candidate.

    class FakeBuilder:
        def graph(self):
            return graph

    seen: dict = {}

    async def spy(session, repo_id, repo_path, *, live_hint=None):
        seen["hint"] = live_hint
        return 0, []

    monkeypatch.setattr(persist_mod, "prune_deleted_file_rows", spy)

    await persist_incremental_index(
        repo,
        FakeBuilder(),
        {},
        None,
        None,
        [],
        log=lambda _msg: None,
        parsed_files=[],
    )

    assert "META-INF/services/com.example.Spi" in seen["hint"]
    assert "external:github.com/spf13/cobra" in seen["hint"]
    assert "src/kept.py" in seen["hint"]
    assert "src/gone.py" not in seen["hint"]
    # Symbol nodes are not file paths and must not widen the hint.
    assert "src/kept.py::main" not in seen["hint"]


def test_the_range_scoped_set_is_exactly_these_steps():
    """Which steps are repairable is a judgement, so it is pinned rather than assumed.

    Getting it wrong is silent in both directions: a range-scoped step left
    untagged strands its data forever (the bug), and a repo-wide step tagged by
    mistake keeps the repair window open on every run until the commit bound
    gives up on it. Neither shows up in any other test.

    ``Commit capture`` is the one that reads range-scoped and is not: it bounds
    its walk by the newest ``committed_at`` already persisted, so the next run
    re-walks whatever it skipped. ``External systems refresh`` is the reverse,
    since it only runs when a dependency manifest is in *this* diff.
    """
    import re
    from pathlib import Path

    import repowise.core.pipeline.incremental as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    tagged = set(re.findall(r'_skip\(\s*"([^"]+)",\s*exc,\s*range_scoped=True', source))
    assert tagged == {
        "Tombstone marking",
        "Git persist",
        "Dead-code persist",
        "Health persist",
        "Symbol persist",
        "Graph edges persist",
        "External systems refresh",
        "Tombstone full-text removal",
    }


async def test_clean_run_marks_nothing(repo):
    """No failures means no repair marker, whatever else degraded."""
    failed_steps: list[str] = []
    await persist_incremental_index(
        repo,
        object(),
        {},
        None,
        None,
        [],
        log=lambda _msg: None,
        degraded=[],
        failed_steps=failed_steps,
    )
    assert failed_steps == []
