"""DDL contracts parse through the guarded ``parse_sql``.

A psql meta-command or a statement sqlglot cannot finish must not cost the
tables declared after it.
"""

from __future__ import annotations

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.data.ddl import DdlDialect


def _tables(content: str) -> list[str]:
    ctx = ScanContext("svc", "schema.sql", ".sql", content)
    return [c.contract_id for c in DdlDialect().extract(ctx)]


def test_a_psql_set_line_does_not_swallow_the_next_table() -> None:
    content = "\\set ON_ERROR_STOP on\nCREATE TABLE orders (id INT, total NUMERIC);\n"
    assert _tables(content) == ["data::orders"]


def test_a_table_after_an_over_budget_statement_is_still_found() -> None:
    # Both statements before ``orders`` loop forever in unguarded sqlglot.
    content = (
        "COPY staging FROM :'datafile';\n"
        "CREATE TABLE broken (id INT, flag INTEGER NOT NUL);\n"
        "CREATE TABLE orders (id INT, total NUMERIC);\n"
    )
    assert _tables(content) == ["data::orders"]
