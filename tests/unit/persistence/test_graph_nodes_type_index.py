"""``graph_nodes`` must be seekable by ``(repository_id, node_type)``.

``node_type == "file"`` is the most-issued predicate on the table, and the only
index covering the pair was ``uq_graph_node``'s, keyed ``(repository_id,
node_id)`` — so every "all the file nodes" read seeked on the repo and filtered
the rest in memory. On the repowise index that is 36,480 rows scanned to return
3,449.

Declared on the model *and* shipped as Alembic 0051. Local stores come from
``init_db`` and never run Alembic; hosted only ever runs Alembic. This pins the
model half, which is what ``init_db`` builds from.

Also covers the ``paths=`` scoping ``get_test_file_paths`` grew for it, since
that read is the reason the index was measured in the first place.
"""

from __future__ import annotations

from sqlalchemy import text

from repowise.core.persistence.crud import get_test_file_paths
from repowise.core.persistence.models import GraphNode
from tests.unit.persistence.helpers import insert_repo

_INDEX_NAME = "ix_graph_nodes_repo_type"


def test_index_is_declared_on_the_model() -> None:
    by_name = {ix.name: ix for ix in GraphNode.__table__.indexes}
    assert _INDEX_NAME in by_name, "init_db-created stores would lack the index"
    assert [c.name for c in by_name[_INDEX_NAME].columns] == ["repository_id", "node_type"]


async def test_a_file_node_read_seeks_instead_of_scanning(async_session) -> None:
    """The plan, not the timing — a wall-clock assert would be flaky in CI."""
    plan = (
        await async_session.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT node_id FROM graph_nodes "
                "WHERE repository_id = :r AND node_type = 'file'"
            ),
            {"r": "repo"},
        )
    ).all()

    detail = " ".join(str(row[-1]) for row in plan)
    assert _INDEX_NAME in detail, f"expected a seek on {_INDEX_NAME}, got: {detail}"
    assert "node_type" in detail, f"index used but not keyed on node_type: {detail}"


async def _seed_nodes(session) -> str:
    repo = await insert_repo(session)
    session.add_all(
        [
            GraphNode(repository_id=repo.id, node_id="a_test.py", node_type="file", is_test=True),
            GraphNode(repository_id=repo.id, node_id="b_test.py", node_type="file", is_test=True),
            GraphNode(repository_id=repo.id, node_id="prod.py", node_type="file", is_test=False),
            # A symbol node whose composite id embeds a test path — the
            # ``node_type`` filter is what keeps it out, not the path shape.
            GraphNode(
                repository_id=repo.id,
                node_id="a_test.py::helper",
                node_type="symbol",
                is_test=True,
            ),
        ]
    )
    await session.commit()
    return repo.id


async def test_paths_narrows_the_answer_to_the_asked_about_files(async_session) -> None:
    repo_id = await _seed_nodes(async_session)

    assert await get_test_file_paths(async_session, repo_id) == {"a_test.py", "b_test.py"}
    # Scoped: only what was asked about, and a non-test path in the ask is
    # simply absent rather than an error.
    assert await get_test_file_paths(async_session, repo_id, ["a_test.py", "prod.py"]) == {
        "a_test.py"
    }
    assert await get_test_file_paths(async_session, repo_id, ["prod.py"]) == set()


async def test_empty_paths_is_not_the_same_as_none(async_session) -> None:
    """``[]`` means "no files asked about" and must not fall back to repo-wide.

    ``get_health`` passes the resolved target list straight through, and a
    targets call that resolved nothing passes ``[]``. Treating that as ``None``
    would answer a scoped question with the whole repo.
    """
    repo_id = await _seed_nodes(async_session)

    assert await get_test_file_paths(async_session, repo_id, []) == set()
    assert await get_test_file_paths(async_session, repo_id, None) != set()


async def test_scoping_survives_more_paths_than_one_statement_can_bind(async_session) -> None:
    """A ``module:`` target expands to every file in the module, which on a
    monorepo exceeds SQLite's bind-parameter limit for a single ``IN``."""
    repo_id = await _seed_nodes(async_session)
    padding = [f"pad/{i}.py" for i in range(3000)]

    assert await get_test_file_paths(async_session, repo_id, [*padding, "b_test.py"]) == {
        "b_test.py"
    }
