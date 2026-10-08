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
