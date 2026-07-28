"""The shared LIKE escaper.

Every hand-built ``like``/``ilike`` in the tree pairs :func:`escape_like` with
``escape=LIKE_ESCAPE``. The pairing is the whole point: SQLite declares no
default escape character, so escaping without passing ``escape=`` does
nothing at all, which is how the first version of this shipped.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import GraphNode
from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like
from tests.unit.persistence.helpers import insert_repo


def test_the_two_like_wildcards_are_escaped() -> None:
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("50%") == "50\\%"


def test_the_escape_character_itself_is_escaped() -> None:
    """Otherwise a value ending in a backslash escapes the pattern's own ``%``."""
    assert escape_like("a\\b") == "a\\\\b"
    assert escape_like("C:\\src\\main.py") == "C:\\\\src\\\\main.py"
    # Order matters: escaping the backslash last would double the ones this
    # function just added.
    assert escape_like("a\\_b") == "a\\\\\\_b"


def test_a_value_with_no_metacharacters_is_untouched() -> None:
    assert escape_like("src/main.py") == "src/main.py"
    assert escape_like("") == ""


@pytest.mark.parametrize(
    ("stored", "decoy", "needle"),
    [
        ("src/my_module.py", "src/myXmodule.py", "src/my_module.py"),
        ("docs/50%.md", "docs/50AB.md", "docs/50%.md"),
        ("src/a\\b.py", "src/aXb.py", "src/a\\b.py"),
    ],
)
async def test_an_escaped_pattern_matches_only_itself(async_session, stored, decoy, needle):
    """The behaviour the escaping exists for, driven through a real query."""
    repo = await insert_repo(async_session)
    for index, node_id in enumerate((stored, decoy)):
        async_session.add(
            GraphNode(
                id=f"gn{index}",
                repository_id=repo.id,
                node_id=node_id,
                node_type="file",
                language="python",
            )
        )
    await async_session.commit()

    rows = await async_session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo.id,
            GraphNode.node_id.like(f"%{escape_like(needle)}%", escape=LIKE_ESCAPE),
        )
    )
    assert [row[0] for row in rows.all()] == [stored]
