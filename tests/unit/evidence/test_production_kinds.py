"""The kind registry and the production kinds: route latency, statement statistics,
the ``pg_stat_statements`` CSV, and who may store a run as ``production``."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.evidence import EVIDENCE_FORMAT, EvidenceFormatError, parse_evidence
from repowise.core.evidence.add import EvidenceRefusedError
from repowise.core.evidence.format_v1 import STATEMENT_TEXT_MAX, statement_text
from repowise.core.evidence.ingest import (
    EvidenceCaps,
    RunProvenance,
    cap_rows,
    entry_paths,
    join_entries,
    run_class,
    run_fields,
)
from repowise.core.evidence.kinds import (
    RESERVED_KINDS,
    STORE_TABLES,
    evidence_kinds,
    register_kind,
)
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats
from repowise.core.persistence.crud import EVIDENCE_ROW_TABLES
from repowise.core.persistence.models import (
    Base,
    EvidenceRouteLatency,
    EvidenceRun,
    EvidenceStatementStats,
)
from tests.unit.cli.test_evidence_cmd import _indexed_copy
from tests.unit.evidence.test_add import _ingest

FIXTURES = Path(__file__).parent / "fixtures" / "production"


@pytest.fixture
def indexed(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    return tmp_path, _indexed_copy(tmp_path, tmp_path_factory)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- the registry -------------------------------------------------------------


def test_every_kind_names_a_row_table_keyed_by_run() -> None:
    assert set(evidence_kinds) == {
        "profile",
        "benchmark",
        "test_timing",
        "query_count",
        "route_latency",
        "statement_stats",
    }
    for name, kind in evidence_kinds.items():
        table = Base.metadata.tables[kind.table]
        assert table.c.run_id.primary_key, name
        assert EVIDENCE_ROW_TABLES[name] is table
    assert {kind.table for kind in evidence_kinds.values()} == set(STORE_TABLES)


def test_a_kind_cannot_take_a_reserved_name_or_a_table_of_its_own() -> None:
    base = evidence_kinds["statement_stats"]
    with pytest.raises(ValueError, match="reserved"):
        register_kind(replace(base, name="error_event"))
    with pytest.raises(ValueError, match="taken"):
        register_kind(base)
    with pytest.raises(ValueError, match="not an evidence row table"):
        register_kind(replace(base, name="plugin_kind", table="plugin_rows"))
    with pytest.raises(ValueError, match="join"):
        register_kind(replace(base, name="plugin_kind", join="guess"))
    assert "plugin_kind" not in evidence_kinds


@pytest.mark.parametrize("kind", RESERVED_KINDS)
def test_a_reserved_kind_is_refused_as_not_read_yet(kind: str) -> None:
    with pytest.raises(EvidenceFormatError, match="reserved"):
        parse_evidence(json.dumps({"format": EVIDENCE_FORMAT, "kind": kind, "events": []}))


# --- route_latency -------------------------------------------------------------


def test_a_route_latency_file_reads_routes_and_skips_urls_by_reason() -> None:
    doc = parse_evidence(_fixture("route_latency.json").decode())
    assert (doc.kind, doc.value_kind, doc.unit) == ("route_latency", "wall", "ms")
    assert doc.window_start == datetime(2026, 11, 19, tzinfo=UTC)
    assert doc.window_end == datetime(2026, 11, 26, tzinfo=UTC)
    assert (doc.commit_sha, doc.base_commit_sha) == ("c" * 40, "d" * 40)
    assert doc.entries[0] == {
        "method": "GET",
        "route": "/api/repos/:id",
        "count": 48210,
        "p50_ms": 41.5,
        "p95_ms": 380.0,
        "total_ms": 3105000.0,
        "errors": 12,
    }
    assert [e["route"] for e in doc.entries] == ["/api/repos/:id", "/api/repos"]
    # A requested URL (query string, no leading slash) is never a route.
    assert doc.skipped == {
        "not_a_route_template": 2,
        "bad_method": 1,
        "duplicate": 1,
        "no_value": 1,
    }


def test_route_and_statement_rows_keep_no_function_until_their_join_exists() -> None:
    doc = parse_evidence(_fixture("route_latency.json").decode())
    assert entry_paths(doc) == set()
    joined = join_entries(doc, {}, {}, frozenset())
    assert joined.matched == 2 and not joined.skipped and not joined.unlinked
    assert joined.rows[0] == {
        "method": "GET",
        "route": "/api/repos/:id",
        "handler_symbol_id": None,
        "request_count": 48210,
        "p50_ms": 41.5,
        "p95_ms": 380.0,
        "total_ms": 3105000.0,
        "error_count": 12,
    }


def test_a_production_run_records_its_window_and_deploy_range() -> None:
    doc = parse_evidence(_fixture("route_latency.json").decode())
    provenance = RunProvenance("prod", "production", "c" * 40, "/repo", None)
    run = run_fields(
        doc,
        provenance,
        join_entries(doc, {}, {}, frozenset()),
        key="k",
        truncated=0,
        mapping_partial=False,
    )
    assert (run["window_start"], run["window_end"]) == (doc.window_start, doc.window_end)
    assert run["base_commit_sha"] == "d" * 40


@pytest.mark.parametrize(
    ("window", "message"),
    [
        ({"start": "2026-11-19"}, "UTC offset"),
        ({"start": "2026-11-20T00:00:00Z", "end": "2026-11-19T00:00:00Z"}, "after"),
        ("last week", "not an object"),
    ],
)
def test_a_bad_window_refuses_the_document(window, message) -> None:
    text = json.dumps(
        {"format": EVIDENCE_FORMAT, "kind": "route_latency", "routes": [], "window": window}
    )
    with pytest.raises(EvidenceFormatError, match=message):
        parse_evidence(text)


# --- statement_stats -----------------------------------------------------------


def test_a_statement_stats_file_keeps_normalized_text_and_masks_values() -> None:
    doc = parse_evidence(_fixture("statement_stats.json").decode())
    assert doc.kind == "statement_stats"
    first, refresh, update = doc.entries
    assert first == {
        "statement_fp": "a0b1c2d3e4f50617",
        "query": "select * from search_repos($1, $2)",
        # The uncut masked text the statement-site join reads; never stored.
        "shape_text": "select * from search_repos($1, $2)",
        "calls": 50000,
        "total_ms": 31950000.5,
        "mean_ms": 639.0,
        "rows": 1000000,
    }
    assert refresh["query"] == "REFRESH MATERIALIZED VIEW repo_stats"
    # A value a source failed to normalize never reaches the store.
    assert update["query"] == "update users set token = ? where id = ?"
    assert doc.skipped == {"statement_not_digest": 1, "no_value": 1}


@pytest.mark.parametrize(
    ("text", "stored"),
    [
        ("select $1, $12 from t2", "select $1, $12 from t2"),
        ("select 'a''b', E'x', 3.5e2 from t", "select ?, ?, ? from t"),
        ("select $tag$ secret $tag$ /* user=bob */ from t -- note", "select ? from t"),
        ("select 'unterminated", "select ?"),
        # A backslash escapes a quote only in an E string; in a standard one it is text.
        (r"select * from t where a = E'it\'s secret'", "select * from t where a = ?"),
        (
            r"select * from t where path = '\' and owner = 'alice'",
            "select * from t where path = ? and owner = ?",
        ),
        # An apostrophe inside a double-quoted name opens no string.
        (
            """select "user's_id" from t where e = 'a@b.c'""",
            """select "user's_id" from t where e = ?""",
        ),
        ("select 'a -- b', 'c /* d' from t", "select ?, ? from t"),
        ("select .5, 0x1F, 1_000, 5.5.5, -3 from t1", "select ?, ?, ?, ?, -? from t1"),
        ("  \n\t ", None),
        (None, None),
    ],
)
def test_statement_text_masks_every_value(text, stored) -> None:
    assert statement_text(text) == stored


def test_statement_text_is_cut_short() -> None:
    text = statement_text("select " + "a, " * 500 + "b from t")
    assert len(text) == STATEMENT_TEXT_MAX and text.endswith("...")


def test_a_statement_run_cap_keeps_the_costliest_statements() -> None:
    doc = parse_evidence(_fixture("statement_stats.json").decode())
    rows = join_entries(doc, {}, {}, frozenset()).rows
    caps = EvidenceCaps(max_symbols_per_run=1, max_rows_per_run=2, history_runs=1, max_runs=1)
    kept, dropped = cap_rows("statement_stats", list(reversed(rows)), caps)
    assert [r["statement_fp"] for r in kept] == ["a0b1c2d3e4f50617", "0000000000000077"]
    assert dropped == 1


# --- pg_stat_statements CSV ------------------------------------------------------


def test_a_psql_csv_export_is_sniffed_and_folds_to_one_entry_per_statement() -> None:
    data = _fixture("pg_stat_statements.csv")
    assert detect_profile_format(data[:4096]) == "pg-stat-statements"
    v1 = profile_formats.get("pg-stat-statements").to_v1(data)
    doc = parse_evidence(json.dumps(v1))
    assert doc.kind == "statement_stats" and doc.tool_name == "pg_stat_statements"
    by_fp = {e["statement_fp"]: e for e in doc.entries}
    # queryid as 16 hex digits, a negative one in two's complement.
    search = by_fp[f"{-6865005379436118016 & 0xFFFFFFFFFFFFFFFF:016x}"]
    # Two users' rows of one statement add up; the mean is the sum over the calls.
    assert (search["calls"], search["total_ms"], search["rows"]) == (50000, 31950000.5, 1000000)
    assert search["mean_ms"] == pytest.approx(639.0)
    assert by_fp["000000000000004d"]["query"] == "REFRESH MATERIALIZED VIEW CONCURRENTLY repo_stats"
    lookup = by_fp[f"{4211981207742413312:016x}"]
    assert lookup["query"] == "SELECT id, name FROM repositories WHERE owner_id = $1 AND name = ?"
    assert lookup["mean_ms"] == pytest.approx(2.0002)
    assert doc.skipped == {"unreadable_row": 1}
    assert doc.window_start == datetime(2026, 10, 1, 0, 0, 0, 500000, tzinfo=UTC)
    assert doc.window_end is None


def test_a_minimal_export_without_queryid_digests_the_text() -> None:
    data = (
        b"captured_at,query,calls,total_time,rows\n"
        b"2026-11-26 09:00:00+00,select * from t where id = $1,4,10.0,4\n"
    )
    doc = parse_evidence(json.dumps(profile_formats.get("pg-stat-statements").to_v1(data)))
    (entry,) = doc.entries
    assert len(entry["statement_fp"]) == 16
    assert (entry["calls"], entry["total_ms"], entry["mean_ms"]) == (4, 10.0, 2.5)
    assert doc.window_end == datetime(2026, 11, 26, 9, tzinfo=UTC)


def test_a_csv_row_with_no_finite_count_is_skipped_and_a_backwards_window_dropped() -> None:
    data = (
        b"queryid,query,calls,total_exec_time,rows,stats_since,captured_at\n"
        b"1,select $1,NaN,1.0,1,,\n"
        b"2,select $1,2,inf,inf,2026-11-27 00:00:00+00,2026-11-26 00:00:00+00\n"
        b"3,select $2,3,9.0,3,,\n"
        b"3,select $2,1,,1,,\n"
    )
    doc = parse_evidence(json.dumps(profile_formats.get("pg-stat-statements").to_v1(data)))
    by_fp = {e["statement_fp"]: e for e in doc.entries}
    assert doc.skipped == {"unreadable_row": 1}
    assert (by_fp[f"{2:016x}"]["total_ms"], by_fp[f"{2:016x}"]["rows"]) == (None, None)
    # The mean covers only the calls whose time was exported.
    assert (by_fp[f"{3:016x}"]["calls"], by_fp[f"{3:016x}"]["mean_ms"]) == (4, 3.0)
    assert (doc.window_start, doc.window_end) == (None, None)


@pytest.mark.parametrize(
    "route", ["/users/12345/orders", "/users/alice@example.com", "/f/3fa85f64-5717-4562-b3fc"]
)
def test_a_route_with_a_value_in_it_is_not_a_template(route) -> None:
    text = json.dumps(
        {
            "format": EVIDENCE_FORMAT,
            "kind": "route_latency",
            "routes": [{"method": "GET", "route": route, "count": 1}],
        }
    )
    assert parse_evidence(text).skipped == {"not_a_route_template": 1}


def test_a_hidden_statement_row_never_hides_the_text_a_later_row_shows() -> None:
    data = (
        b"queryid,query,calls,total_exec_time\n"
        b"9,<insufficient privilege>,1,1.0\n"
        b"9,select * from t where id = $1,2,2.0\n"
        b",<insufficient privilege>,5,5.0\n"
    )
    doc = parse_evidence(json.dumps(profile_formats.get("pg-stat-statements").to_v1(data)))
    (entry,) = doc.entries
    assert (entry["query"], entry["calls"]) == ("select * from t where id = $1", 3)
    # Neither an id nor readable text: nothing names the statement.
    assert doc.skipped == {"unreadable_row": 1}


def test_a_csv_field_past_the_csv_limit_is_refused_with_its_reason() -> None:
    import csv

    long_query = b"select " + b"a" * (csv.field_size_limit() + 10)
    data = b"queryid,query,calls,total_exec_time\n1," + long_query + b",1,1.0\n"
    with pytest.raises(EvidenceFormatError, match="cannot be read"):
        profile_formats.get("pg-stat-statements").to_v1(data)


def test_a_base_commit_column_holds_a_sha256_commit() -> None:
    assert EvidenceRun.__table__.c.base_commit_sha.type.length == 64


def test_a_csv_that_is_not_an_export_is_not_claimed() -> None:
    assert detect_profile_format(b"name,calls\nfoo,1\n") is None
    with pytest.raises(EvidenceFormatError, match="pg_stat_statements"):
        profile_formats.get("pg-stat-statements").to_v1(b"name,calls\nfoo,1\n")


# --- the production class -----------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "given", "verified", "stored", "refused"),
    [
        ("statement_stats", None, False, "production", None),
        ("route_latency", "production", True, "production", None),
        ("route_latency", "production", False, None, "never from --class"),
        ("route_latency", "load_test", False, None, "production by what it measures"),
        ("profile", "production", False, None, "never from --class"),
        ("profile", "production", True, "production", None),
        ("profile", None, False, None, "name the workload class"),
        ("benchmark", "production", True, None, "does not apply"),
        ("test_timing", "unit_test", False, "unit_test", None),
    ],
)
def test_who_may_store_a_run_as_production(kind, given, verified, stored, refused) -> None:
    got, why = run_class(kind, given, verified=verified)
    assert got == stored
    assert (why is None) if refused is None else (refused in why)


def _stored_production(root: Path) -> tuple[list, list, list]:
    import asyncio

    from repowise.core.persistence import create_session_factory, get_session
    from repowise.core.persistence.database import create_engine

    async def run():
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                out = []
                for model in (EvidenceRun, EvidenceRouteLatency, EvidenceStatementStats):
                    out.append(list((await session.execute(select(model))).scalars()))
                return tuple(out)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_production_files_store_their_rows_with_no_class_and_no_commit(indexed) -> None:
    root, _head = indexed

    routes = _ingest(root, _fixture("route_latency.json"), workload="api", workload_class=None)
    statements = _ingest(
        root, _fixture("pg_stat_statements.csv"), workload="prod db", workload_class=None
    )

    assert routes["stored"] and statements["stored"]
    assert (routes["class"], routes["commit_sha"]) == ("production", "c" * 40)
    assert routes["window"]["start"] == "2026-11-19T00:00:00+00:00"
    assert (statements["input_format"], statements["commit_sha"]) == ("pg-stat-statements", None)
    assert [w["code"] for w in statements["warnings"]] == ["deployed_commit_unknown"]
    runs, route_rows, statement_rows = _stored_production(root)
    assert sorted((r.kind, r.workload_class) for r in runs) == [
        ("route_latency", "production"),
        ("statement_stats", "production"),
    ]
    by_kind = {r.kind: r for r in runs}
    assert by_kind["route_latency"].base_commit_sha == "d" * 40
    assert by_kind["statement_stats"].commit_sha is None
    assert [(r.method, r.route, r.request_count) for r in route_rows] == [
        ("GET", "/api/repos/:id", 48210),
        ("POST", "/api/repos", 310),
    ]
    assert len(statement_rows) == 3
    # The masked text is all that is stored: the literal the export carried is gone.
    assert all("literal-left" not in (r.query_text or "") for r in statement_rows)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"workload_class": "production"}, "never from --class"),
        ({"workload_class": "load_test"}, "production by what it measures"),
    ],
)
def test_a_production_file_refuses_a_typed_class(indexed, options, message) -> None:
    root, _head = indexed
    with pytest.raises(EvidenceRefusedError, match=message) as refused:
        _ingest(root, _fixture("statement_stats.json"), workload="db", **options)
    assert refused.value.option == "--class"
    runs, _, statements = _stored_production(root)
    assert runs == [] and statements == []
