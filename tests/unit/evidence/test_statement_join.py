"""The statement-site join: a normalized statement's shape, and the one function that issues it."""

from __future__ import annotations

import pytest

from repowise.core.evidence.statement_join import (
    Columns,
    StatementShape,
    StatementSite,
    match_statement,
    statement_shape,
)

# pg_stat_statements text as PostgREST sends it (values already $n).
_RPC = (
    'WITH pgrst_source AS (SELECT "pgrst_call".* FROM (SELECT $1 AS json_data) pgrst_payload, '
    'LATERAL (SELECT "p_search", "p_limit" FROM json_to_record(pgrst_payload.json_data) AS '
    '_("p_search" text, "p_limit" integer) LIMIT $2) pgrst_body , LATERAL '
    '"public"."search_repos"("p_search" := pgrst_body."p_search", "p_limit" := '
    'pgrst_body."p_limit") pgrst_call) SELECT $3::bigint AS total_result_set'
)
_INSERT = (
    'WITH pgrst_source AS (INSERT INTO "public"."events"("event", "user_id") SELECT '
    '"pgrst_body"."event", "pgrst_body"."user_id" FROM (SELECT $1 AS json_data) pgrst_payload, '
    'LATERAL (SELECT "event", "user_id" FROM json_to_record(pgrst_payload.json_data) AS '
    '_("event" text, "user_id" uuid) ) pgrst_body RETURNING $2) SELECT $3'
)
_DELETE = (
    'WITH pgrst_source AS (DELETE FROM "public"."pages" WHERE "public"."pages"."snapshot_id" = $1 '
    'AND "public"."pages"."path" <= $2 RETURNING $3) SELECT $4 AS total_result_set'
)
_SELECT = (
    'WITH pgrst_source AS ( SELECT "public"."snapshots"."short_id" FROM "public"."snapshots" '
    'WHERE "public"."snapshots"."repo_id" = $1 AND "public"."snapshots"."reaped_at" IS NULL '
    'ORDER BY "public"."snapshots"."completed_at" DESC LIMIT $2 OFFSET $3 ) SELECT $4'
)


@pytest.mark.parametrize(
    ("text", "shape"),
    [
        (
            _RPC,
            StatementShape(
                "postgrest", "call", "search_repos", "public", frozenset({"p_search", "p_limit"})
            ),
        ),
        (
            _INSERT,
            StatementShape(
                "postgrest", "insert", "events", "public", frozenset({"event", "user_id"})
            ),
        ),
        (
            _DELETE,
            StatementShape(
                "postgrest", "delete", "pages", "public", None, frozenset({"snapshot_id", "path"})
            ),
        ),
        (
            _SELECT,
            StatementShape(
                "postgrest",
                "select",
                "snapshots",
                "public",
                frozenset({"short_id"}),
                frozenset({"repo_id", "reaped_at"}),
            ),
        ),
        (
            'WITH pgrst_source AS (SELECT "pgrst_call".* FROM "public"."bands"() pgrst_call) SELECT $1',
            StatementShape("postgrest", "call", "bands", "public", frozenset()),
        ),
        (
            "select public.refresh_summary()",
            StatementShape("sql", "call", "refresh_summary", "public"),
        ),
        (
            "SELECT users.id, users.name FROM users WHERE users.id = $1",
            StatementShape(
                "sql", "select", "users", None, frozenset({"id", "name"}), frozenset({"id"})
            ),
        ),
        (
            "update accounts set plan = $1, seats = $2 where id = $3",
            StatementShape(
                "sql", "update", "accounts", None, frozenset({"plan", "seats"}), frozenset({"id"})
            ),
        ),
        (
            "INSERT INTO t (a, b) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            StatementShape("sql", "insert", "t", None, frozenset({"a", "b"})),
        ),
    ],
)
def test_a_statement_reads_as_its_verb_table_columns_and_filters(text, shape) -> None:
    assert statement_shape(text) == shape


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "SET statement_timeout = ?",
        "WITH x AS (SELECT 1) SELECT * FROM x",  # a CTE that is not PostgREST's
        "SELECT count(*) FROM (SELECT 1 FROM a) s",
        # Cut short before the function's name: nothing to join, never a guess.
        'WITH pgrst_source AS (SELECT "pgrst_call".* FROM (SELECT $1 AS json_data) pgrst_payload, LA',
    ],
)
def test_a_statement_this_cannot_read_has_no_shape(text) -> None:
    assert statement_shape(text) is None


def test_a_cut_statement_says_nothing_of_its_filters() -> None:
    cut = statement_shape(
        'WITH pgrst_source AS (DELETE FROM "public"."pages" WHERE "public"."pages"."a" = $1 AND'
    )
    assert cut is not None and (cut.verb, cut.name, cut.filters) == ("delete", "pages", None)
    # A column list the cut leaves open is unknown, not short.
    insert = statement_shape('INSERT INTO "public"."events"("event", "user_')
    assert insert is not None and insert.columns is None


