"""Tests for the incremental deleted-file prune and its floor guard.

The incremental update path used to prune nothing: deleting a file tombstoned
its wiki page and left its graph nodes, edges, metrics, symbols, health rows
and git metadata serving a file that no longer existed. These cover both
halves of the fix — that a real deletion is cleaned out of every file-scoped
table, and that a run whose parser fell over cannot be mistaken for one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    GraphEdge,
    GraphMetric,
    GraphNode,
    HealthFileMetric,
    HealthFinding,
    SecurityFinding,
    WikiSymbol,
)
from repowise.core.pipeline.persist import prune_deleted_file_rows
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_persist_prune import KEPT, STALE, _paths, _seed

# Every file-scoped table the audit found surviving a delete, with the column
# that carries the path. Kept in one list so a new table cannot be covered by
# the "deleted" assertion and forgotten by the "transient failure" one.
FILE_SCOPED = [
    (GraphMetric, GraphMetric.node_id),
    (WikiSymbol, WikiSymbol.file_path),
    (SecurityFinding, SecurityFinding.file_path),
    (DeadCodeFinding, DeadCodeFinding.file_path),
    (HealthFileMetric, HealthFileMetric.file_path),
    (HealthFinding, HealthFinding.file_path),
    (GitMetadata, GitMetadata.file_path),
]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo_with_kept_file(tmp_path: Path) -> Path:
    """A git repo where KEPT exists and is tracked, and STALE does not exist."""
    repo = tmp_path / "repo"
    (repo / Path(KEPT).parent).mkdir(parents=True)
    (repo / KEPT).write_text("export const main = () => {};\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Seed")
    _git(repo, "config", "user.email", "seed@e.com")
    _git(repo, "add", KEPT)
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


async def test_deleted_file_is_pruned_from_every_table(async_session, repo_with_kept_file):
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)

    pruned, refusals = await prune_deleted_file_rows(
        async_session, repo.id, repo_with_kept_file, live_hint={KEPT}
    )
    await async_session.commit()

    assert refusals == []
    assert pruned == 1

    # The deleted file's graph node and its symbol node are both gone.
    assert await _paths(async_session, GraphNode, GraphNode.node_id, repo.id) == {
        KEPT,
        f"{KEPT}::main",
    }
    # ...along with the edge that pointed at it.
    edges = (
        (await async_session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert [(e.source_node_id, e.target_node_id) for e in edges] == [(KEPT, f"{KEPT}::main")]

    for model, column in FILE_SCOPED:
        assert await _paths(async_session, model, column, repo.id) == {KEPT}, model.__name__


async def test_transient_parse_failure_prunes_nothing(async_session, repo_with_kept_file):
    """A run where nothing parsed must not read as a run where everything was deleted.

    This is the failure mode the parse-derived authority has and the liveness
    one does not: on Windows a file lock or an antivirus scan drops a live file
    out of ``parsed_files``, and that must never delete its rows.
    """
    # Both files exist on disk this time, so the only thing saying they are
    # gone is the empty parse result.
    (repo_with_kept_file / Path(STALE).parent).mkdir(parents=True, exist_ok=True)
    (repo_with_kept_file / STALE).write_text("// locked\n", encoding="utf-8")

    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)

    pruned, refusals = await prune_deleted_file_rows(
        async_session, repo.id, repo_with_kept_file, live_hint=set()
    )
    await async_session.commit()

    assert (pruned, refusals) == (0, [])
    assert await _paths(async_session, GraphNode, GraphNode.node_id, repo.id) == {
        KEPT,
        STALE,
        f"{KEPT}::main",
        f"{STALE}::main",
    }
    for model, column in FILE_SCOPED:
        assert await _paths(async_session, model, column, repo.id) == {KEPT, STALE}, model.__name__


async def test_git_tracked_file_missing_from_disk_survives(async_session, repo_with_kept_file):
    """The second witness: a stat that fails must not count as a deletion.

    ``Path.exists()`` answers False for a file it cannot stat, which is the
    same answer it gives for a file that is really gone. git tracking is what
    separates the two.
    """
    (repo_with_kept_file / KEPT).unlink()  # tracked, but unreadable/absent now

    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)

    pruned, _ = await prune_deleted_file_rows(
        async_session, repo.id, repo_with_kept_file, live_hint=set()
    )
    await async_session.commit()

    # STALE is neither on disk nor tracked, so it goes; KEPT is still tracked.
    assert pruned == 1
    assert await _paths(async_session, GitMetadata, GitMetadata.file_path, repo.id) == {KEPT}


async def test_synthetic_nodes_are_not_deleted_files(async_session, repo_with_kept_file):
    """``external:``/``framework:`` nodes are stored as file nodes and name no file.

    They answer "not live" to every witness there is, because no such file was
    ever meant to exist. Counting them as deletions drops their graph rows and
    every edge that reaches them, and on this path only the *changed* files'
    edges are rebuilt afterwards, so the edges do not come back. It also
    dominates the floor guard: on a Spring sample app they are 70% of the file
    nodes, so the whole prune would be refused rather than corrected.
    """
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)
    for node_id in ("external:github.com/spf13/cobra", "framework:conftest"):
        async_session.add(GraphNode(repository_id=repo.id, node_id=node_id, node_type="file"))
        async_session.add(GraphMetric(repository_id=repo.id, node_id=node_id))
    async_session.add(
        GraphEdge(
            repository_id=repo.id,
            source_node_id=KEPT,
            target_node_id="external:github.com/spf13/cobra",
            edge_type="imports",
        )
    )
    await async_session.flush()

    pruned, refusals = await prune_deleted_file_rows(
        async_session, repo.id, repo_with_kept_file, live_hint={KEPT}
    )
    await async_session.commit()

    assert (pruned, refusals) == (1, [])
    node_ids = await _paths(async_session, GraphNode, GraphNode.node_id, repo.id)
    assert "external:github.com/spf13/cobra" in node_ids
    assert "framework:conftest" in node_ids
    assert await _paths(async_session, GraphMetric, GraphMetric.node_id, repo.id) == {
        KEPT,
        "external:github.com/spf13/cobra",
        "framework:conftest",
    }
    # The edge into the external node survives with it.
    edges = (
        (await async_session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert ("external:github.com/spf13/cobra") in {e.target_node_id for e in edges}


async def test_mass_deletion_is_refused_not_applied(async_session, repo_with_kept_file):
    """A prune that would take most of a table reads as a broken run, and is refused."""
    repo = await insert_repo(async_session)
    for i in range(40):
        async_session.add(
            GitMetadata(repository_id=repo.id, file_path=f"src/module_{i}.js")
        )
    await async_session.flush()

    pruned, refusals = await prune_deleted_file_rows(
        async_session, repo.id, repo_with_kept_file, live_hint=set()
    )
    await async_session.commit()

    assert pruned == 0
    assert len(refusals) == 1
    assert "git_metadata" in refusals[0]
    assert len(await _paths(async_session, GitMetadata, GitMetadata.file_path, repo.id)) == 40


async def test_small_repo_can_still_lose_most_of_its_files(async_session, repo_with_kept_file):
    """The fraction only bites above a row floor, so a 3-file repo still prunes."""
    repo = await insert_repo(async_session)
    for name in ("a.js", "b.js", "c.js"):
        async_session.add(GitMetadata(repository_id=repo.id, file_path=f"src/{name}"))
    await async_session.flush()

    pruned, refusals = await prune_deleted_file_rows(
        async_session, repo.id, repo_with_kept_file, live_hint=set()
    )
    await async_session.commit()

    assert refusals == []
    assert pruned == 3
    assert await _paths(async_session, GitMetadata, GitMetadata.file_path, repo.id) == set()


async def test_prune_is_scoped_to_one_repo(async_session, repo_with_kept_file):
    repo_a = await insert_repo(async_session, local_path="/tmp/repo-a", name="a")
    repo_b = await insert_repo(async_session, local_path="/tmp/repo-b", name="b")
    await _seed(async_session, repo_a.id)
    await _seed(async_session, repo_b.id)

    await prune_deleted_file_rows(
        async_session, repo_a.id, repo_with_kept_file, live_hint={KEPT}
    )
    await async_session.commit()

    assert await _paths(async_session, GitMetadata, GitMetadata.file_path, repo_b.id) == {
        STALE,
        KEPT,
    }
