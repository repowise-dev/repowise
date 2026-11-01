"""Live reads follow the checkout the server serves, not the stored ``local_path``.

A moved or copied repo keeps its old ``local_path`` in the index. Reading HEAD,
state.json or the working tree through it reports the old tree, so a dirty
file in the served checkout would read as fresh and complete. Only the row the
served store identifies follows the served path; any other row keeps its own.
"""

from __future__ import annotations

import json
import shutil
import types
from pathlib import Path

import pytest

from repowise.core.persistence.models import Repository
from repowise.server.mcp_server import _meta, _server, _state
from tests.unit.cli.test_update_e2e import _git
from tests.unit.server.mcp.test_working_tree_freshness import _make_repo


def _indexed(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise").mkdir(exist_ok=True)
    (repo / ".repowise" / "state.json").write_text(json.dumps({"last_sync_commit": head}))


@pytest.fixture
def served(monkeypatch):
    def serve(path: Path | None, repo_id: str | None = "served", *, workspace: bool = False):
        monkeypatch.setattr(_state, "_repo_path", str(path) if path else None)
        monkeypatch.setattr(_state, "_served_repo_id", repo_id)
        monkeypatch.setattr(_state, "_registry", object() if workspace else None)
        _meta._dirty_paths_cache.clear()

    return serve


@pytest.mark.parametrize("old_location", ["gone", "other_checkout"])
def test_moved_repo_reports_dirty_files_of_the_served_checkout(
    tmp_path: Path, served, old_location: str
) -> None:
    old = _make_repo(tmp_path)
    _indexed(old)
    new = tmp_path / "moved"
    shutil.copytree(old, new)
    if old_location == "gone":
        shutil.rmtree(old, ignore_errors=True)
    (new / "pricing.py").write_text("def base_price():\n    return 99173\n")
    row = types.SimpleNamespace(
        id="served", updated_at=None, local_path=str(old), head_commit=None
    )
    served(new)

    out = _meta.build_meta(repository=row, targets=["pricing.py"])

    assert out["working_tree_dirty"] == 1
    assert "differs in the working tree" in out["stale_warning"]
    assert out["index_behind"] is False


def test_another_row_keeps_its_own_checkout(tmp_path: Path, served) -> None:
    # A shared store: the server serves one repo, the call reads another.
    mine = _make_repo(tmp_path)
    _indexed(mine)
    other = tmp_path / "other"
    shutil.copytree(mine, other)
    (mine / "pricing.py").write_text("def base_price():\n    return 99173\n")
    row = types.SimpleNamespace(id="other", updated_at=None, local_path=str(other), head_commit=None)
    served(mine)

    assert _meta.served_root(row, mine) == str(other)
    assert "working_tree_dirty" not in _meta.build_meta(repository=row, targets=["pricing.py"])


def test_the_served_path_needs_an_index_and_a_single_repo_server(tmp_path: Path, served) -> None:
    indexed = tmp_path / "indexed"
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / ".git").write_text("gitdir: elsewhere\n")
    row = types.SimpleNamespace(id="served", local_path=str(indexed))
    served(bare)

    assert _meta.served_root(row) == str(indexed)
    (bare / ".repowise").mkdir()
    # A worktree's .git is a file; it still counts.
    assert _meta.served_root(row) == str(bare)
    served(bare, workspace=True)
    assert _meta.served_root(row) == str(indexed)
    served(bare, repo_id=None)
    assert _meta.served_root(row) == str(indexed)


@pytest.mark.asyncio
async def test_the_served_row_is_the_one_row_its_store_holds(factory, session, repo_id) -> None:
    await session.commit()
    assert await _server._served_repository_id(factory) == repo_id

    session.add(Repository(id="second", name="second", local_path="/elsewhere"))
    await session.commit()
    assert await _server._served_repository_id(factory) is None
