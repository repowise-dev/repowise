"""Production statements and site-less query counts join through the stored statement sites."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.evidence import EVIDENCE_FORMAT, parse_evidence
from repowise.core.evidence.ingest import entry_paths, join_entries
from repowise.core.evidence.statement_join import Columns, StatementSite
from repowise.core.persistence import create_session_factory, get_session
from repowise.core.persistence.crud import replace_statement_sites
from repowise.core.persistence.database import create_engine
from repowise.core.persistence.models import EvidenceStatementStats
from tests.unit.cli.test_evidence_cmd import _git_repo, _index
from tests.unit.evidence.test_add import _ingest

_RPC = (
    'WITH pgrst_source AS (SELECT "pgrst_call".* FROM (SELECT $1 AS json_data) pgrst_payload, '
    'LATERAL (SELECT "p_search" FROM json_to_record(pgrst_payload.json_data) AS _("p_search" text) '
    'LIMIT $2) pgrst_body , LATERAL "public"."search_repos"("p_search" := pgrst_body."p_search") '
    "pgrst_call) SELECT $3::bigint AS total_result_set, pg_catalog.count(_postgrest_t) AS page_total"
)

SITES = [
    StatementSite(
        "src/app.py", 1, "postgrest", "rpc", "search_repos", "call", "literal",
        symbol_id="src/app.py::build", columns=Columns(frozenset({"p_search"})),
    ),
    StatementSite("src/edited.py", 1, "postgrest", "table", "events", "insert", "literal", symbol_id="src/edited.py::f"),
    StatementSite("src/app.py", 2, "postgrest", "table", "events", "insert", "literal", symbol_id="src/app.py::build"),
    StatementSite("db/cron.sql", 3, "sql", "rpc", "refresh_summary", "call", "schedule"),
]  # fmt: skip


def _statements() -> str:
    return json.dumps(
        {
            "format": EVIDENCE_FORMAT,
            "kind": "statement_stats",
            "statements": [
                {"statement_fp": "1" * 16, "query": _RPC, "calls": 50, "total_ms": 900.0},
                {"statement_fp": "2" * 16, "query": "select public.refresh_summary()", "calls": 2},
                {
                    "statement_fp": "3" * 16,
                    "query": 'INSERT INTO "public"."events"("a") VALUES ($1)',
                    "calls": 9,
                },
                {"statement_fp": "4" * 16, "query": "select 1", "calls": 1},
            ],
        }
    )


def test_each_statement_names_its_function_or_says_why_not() -> None:
    doc = parse_evidence(_statements())
    joined = join_entries(doc, {}, {}, frozenset(), SITES)
    by_fp = {r["statement_fp"]: r for r in joined.rows}
    # The function name sits past the 200 characters stored; the join reads it all.
    assert (
        len(by_fp["1" * 16]["query_text"]) == 200
        and "search_repos" not in by_fp["1" * 16]["query_text"]
    )
    assert (by_fp["1" * 16]["symbol_id"], by_fp["1" * 16]["issuer"]) == (
        "src/app.py::build",
        "function",
    )
    assert (by_fp["2" * 16]["symbol_id"], by_fp["2" * 16]["issuer"]) == (None, "database_schedule")
    # Plain SQL text: neither client call issued it.
    assert (by_fp["3" * 16]["symbol_id"], by_fp["3" * 16]["issuer"]) == (None, None)
    assert joined.matched == 4 and not joined.skipped
    assert joined.unlinked == {"database_schedule": 1, "no_statement_site": 2}
    assert "shape_text" not in by_fp["1" * 16]


def _queries(*entries: dict) -> str:
    return json.dumps(
        {
            "format": EVIDENCE_FORMAT,
            "kind": "query_count",
            "commit_sha": "a" * 40,
            "queries": list(entries),
        }
    )


def test_a_query_count_with_no_call_site_takes_the_one_function_that_issues_it() -> None:
    doc = parse_evidence(
        _queries(
            {
                "scope": {"kind": "test", "id": "t1", "items": 10},
                "query": _RPC,
                "statement_fp": "0123456789abcdef",
                "count": 11,
            },
            {
                "scope": {"kind": "test", "id": "t2"},
                "statement_fp": "0123456789abcdef",
                "count": 3,
                "query": 'WITH pgrst_source AS (INSERT INTO "public"."events"("a") SELECT 1) SELECT $1',
            },
            {"scope": {"kind": "test", "id": "t3"}, "statement_fp": "0123456789abcdef", "count": 3},
        )
    )
    # No site and no text: nothing to join, as before.
    assert doc.skipped == {"no_path": 1}
    assert entry_paths(doc) == set()
    joined = join_entries(doc, {}, {}, frozenset(), SITES)
    assert [(r["scope_id"], r["site_symbol_id"], r["site_line"]) for r in joined.rows] == [
        ("t1", "src/app.py::build", 1)
    ]
    # Two functions insert into events: never one picked.
    assert joined.skipped == {"ambiguous_statement": 1}
    # A file that changed since the measured commit is not trusted.
    dirty = join_entries(doc, {}, {}, frozenset({"src/app.py"}), SITES)
    assert dirty.skipped == {"ambiguous_statement": 1, "dirty_file": 1}


@pytest.fixture
def indexed(tmp_path: Path) -> Path:
    _git_repo(tmp_path)
    _index(tmp_path)

    async def store_sites() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                await replace_statement_sites(session, "r1", SITES, "fp")
        finally:
            await engine.dispose()

    asyncio.run(store_sites())
    return tmp_path


def test_evidence_add_stores_the_issuing_function_and_what_the_join_found(indexed) -> None:
    payload = _ingest(indexed, _statements().encode(), workload="prod db", workload_class=None)
    assert payload["stored"] is True

    async def rows() -> list:
        engine = create_engine(f"sqlite+aiosqlite:///{indexed / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                result = await session.execute(select(EvidenceStatementStats))
                return list(result.scalars())
        finally:
            await engine.dispose()

    stored = {r.statement_fp: (r.symbol_id, r.issuer) for r in asyncio.run(rows())}
    assert stored == {
        "1" * 16: ("src/app.py::build", "function"),
        "2" * 16: (None, "database_schedule"),
        "3" * 16: (None, None),
        "4" * 16: (None, None),
    }