def _site(
    symbol: str | None,
    *,
    name: str | None = "events",
    verb: str | None = "insert",
    channel: str = "postgrest",
    target: str = "table",
    via: str = "literal",
    columns: Columns | None = None,
    filters: Columns | None = None,
) -> StatementSite:
    return StatementSite(
        file_path="app/x.py",
        line=1,
        channel=channel,
        target=target,
        name=name,
        verb=verb,
        via=via,
        symbol_id=symbol,
        columns=columns,
        filters=filters,
    )


def test_one_function_issuing_the_statement_is_named() -> None:
    shape = statement_shape(_INSERT)
    sites = [
        _site("app/x.py::record", columns=Columns(frozenset({"event", "user_id"}))),
        _site("app/x.py::record"),  # the same function again: still one
        _site("app/y.py::read", verb="select"),
        _site("app/z.py::other", name="other_table"),
    ]
    match = match_statement(shape, sites)
    assert (match.issuer, match.symbol_id) == ("function", "app/x.py::record")


def test_two_functions_that_could_issue_it_name_none() -> None:
    shape = statement_shape(_INSERT)
    match = match_statement(shape, [_site("a.py::one"), _site("b.py::two")])
    assert (match.issuer, match.symbol_id, match.reason) == (
        "ambiguous",
        None,
        "ambiguous_statement",
    )


def test_columns_and_filters_that_contradict_the_statement_rule_a_site_out() -> None:
    shape = statement_shape(_INSERT)
    sites = [
        _site("a.py::write_events", columns=Columns(frozenset({"event", "user_id"}))),
        # Always sends a column the statement lacks.
        _site("b.py::write_audit", columns=Columns(frozenset({"event", "actor"}), closed=False)),
        # Sends only these, and the statement has one more.
        _site("c.py::write_bare", columns=Columns(frozenset({"event"}))),
    ]
    assert match_statement(shape, sites).symbol_id == "a.py::write_events"

    delete = statement_shape(_DELETE)
    drains = [
        _site(
            "a.py::drain",
            name="pages",
            verb="delete",
            filters=Columns(frozenset({"snapshot_id"}), closed=False),
        ),
        _site(
            "b.py::purge", name="pages", verb="delete", filters=Columns(frozenset({"snapshot_id"}))
        ),
    ]
    assert match_statement(delete, drains).symbol_id == "a.py::drain"


def test_a_site_whose_table_is_only_known_at_run_time_keeps_the_join_open() -> None:
    shape = statement_shape(_INSERT)
    runtime = _site("h.py::save_any", name=None, via="runtime")
    match = match_statement(shape, [_site("a.py::write"), runtime])
    assert match.issuer == "ambiguous"
    # Alone it names nothing.
    assert match_statement(shape, [runtime]).issuer is None


def test_postgrest_text_never_joins_a_literal_sql_site_and_back() -> None:
    sql_site = _site("a.py::raw", channel="sql")
    assert match_statement(statement_shape(_INSERT), [sql_site]).issuer is None
    plain = statement_shape("insert into events (event, user_id) values ($1, $2)")
    assert match_statement(plain, [_site("b.py::client")]).issuer is None
    assert match_statement(plain, [sql_site]).symbol_id == "a.py::raw"


def test_a_statement_only_a_database_schedule_runs_says_so() -> None:
    shape = statement_shape("select public.refresh_summary()")
    cron = _site(
        None, name="refresh_summary", verb="call", channel="sql", target="rpc", via="schedule"
    )
    match = match_statement(shape, [cron])
    assert (match.issuer, match.symbol_id, match.reason) == (
        "database_schedule",
        None,
        "database_schedule",
    )
    # A function that runs the same SQL too: no single answer.
    code = _site(
        "a.py::refresh", name="refresh_summary", verb="call", channel="sql", target="rpc", via="sql"
    )
    assert match_statement(shape, [cron, code]).issuer == "ambiguous"
    # The client's call is PostgREST text: the schedule's plain text is not it.
    client = _site("b.py::refresh", name="refresh_summary", verb="call", target="rpc")
    assert match_statement(shape, [cron, client]).issuer == "database_schedule"


def test_an_rpc_with_optional_arguments_matches_each_call_shape() -> None:
    site = _site(
        "app/explore.py::explore",
        name="search_repos",
        verb="call",
        target="rpc",
        columns=Columns(frozenset({"p_search"}), frozenset({"p_limit", "p_min"})),
    )
    assert match_statement(statement_shape(_RPC), [site]).symbol_id == "app/explore.py::explore"
    narrow = Columns(frozenset({"p_search"}))
    assert (
        match_statement(
            statement_shape(_RPC),
            [_site("x", name="search_repos", verb="call", target="rpc", columns=narrow)],
        ).issuer
        is None
    )


def test_columns_round_trip_through_json() -> None:
    columns = Columns(frozenset({"a"}), frozenset({"b"}), closed=False)
    assert Columns.from_json(columns.as_json()) == columns
    assert Columns.from_json(None) is None
