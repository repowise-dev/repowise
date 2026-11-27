"""Shapes of code that once let the statement-site join name a wrong function.

Each case has a second function that issues the same statement in a way the
index reads less well; the join must name neither (or the right one), never
only the one it read best.
"""

from __future__ import annotations

from repowise.core.evidence.statement_join import match_statement, statement_shape
from repowise.core.workspace.statement_sites import (
    attach_functions,
    read_python_sites,
    read_schedule_sites,
)


def _issuer(texts: dict[str, str], statement: str) -> tuple[str | None, str | None]:
    sites = read_python_sites(texts)
    spans: dict[str, list[tuple[int, int, str]]] = {}
    for path, text in texts.items():
        lines = text.splitlines()
        starts = [i + 1 for i, line in enumerate(lines) if line.startswith(("def ", "async def "))]
        for i, start in enumerate(starts):
            end = (starts[i + 1] - 1) if i + 1 < len(starts) else len(lines)
            name = lines[start - 1].split("def ", 1)[1].split("(", 1)[0]
            spans.setdefault(path, []).append((start, end, f"{path}::{name}"))
    match = match_statement(statement_shape(statement), attach_functions(sites, spans))
    return match.issuer, match.symbol_id


_INSERT_T = 'WITH pgrst_source AS (INSERT INTO "public"."t"("a") SELECT 1) SELECT $1'


def test_a_helper_reached_through_a_package_re_export_keeps_its_callers_open() -> None:
    texts = {
        "pkg/__init__.py": "from pkg.sub import helper\n",
        "pkg/sub.py": "def helper(c, table):\n    c.table(table).insert({'a': 1}).execute()\n",
        "app/a.py": "from pkg import helper\n\n\ndef a(c):\n    helper(c, 't')\n",
        "app/b.py": "from pkg.sub import helper\n\n\ndef b(c):\n    helper(c, 't')\n",
    }
    assert _issuer(texts, _INSERT_T)[0] == "ambiguous"


def test_an_argument_spread_from_kwargs_is_not_the_default() -> None:
    texts = {
        "app/x.py": (
            "def helper(c, table='a'):\n    c.table(table).insert({'a': 1}).execute()\n\n\n"
            "def f1(c):\n    helper(c, table='t')\n\n\n"
            "def f2(c, cfg):\n    helper(c, **cfg)\n"
        )
    }
    assert _issuer(texts, _INSERT_T)[0] == "ambiguous"


def test_a_parameter_given_a_local_fallback_is_not_one_literal() -> None:
    texts = {
        "app/x.py": (
            "def f(c, table):\n    if not table:\n        table = 'a'\n"
            "    c.table(table).insert({'a': 1}).execute()\n\n\n"
            "def g(c):\n    f(c, 't')\n\n\n"
            "def h(c):\n    c.table('t').insert({'a': 1}).execute()\n"
        )
    }
    assert _issuer(texts, _INSERT_T)[0] == "ambiguous"


def test_quoted_mixed_case_columns_still_match_their_site() -> None:
    texts = {
        "app/x.py": (
            "def write(c):\n    c.table('t').insert({'userId': 1}).execute()\n\n\n"
            "def write_any(c, row):\n    c.table('t').insert(row).execute()\n"
        )
    }
    statement = 'WITH pgrst_source AS (INSERT INTO "public"."t"("userId") SELECT 1) SELECT $1'
    assert _issuer(texts, statement)[0] == "ambiguous"


def test_one_builder_issuing_two_verbs_is_a_candidate_for_both() -> None:
    texts = {
        "app/x.py": (
            "def both(c):\n    t = c.table('t')\n    t.insert({'a': 1}).execute()\n"
            "    t.delete().eq('id', 1).execute()\n\n\n"
            "def purge(c):\n    c.table('t').delete().eq('id', 1).execute()\n"
        )
    }
    delete = (
        'WITH pgrst_source AS (DELETE FROM "public"."t" WHERE "public"."t"."id" = $1) SELECT $2'
    )
    assert _issuer(texts, delete)[0] == "ambiguous"


def test_a_constant_bound_in_both_branches_has_no_single_value() -> None:
    texts = {
        "app/x.py": (
            "import os\nif os.environ.get('X'):\n    T = 't'\nelse:\n    T = 'b'\n\n\n"
            "def a(c):\n    c.table(T).insert({'a': 1}).execute()\n\n\n"
            "def b(c):\n    c.table('t').insert({'a': 1}).execute()\n"
        )
    }
    assert _issuer(texts, _INSERT_T)[0] == "ambiguous"


