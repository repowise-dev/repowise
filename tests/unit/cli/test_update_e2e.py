"""End-to-end ``repowise update`` on a real fixture repo.

The test that would have caught #669: a real (index-only) full pipeline run
seeds wiki.db, a new commit lands, and one CliRunner ``update`` invocation
must refresh every index layer — graph nodes, health metrics, the knowledge
graph rows AND the exported knowledge-graph.json, the DB head_commit stamp,
and state.json — not just the subset the incremental path happened to touch.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.main import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return result.stdout.strip()


def _make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    (repo / "b.py").write_text("from a import alpha\n\n\ndef beta():\n    return alpha() + 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _index_full(repo: Path) -> None:
    """Real index-only init: full pipeline + persistence + KG artifact."""
    from repowise.core.pipeline.full_index import index_repo_full

    asyncio.run(index_repo_full(repo))


async def _db_snapshot(repo: Path) -> dict:
    """Read the layers the update must keep fresh from wiki.db."""
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import (
        GraphNode,
        HealthFileMetric,
        KnowledgeGraphLayer,
        Repository,
    )

    engine = create_engine(resolve_db_url(repo))
    try:
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo_row = (await session.execute(select(Repository))).scalars().first()
            graph_node_ids = {
                n.node_id for n in (await session.execute(select(GraphNode))).scalars()
            }
            health_paths = {
                m.file_path for m in (await session.execute(select(HealthFileMetric))).scalars()
            }
            kg_layer_count = len(
                list((await session.execute(select(KnowledgeGraphLayer))).scalars())
            )
            return {
                "head_commit": repo_row.head_commit if repo_row else None,
                "graph_node_ids": graph_node_ids,
                "health_paths": health_paths,
                "kg_layer_count": kg_layer_count,
            }
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_update_refreshes_every_index_layer(tmp_path: Path) -> None:
    repo = _make_git_repo(tmp_path)
    _index_full(repo)

    base = _git(repo, "rev-parse", "HEAD")
    from repowise.cli.helpers import save_state

    save_state(repo, {"last_sync_commit": base, "docs_enabled": False})

    kg_path = repo / ".repowise" / "knowledge-graph.json"
    assert kg_path.is_file(), "init must export the KG artifact"
    assert "c.py" not in kg_path.read_text(encoding="utf-8")

    # A new file changes the graph shape, so every layer must move.
    (repo / "c.py").write_text(
        "from b import beta\n\n\ndef gamma():\n    return beta() * 2\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add c.py")
    new_head = _git(repo, "rev-parse", "HEAD")

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output

    # state.json advanced.
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["last_sync_commit"] == new_head

    snap = asyncio.run(_db_snapshot(repo))
    # DB freshness stamp (the /repos + MCP _meta signal) matches HEAD.
    assert snap["head_commit"] == new_head
    # Graph rows include the new file.
    assert "c.py" in snap["graph_node_ids"]
    # Health metrics were computed for the new file.
    assert "c.py" in snap["health_paths"]
    # KG rows exist (layers persisted by the refresh, not just at init).
    assert snap["kg_layer_count"] >= 1

    # The exported artifact was rewritten for the new graph shape (#669:
    # this file used to stay frozen at init forever), and state.json carries
    # the refreshed fingerprint that gates the next rebuild.
    kg_after = json.loads(kg_path.read_text(encoding="utf-8"))
    assert any("c.py" in (n.get("filePath") or n.get("id") or "") for n in kg_after["nodes"])
    assert state.get("knowledge_graph", {}).get("fingerprint")


def test_update_degrades_visibly_when_a_step_fails(tmp_path: Path, monkeypatch) -> None:
    """A failing best-effort persist step must surface in the degraded list
    (exit 0, warning block rendered) instead of being silently swallowed."""
    repo = _make_git_repo(tmp_path)
    _index_full(repo)

    base = _git(repo, "rev-parse", "HEAD")
    from repowise.cli.helpers import save_state

    save_state(repo, {"last_sync_commit": base, "docs_enabled": False})

    (repo / "c.py").write_text("def gamma():\n    return 3\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add c.py")

    import repowise.core.pipeline.persist as persist_mod

    async def _boom(*args, **kwargs):
        raise RuntimeError("graph nodes exploded")

    monkeypatch.setattr(persist_mod, "persist_graph_nodes", _boom)

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])

    assert result.exit_code == 0, result.output
    assert "degraded step(s)" in result.output
    assert "Graph nodes persist" in result.output
