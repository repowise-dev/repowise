"""``repowise update --working-tree`` indexes uncommitted work."""

from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.commands.update_cmd import command as update_command_mod
from repowise.cli.main import cli
from tests.unit.cli.test_update_e2e import _git, _index_full, _make_git_repo


def _capture_run_update(monkeypatch) -> dict:
    seen: dict = {}

    def _fake(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(update_command_mod, "run_update", _fake)
    return seen


def test_working_tree_flag_reaches_run_update(monkeypatch, tmp_path: Path) -> None:
    seen = _capture_run_update(monkeypatch)
    result = CliRunner().invoke(cli, ["update", str(tmp_path), "--working-tree"])
    assert result.exit_code == 0, result.output
    assert seen["include_working_tree"] is True


def test_update_without_flag_stays_commit_anchored(monkeypatch, tmp_path: Path) -> None:
    seen = _capture_run_update(monkeypatch)
    result = CliRunner().invoke(cli, ["update", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen["include_working_tree"] is False


async def _symbols_and_call_edges(repo: Path) -> tuple[set[str], set[tuple[str, str]]]:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import GraphEdge, GraphNode

    engine = create_engine(resolve_db_url(repo))
    try:
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            nodes = {n.node_id for n in (await session.execute(select(GraphNode))).scalars()}
            edges = {
                (e.source_node_id, e.target_node_id)
                for e in (await session.execute(select(GraphEdge))).scalars()
                if e.edge_type == "calls"
            }
            return nodes, edges
    finally:
        await engine.dispose()


def test_uncommitted_rename_is_indexed_with_working_tree(tmp_path: Path) -> None:
    from repowise.cli.helpers import save_state

    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    head = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {"last_sync_commit": head, "docs_enabled": False})

    # Rename the function and its caller's call site, without committing.
    (repo / "a.py").write_text("def alpha_renamed():\n    return 1\n")
    (repo / "b.py").write_text(
        "from a import alpha_renamed\n\n\ndef beta():\n    return alpha_renamed() + 1\n"
    )

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", "--working-tree"])
    assert result.exit_code == 0, result.output

    nodes, calls = asyncio.run(_symbols_and_call_edges(repo))
    assert "a.py::alpha_renamed" in nodes
    assert "a.py::alpha" not in nodes
    assert ("b.py::beta", "a.py::alpha_renamed") in calls
    assert _git(repo, "rev-parse", "HEAD") == head


def test_workspace_update_indexes_uncommitted_work_with_working_tree(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.cli.helpers import save_state
    from tests.unit.cli.test_workspace_docs_pointer import _make_workspace

    repo = _make_workspace(tmp_path)
    _index_full(repo)
    save_state(repo, {"last_sync_commit": _git(repo, "rev-parse", "HEAD"), "docs_enabled": False})
    monkeypatch.chdir(repo)
    # Records the indexed head in the workspace config, so only the edit below is new.
    assert CliRunner().invoke(cli, ["update", "--workspace"]).exit_code == 0

    (repo / "a.py").write_text("def alpha_renamed():\n    return 1\n")

    plain = CliRunner().invoke(cli, ["update", "--workspace"])
    assert plain.exit_code == 0, plain.output
    assert "All repos are up to date" in plain.output

    result = CliRunner().invoke(cli, ["update", "--workspace", "--working-tree"])
    assert result.exit_code == 0, result.output
    nodes, _ = asyncio.run(_symbols_and_call_edges(repo))
    assert "a.py::alpha_renamed" in nodes


_EDITED_A = (
    "def alpha():\n    return 1\n\n\n"
    "def gamma(x):\n    if x:\n        for i in range(x):\n"
    "            if i > 2:\n                return i\n    return 0\n"
)
_EDITED_B = "from a import alpha, gamma\n\n\ndef beta():\n    return alpha() + gamma(4)\n"


async def _store_view(repo: Path) -> dict:
    """Symbols, call edges, health, dead-code and git rows of the two edited files."""
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import (
        DeadCodeFinding,
        GitMetadata,
        HealthFileMetric,
        WikiSymbol,
    )

    edited = ("a.py", "b.py")

    nodes, calls = await _symbols_and_call_edges(repo)
    # The update also writes editor files that a clone never has.
    nodes = {n for n in nodes if n.startswith(edited)}
    engine = create_engine(resolve_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            symbols = {
                (s.file_path, s.symbol_id, s.start_line, s.end_line)
                for s in (await session.execute(select(WikiSymbol))).scalars()
            }
            health = {
                m.file_path: (m.nloc, m.max_ccn, m.max_nesting, m.score)
                for m in (await session.execute(select(HealthFileMetric))).scalars()
                if m.file_path in edited
            }
            dead = {
                (d.file_path, d.kind, d.symbol_name, d.start_line)
                for d in (await session.execute(select(DeadCodeFinding))).scalars()
                if d.file_path in edited
            }
            git = {
                g.file_path: (g.commit_count_total, g.last_commit_at, g.co_change_partners_json)
                for g in (await session.execute(select(GitMetadata))).scalars()
                if g.file_path in edited
            }
    finally:
        await engine.dispose()
    return {
        "nodes": nodes,
        "calls": calls,
        "symbols": symbols,
        "health": health,
        "dead_code": dead,
        "git": git,
    }


def _spy(monkeypatch, owner, name: str) -> list:
    calls: list = []
    real = getattr(owner, name)

    def _wrapped(*args, **kwargs):
        calls.append(name)
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, _wrapped)
    return calls


def _commit_edit(repo: Path) -> None:
    # The first run's lock is released at exit, which never comes in-process.
    update_command_mod.release_update_lock(repo)
    _git(repo, "add", "a.py", "b.py")
    _git(repo, "commit", "-m", "add gamma")


def _phase_rows(repo: Path) -> set[str]:
    from repowise.cli.helpers import load_state

    return set(load_state(repo).get("phase_timings") or {})


def test_working_tree_only_update_skips_history_phases_until_the_commit(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.cli.helpers import load_state, save_state
    from repowise.core.pipeline import incremental as core_incremental

    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    head = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {"last_sync_commit": head, "docs_enabled": False})
    health_at_head = asyncio.run(_store_view(repo))["health"]

    analysis = _spy(monkeypatch, update_command_mod, "_run_partial_analysis")
    drift = _spy(monkeypatch, update_command_mod, "_run_doc_drift_partial")
    commits = _spy(monkeypatch, core_incremental, "persist_incremental_commits")

    (repo / "a.py").write_text(_EDITED_A)
    (repo / "b.py").write_text(_EDITED_B)
    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", "--working-tree"])
    assert result.exit_code == 0, result.output

    assert (analysis, drift, commits) == ([], [], [])
    history_phases = {"persist.commits", "persist.health", "analysis.health", "rescore"}
    assert not history_phases & _phase_rows(repo)
    view = asyncio.run(_store_view(repo))
    assert "a.py::gamma" in view["nodes"]
    assert ("b.py::beta", "a.py::gamma") in view["calls"]
    # Health still describes HEAD, and the pointer has not moved past it.
    assert view["health"] == health_at_head
    assert load_state(repo)["last_sync_commit"] == head

    _commit_edit(repo)
    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output

    assert analysis and drift and commits
    # Each label is real: the commit's update records every one of them.
    assert history_phases <= _phase_rows(repo)
    assert load_state(repo)["last_sync_commit"] == _git(repo, "rev-parse", "HEAD")


def test_working_tree_update_then_commit_converges_with_a_fresh_index(tmp_path: Path) -> None:
    from repowise.cli.helpers import save_state

    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    save_state(repo, {"last_sync_commit": _git(repo, "rev-parse", "HEAD"), "docs_enabled": False})

    (repo / "a.py").write_text(_EDITED_A)
    (repo / "b.py").write_text(_EDITED_B)
    runner = CliRunner()
    wt = runner.invoke(cli, ["update", str(repo), "--no-workspace", "--working-tree"])
    assert wt.exit_code == 0, wt.output
    _commit_edit(repo)
    plain = runner.invoke(cli, ["update", str(repo), "--no-workspace"])
    assert plain.exit_code == 0, plain.output

    fresh = tmp_path / "fresh"
    _git(tmp_path, "clone", "-q", str(repo), str(fresh))
    _index_full(fresh)

    updated, expected = asyncio.run(_store_view(repo)), asyncio.run(_store_view(fresh))
    assert expected["git"] and expected["dead_code"] and expected["health"]
    assert updated == expected


def test_working_tree_update_after_a_config_change_still_rescores(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.cli.helpers import save_state

    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    # A fingerprint with no per-dependency breakdown reads as a full config change.
    save_state(
        repo,
        {
            "last_sync_commit": _git(repo, "rev-parse", "HEAD"),
            "docs_enabled": False,
            "config_fingerprint": "an-older-config",
        },
    )
    analysis = _spy(monkeypatch, update_command_mod, "_run_partial_analysis")

    (repo / "a.py").write_text(_EDITED_A)
    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace", "--working-tree"])
    assert result.exit_code == 0, result.output
    assert analysis
