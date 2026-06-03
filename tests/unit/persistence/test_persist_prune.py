"""Tests for full-run stale file-scoped row pruning.

A full pipeline result is authoritative for which files exist. When a file
disappears from the latest result (e.g. a directory newly added to
``exclude_patterns``), its previously-persisted rows must be pruned so stale
graph / dead-code / health / git data does not leak into those surfaces.
"""

from __future__ import annotations

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
from repowise.core.pipeline.persist import _prune_stale_file_rows
from tests.unit.persistence.helpers import insert_repo

STALE = "tools/debug.js"
KEPT = "src/app.js"


async def _seed(session, repo_id: str) -> None:
    """Seed file-scoped rows for both a stale and a kept file."""
    for path in (STALE, KEPT):
        # graph: one file node + one symbol node owned by the file
        session.add(GraphNode(repository_id=repo_id, node_id=path, node_type="file"))
        session.add(
            GraphNode(
                repository_id=repo_id,
                node_id=f"{path}::main",
                node_type="symbol",
                file_path=path,
            )
        )
        session.add(GraphMetric(repository_id=repo_id, node_id=path))
        session.add(
            WikiSymbol(
                repository_id=repo_id,
                file_path=path,
                symbol_id=f"{path}::main",
                name="main",
                qualified_name="main",
                kind="function",
            )
        )
        session.add(
            SecurityFinding(
                repository_id=repo_id,
                file_path=path,
                kind="hardcoded_secret",
                severity="high",
            )
        )
        session.add(DeadCodeFinding(repository_id=repo_id, kind="unreachable_file", file_path=path))
        session.add(HealthFileMetric(repository_id=repo_id, file_path=path))
        session.add(
            HealthFinding(
                repository_id=repo_id,
                file_path=path,
                biomarker_type="complexity",
                severity="low",
            )
        )
        session.add(GitMetadata(repository_id=repo_id, file_path=path))

    # An edge between the two files (references the stale file node).
    session.add(
        GraphEdge(
            repository_id=repo_id,
            source_node_id=KEPT,
            target_node_id=STALE,
            edge_type="imports",
        )
    )
    # An edge that survives (both endpoints kept).
    session.add(
        GraphEdge(
            repository_id=repo_id,
            source_node_id=KEPT,
            target_node_id=f"{KEPT}::main",
            edge_type="contains",
        )
    )
    await session.flush()


async def _paths(session, model, column, repo_id) -> set[str]:
    rows = (
        (await session.execute(select(column).where(model.repository_id == repo_id)))
        .scalars()
        .all()
    )
    return set(rows)


async def test_prune_removes_stale_file_rows(async_session):
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)

    await _prune_stale_file_rows(async_session, repo.id, {KEPT}, {KEPT})
    await async_session.commit()

    # graph nodes: stale file node + its symbol node gone; kept ones remain.
    node_ids = await _paths(async_session, GraphNode, GraphNode.node_id, repo.id)
    assert node_ids == {KEPT, f"{KEPT}::main"}

    # graph edges referencing the stale node are gone; the kept edge survives.
    edges = (
        (await async_session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo.id)))
        .scalars()
        .all()
    )
    assert [(e.source_node_id, e.target_node_id) for e in edges] == [(KEPT, f"{KEPT}::main")]

    # Every remaining file-scoped table holds only the kept path.
    assert await _paths(async_session, GraphMetric, GraphMetric.node_id, repo.id) == {KEPT}
    assert await _paths(async_session, WikiSymbol, WikiSymbol.file_path, repo.id) == {KEPT}
    assert await _paths(async_session, SecurityFinding, SecurityFinding.file_path, repo.id) == {
        KEPT
    }
    assert await _paths(async_session, DeadCodeFinding, DeadCodeFinding.file_path, repo.id) == {
        KEPT
    }
    assert await _paths(async_session, HealthFileMetric, HealthFileMetric.file_path, repo.id) == {
        KEPT
    }
    assert await _paths(async_session, HealthFinding, HealthFinding.file_path, repo.id) == {KEPT}
    assert await _paths(async_session, GitMetadata, GitMetadata.file_path, repo.id) == {KEPT}


async def test_prune_noop_on_empty_current_paths(async_session):
    """Empty current set must NOT wipe the repo (guards broken pipeline runs)."""
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)

    await _prune_stale_file_rows(async_session, repo.id, set(), set())
    await async_session.commit()

    # All rows for both files still present.
    assert await _paths(async_session, GitMetadata, GitMetadata.file_path, repo.id) == {
        STALE,
        KEPT,
    }
    node_ids = await _paths(async_session, GraphNode, GraphNode.node_id, repo.id)
    assert STALE in node_ids and KEPT in node_ids


async def test_prune_scoped_to_repo(async_session):
    """Pruning one repo must not touch another repo's rows."""
    repo_a = await insert_repo(async_session, local_path="/tmp/repo-a", name="a")
    repo_b = await insert_repo(async_session, local_path="/tmp/repo-b", name="b")
    await _seed(async_session, repo_a.id)
    await _seed(async_session, repo_b.id)

    await _prune_stale_file_rows(async_session, repo_a.id, {KEPT}, {KEPT})
    await async_session.commit()

    # repo_b is untouched.
    assert await _paths(async_session, GitMetadata, GitMetadata.file_path, repo_b.id) == {
        STALE,
        KEPT,
    }


async def test_prune_keeps_git_indexed_unparsed_file(async_session):
    """git_metadata keys off the git set, so a git-tracked unparsed file survives."""
    unparsed = "src/unparsed-but-git-indexed.js"
    repo = await insert_repo(async_session)
    await _seed(async_session, repo.id)
    async_session.add(GitMetadata(repository_id=repo.id, file_path=unparsed))
    await async_session.flush()

    # Graph set lacks the unparsed file; git set includes it.
    await _prune_stale_file_rows(async_session, repo.id, {KEPT}, {KEPT, unparsed})
    await async_session.commit()

    assert await _paths(async_session, GitMetadata, GitMetadata.file_path, repo.id) == {
        KEPT,
        unparsed,
    }
    # It was never parsed, so it has no graph node either way.
    assert await _paths(async_session, GraphNode, GraphNode.node_id, repo.id) == {
        KEPT,
        f"{KEPT}::main",
    }
