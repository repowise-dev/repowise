"""get_top_entry_points ranks symbols by the score stored in their meta blob."""

from __future__ import annotations

import json

import pathspec

from repowise.core.persistence.crud import get_top_entry_points
from repowise.core.persistence.models import GraphNode
from tests.unit.persistence.helpers import insert_repo


def _symbol(repo_id: str, node_id: str, meta: object, **extra: object) -> GraphNode:
    blob = meta if isinstance(meta, str) else json.dumps(meta)
    return GraphNode(
        repository_id=repo_id,
        node_id=node_id,
        node_type="symbol",
        community_meta_json=blob,
        file_path=node_id.split("::")[0],
        **extra,
    )


async def test_returns_whole_rows_of_the_highest_scores_in_order(async_session):
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            _symbol(repo.id, "src/a.py::main", {"entry_point_score": 0.4}, name="main"),
            _symbol(repo.id, "src/b.py::run", {"entry_point_score": 0.9}, name="run"),
            _symbol(repo.id, "src/c.py::low", {"entry_point_score": 0.1}),
            # No score, an unreadable blob, and a file node are never candidates.
            _symbol(repo.id, "src/d.py::helper", {"community": 3}),
            _symbol(repo.id, "src/e.py::broken", '{"entry_point_score": '),
            GraphNode(
                repository_id=repo.id,
                node_id="src/f.py",
                node_type="file",
                community_meta_json=json.dumps({"entry_point_score": 1.0}),
            ),
        ]
    )
    await async_session.commit()

    top = await get_top_entry_points(async_session, repo.id, min_score=0.3, limit=5)

    assert [n.node_id for n in top] == ["src/b.py::run", "src/a.py::main"]
    # Callers read more than the id: the rows come back whole.
    assert [n.name for n in top] == ["run", "main"]


async def test_limit_and_exclusion_apply_before_the_cut(async_session, tmp_path):
    (tmp_path / ".repowiseignore").write_text("vendor/\n")
    repo = await insert_repo(async_session)
    async_session.add_all(
        [
            _symbol(repo.id, "vendor/x.py::top", {"entry_point_score": 0.99}),
            _symbol(repo.id, "src/a.py::one", {"entry_point_score": 0.8}),
            _symbol(repo.id, "src/b.py::two", {"entry_point_score": 0.7}),
            _symbol(repo.id, "src/c.py::three", {"entry_point_score": 0.6}),
        ]
    )
    await async_session.commit()

    top = await get_top_entry_points(
        async_session,
        repo.id,
        min_score=0.0,
        limit=2,
        exclude_spec=pathspec.PathSpec.from_lines("gitwildmatch", ["vendor/"]),
    )

    assert [n.node_id for n in top] == ["src/a.py::one", "src/b.py::two"]


async def test_no_scored_symbol_returns_empty(async_session):
    repo = await insert_repo(async_session)
    async_session.add(_symbol(repo.id, "src/a.py::f", {}))
    await async_session.commit()

    assert await get_top_entry_points(async_session, repo.id, min_score=0.0) == []
