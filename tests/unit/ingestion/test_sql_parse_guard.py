"""sqlglot must never hang a parse: the guarded ``parse_sql`` entry point.

sqlglot loops forever, allocating as it goes, on some statements it cannot
consume (psql ``COPY ... FROM :'var'``, a ``NOT NUL`` constraint typo). The
postgres regression suite is full of them. Every parse here runs in a child
process with a timeout, so a regression fails the test instead of hanging the
suite.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

_TIMEOUT_S = 60

# Excerpt of postgres src/test/regress/sql/copy.sql (PostgreSQL License), which
# hung the parser outright, plus the constraint typo from errors.sql.
_REGRESS_EXCERPT = r"""--
-- COPY
--

-- directory paths are passed to us in environment variables
\getenv abs_srcdir PG_ABS_SRCDIR
\getenv abs_builddir PG_ABS_BUILDDIR

create temp table copytest (
	style	text,
	test 	text,
	filler	int);

insert into copytest values('DOS',E'abc\r\ndef',1);
insert into copytest values(E'esc\\ape',E'a\\r\\\r\\\n\\nb',4);

\set filename :abs_builddir '/results/copytest.csv'
copy copytest to :'filename' csv;

create table copytest2 (style text, test text, filler int);

copy copytest2 from :'filename' csv quote '''' escape E'\\';

-- with a tab
CREATE TABLE foo
  (id INT4 UNIQUE NOT NULL, id2 TEXT NOT NULL PRIMARY KEY,
	id3 INTEGER NOT NUL,
   id4 INT4 UNIQUE NOT NULL, id5 TEXT UNIQUE NOT NULL);

CREATE VIEW after_the_typo AS SELECT style FROM copytest2;
"""

_SCRIPT = r"""
import json, sys
from datetime import datetime
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.special_handlers import parse_special

source = sys.stdin.read()
fi = FileInfo(path="copy.sql", abs_path="/repo/copy.sql", language="sql", size_bytes=len(source),
              git_hash="", last_modified=datetime.now(), is_test=False, is_config=False,
              is_api_contract=False, is_entry_point=False)
parsed = parse_special(fi, source.encode(), "sql")
print(json.dumps({"symbols": [[s.name, s.start_line] for s in parsed.symbols],
                  "errors": parsed.parse_errors}))
"""


def _parse_in_child(source: str) -> dict:
    try:
        done = subprocess.run(
            [sys.executable, "-c", _SCRIPT],
            input=source,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"SQL parse did not finish within {_TIMEOUT_S}s")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "source",
    [
        "COPY t FROM :'f';",
        "COPY t TO :'f';",
        "COPY t FROM -;",
        "COPY (SELECT 1) TO :'f';",
        "CREATE TABLE foo (id INT4, id3 INTEGER NOT NUL, id4 INT4);",
    ],
)
def test_statements_that_hang_sqlglot_finish(source: str) -> None:
    assert _parse_in_child(source) == {"symbols": [], "errors": []}


def test_regress_excerpt_keeps_every_other_symbol() -> None:
    result = _parse_in_child(_REGRESS_EXCERPT)
    assert result["errors"] == []
    # The hanging COPY statements and the ``NOT NUL`` table are dropped; the
    # statements after them still parse, with their own line numbers.
    assert result["symbols"] == [
        ["copytest", 9],
        ["copytest2", 20],
        ["after_the_typo", 30],
    ]


def test_meta_command_does_not_swallow_the_next_statement() -> None:
    from repowise.core.ingestion.sql_parse import parse_sql

    statements = parse_sql("\\set x 1\nCREATE TABLE after_meta (b int);")
    assert [s.this.this.name for s in statements if s is not None] == ["after_meta"]


def test_backslash_inside_a_string_is_not_a_meta_command() -> None:
    from repowise.core.ingestion.sql_parse import parse_sql

    statements = parse_sql("INSERT INTO t VALUES ('a\\b');\nCREATE TABLE kept (a int);")
    assert [type(s).__name__ for s in statements] == ["Insert", "Create"]


def test_gset_ends_the_statement_like_a_semicolon() -> None:
    from repowise.core.ingestion.sql_parse import parse_sql

    statements = parse_sql("SELECT 1 AS x \\gset\nCREATE TABLE kept (a int);")
    assert [type(s).__name__ for s in statements] == ["Select", "Create"]


def test_embedded_sql_keeps_a_stray_backslash_an_error() -> None:
    import sqlglot

    from repowise.core.ingestion.sql_parse import parse_sql

    with pytest.raises(sqlglot.errors.ParseError):
        parse_sql(
            "DELETE FROM users \\ WHERE 1",
            error_level=sqlglot.ErrorLevel.RAISE,
            psql_meta_commands=False,
        )
