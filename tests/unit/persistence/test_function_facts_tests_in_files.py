"""Statement-shape test for ``tests_in_files``.

``tests_in_files`` joins ``graph_nodes`` on ``repository_id`` and ``node_id`` and
filters on ``graph_nodes.file_path``. The repository equality is carried on the
joined side as well as in the join's ``ON`` clause.

That second predicate is redundant: both equalities merge into one equivalence
class, so the planner derives ``graph_nodes.repository_id`` either way. This test
pins its presence as an explicit guarantee rather than as a plan fix — the
guarantee being that the row restriction does not depend on the join staying an
inner one.

It asserts the compiled statement, not a query plan. The plan depends on a
populated Postgres instance and cannot be measured from a unit test.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

# Imported under an alias: pytest would otherwise collect the function itself.
from repowise.core.persistence.crud.analysis.function_facts import (
    tests_in_files as fetch_tests_in_files,
)

# A comparison against a bound parameter, whatever SQLAlchemy names it.
_BOUND_PARAM = re.compile(r"graph_nodes\.repository_id = [%:]")


class _EmptyResult:
    """Stands in for the ``Result`` of a statement that matched no rows."""

    def all(self) -> list[Any]:
        return []


class _RecordingSession:
    """Records every statement a caller executes, and matches nothing."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()


def _where_clause(statement: Any) -> str:
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )
    assert "WHERE" in sql, sql
    return sql.split("WHERE", 1)[1]


@pytest.mark.asyncio
async def test_the_repository_predicate_is_carried_on_the_joined_side() -> None:
    session = _RecordingSession()

    await fetch_tests_in_files(session, "repo-1", ["src/a.py", "src/b.py"])

    assert len(session.statements) == 1
    assert _BOUND_PARAM.search(_where_clause(session.statements[0]))