def test_sql_in_an_imported_constant_is_a_site_where_it_is_read() -> None:
    texts = {
        "app/q.py": 'SQL = "DELETE FROM sessions WHERE id = %s"\n',
        "app/a.py": "from app.q import SQL\n\n\ndef a(cur):\n    cur.execute(SQL)\n",
        "app/c.py": 'def c(cur):\n    cur.execute("DELETE FROM sessions WHERE id = %s")\n',
    }
    assert _issuer(texts, "DELETE FROM sessions WHERE id = $1")[0] == "ambiguous"


def test_an_orm_query_that_deletes_is_a_delete_site() -> None:
    texts = {
        "app/m.py": "class U(Base):\n    __tablename__ = 'users'\n",
        "app/x.py": (
            "from app.m import U\nfrom sqlalchemy import delete\n\n\n"
            "def a(session):\n    session.query(U).filter(U.id == 1).delete()\n\n\n"
            "def b(session):\n    session.execute(delete(U))\n"
        ),
    }
    assert _issuer(texts, "DELETE FROM users WHERE users.id = $1")[0] == "ambiguous"


def test_a_session_add_of_a_variable_keeps_inserts_open() -> None:
    texts = {
        "app/m.py": "class U(Base):\n    __tablename__ = 'users'\n",
        "app/x.py": (
            "from app.m import U\n\n\n"
            "def a(session, u):\n    session.add(u)\n\n\n"
            "def b(session):\n    session.add(U(name='x'))\n"
        ),
    }
    assert _issuer(texts, "INSERT INTO users (name) VALUES ($1)")[0] == "ambiguous"


def test_a_helper_handed_on_by_reference_keeps_its_callers_open() -> None:
    texts = {
        "app/x.py": (
            "def helper(c, table):\n    c.table(table).insert({'a': 1}).execute()\n\n\n"
            "def a(c):\n    helper(c, 't')\n\n\n"
            "def b(pool, c):\n    pool.submit(helper, c, 'other')\n"
        )
    }
    assert _issuer(texts, _INSERT_T)[0] == "ambiguous"


def test_an_embedded_select_joins_on_more_columns_than_it_filters() -> None:
    texts = {
        "app/x.py": (
            "def a(c):\n    c.table('t').select('id, o(*)').eq('id', 1).execute()\n\n\n"
            "def b(c, col):\n    c.table('t').select('id').eq(col, 1).execute()\n"
        )
    }
    statement = (
        'WITH pgrst_source AS ( SELECT "public"."t"."id" FROM "public"."t" '
        'WHERE "public"."t"."id" = $1 AND "public"."t"."t_id" = $2 ) SELECT $3'
    )
    assert _issuer(texts, statement)[0] == "ambiguous"


def test_a_dollar_quoted_cron_job_and_its_filters_are_read() -> None:
    sql = "select cron.schedule('j', '0 * * * *', $$DELETE FROM t WHERE expires < now()$$);\n"
    (site,) = read_schedule_sites("m.sql", sql)
    assert (site.name, site.verb) == ("t", "delete")
    other = statement_shape("DELETE FROM t WHERE id = $1")
    assert match_statement(other, [site]).issuer is None
    own = statement_shape("DELETE FROM t WHERE expires < now()")
    assert match_statement(own, [site]).issuer == "database_schedule"


def test_a_from_inside_a_function_call_is_not_the_table() -> None:
    shape = statement_shape("SELECT extract(year FROM created_at) FROM orders WHERE id = $1")
    assert shape is not None and shape.name == "orders"


def test_an_add_on_any_receiver_in_an_orm_file_keeps_inserts_open() -> None:
    texts = {
        "app/m.py": "class U(Base):\n    __tablename__ = 'users'\n",
        "app/x.py": (
            "from sqlalchemy.orm import Session\n\n\ndef a(db: Session, u):\n    db.add(u)\n"
        ),
        "app/y.py": ("from app.m import U\n\n\ndef b(db):\n    db.add(U(name='x'))\n"),
    }
    assert _issuer(texts, "INSERT INTO users (name) VALUES ($1)")[0] == "ambiguous"


def test_a_cron_job_built_at_run_time_keeps_plain_sql_open() -> None:
    sql = (
        "select cron.schedule('purge', '0 3 * * *', format('delete from %I', tbl));\n"
        "select cron.schedule('nightly', '0 4 * * *', v_cmd);\n"
    )
    sites = read_schedule_sites("m.sql", sql)
    assert {(s.name, s.via) for s in sites} == {(None, "schedule")}
    statement = statement_shape("DELETE FROM sessions WHERE id = $1")
    code = read_python_sites(
        {"app/x.py": 'def purge(cur):\n    cur.execute("DELETE FROM sessions WHERE id = %s")\n'}
    )
    assert match_statement(statement, [*code, *sites]).issuer == "ambiguous"
    # A job the text does not spell never claims a statement on its own.
    assert match_statement(statement, sites).issuer is None
