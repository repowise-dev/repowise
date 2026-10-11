"""The update path records one timing row per stage.

``init`` has recorded ``phase_timings`` for a while; ``update`` recorded
nothing, so a slow update could not say which stage made it slow. These pin
the rows each core step writes when handed the run's table, and that a caller
with no table gets exactly the behaviour it had before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.pipeline import PhaseTimings
from repowise.core.pipeline.incremental import (
    persist_incremental_index,
    rebuild_graph_and_git,
    run_partial_analysis,
)


def _git_repo_with_one_file(tmp_path: Path) -> Path:
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("feat: add a")
    repo.close()
    (tmp_path / ".repowise").mkdir()
    return tmp_path


async def test_rebuild_records_every_sub_stage(tmp_path: Path) -> None:
    repo = _git_repo_with_one_file(tmp_path)
    timings = PhaseTimings()

    _pf, _sm, builder, _rs, _count, _meta = await rebuild_graph_and_git(
        repo, [], {}, [], timings=timings
    )

    rows = timings.totals
    assert {
        "rebuild",
        "rebuild.traverse",
        "rebuild.read",
        "rebuild.parse",
        "rebuild.graph",
        "rebuild.git",
        "rebuild.metrics",
    } <= set(rows)
    # Every sub-stage is inside the parent's span.
    assert all(rows[k] <= rows["rebuild"] for k in rows if k.startswith("rebuild."))

    # Partial analysis writes its two rows on the same table.
    run_partial_analysis(
        repo,
        builder,
        {},
        _pf,
        [],
        stored_git_meta={},
        timings=timings,
    )
    assert {"analysis.health", "analysis.dead_code"} <= set(timings.totals)


async def test_persist_records_the_steps_that_ran(tmp_path: Path) -> None:
    """A skipped step leaves no row: absent means skipped, not free."""
    import networkx as nx

    (tmp_path / ".repowise").mkdir()

    class FakeBuilder:
        def graph(self):
            return nx.DiGraph()

    timings = PhaseTimings()
    await persist_incremental_index(
        tmp_path,
        FakeBuilder(),
        {},
        None,
        None,
        [],
        log=lambda _msg: None,
        parsed_files=[],
        timings=timings,
    )

    rows = timings.totals
    assert {"persist", "persist.open", "persist.page_tree", "persist.graph_nodes"} <= set(rows)
    # No git rows, no dead-code report, no health report: those steps did not run.
    assert not {"persist.git", "persist.commits", "persist.dead_code", "persist.health"} & set(
        rows
    )
    assert all(rows[k] <= rows["persist"] for k in rows if k.startswith("persist."))


async def test_no_table_records_nothing_and_changes_nothing(tmp_path: Path) -> None:
    """Callers that pass no table (workspace update, tests) are untouched."""
    import networkx as nx

    (tmp_path / ".repowise").mkdir()

    class FakeBuilder:
        def graph(self):
            return nx.DiGraph()

    degraded: list[str] = []
    await persist_incremental_index(
        tmp_path,
        FakeBuilder(),
        {},
        None,
        None,
        [],
        log=lambda _msg: None,
        parsed_files=[],
        degraded=degraded,
    )
    # The same degradations as before the table existed, none about timing.
    assert all("timing" not in entry.lower() for entry in degraded)


@pytest.mark.parametrize("stage", ["analysis.health", "analysis.dead_code"])
def test_analysis_rows_close_when_the_analyzer_raises(monkeypatch, tmp_path: Path, stage: str):
    """Both analyses are best-effort; a failure must still close its row."""
    import networkx as nx

    from repowise.core.analysis import health as health_mod
    from repowise.core.analysis.dead_code import analyzer as dead_code_mod

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(health_mod, "HealthAnalyzer", Boom)
    monkeypatch.setattr(dead_code_mod, "DeadCodeAnalyzer", Boom)

    class FakeBuilder:
        def __init__(self):
            self._parsed_files: list = []

        def graph(self):
            return nx.DiGraph()

    timings = PhaseTimings()
    run_partial_analysis(tmp_path, FakeBuilder(), {}, [], [], timings=timings)
    assert stage in timings.totals
