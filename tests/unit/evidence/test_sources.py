"""The ``evidence-source`` seam: the gate against an uploaded file, and the contract every source passes.

The contract tests drive :mod:`tests.unit.evidence.source_double`, loaded as
a plugin through a ``repowise.plugins`` entry point. A plugin author can run
the same checks against their own source by swapping the batches it fetches.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.metadata
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from repowise.cli.commands import evidence_cmd
from repowise.core.evidence.add import ingest_batch
from repowise.core.evidence.kinds import evidence_kinds
from repowise.core.evidence.payloads import functions_payload
from repowise.core.evidence.sources import (
    SOURCE_COLUMN,
    FetchOutcome,
    SourceBatch,
    SourceRequest,
    evidence_sources,
    run_fetch,
)
from repowise.core.persistence import create_session_factory, get_session
from repowise.core.persistence.database import create_engine
from repowise.core.persistence.models import Repository, SymbolHotness
from repowise.core.registry import plugins
from tests.unit.cli.test_evidence_cmd import _add, _indexed_copy, _json, _stored
from tests.unit.evidence import source_double
from tests.unit.evidence.source_double import SECRET, Credentials
from tests.unit.evidence.test_add import _index_test_file

_REPO = Path(__file__).resolve().parents[3]

#: A profile as a user would write it, split into the batch's entries and document.
_PROFILE_DOC = {
    "value_kind": "wall",
    "unit": "samples",
    "total": 100,
    "explained": 60,
    "repo_self": 45,
    "waits": 30,
    "libraries": 10,
    "tool": {"name": "pyinstrument", "version": "4.6.2"},
    "skipped": {"outside_repo": 5},
}
_PROFILE_ENTRIES = [
    {"path": "src/app.py", "function": "build", "line": 1, "self": 40, "total": 60},
    {"path": "src/edited.py", "function": "f", "line": 1, "self": 5, "total": 10},
    {"path": "src/app.py", "function": "missing", "total": 3},
]
_STATEMENTS = [
    {
        "statement_fp": "a0b1c2d3e4f50617",
        "query": "select * from repos where id = $1",
        "calls": 50000,
        "total_ms": 31950.5,
        "mean_ms": 0.639,
        "rows": 50000,
    },
]
_STATEMENT_DOC = {
    "tool": {"name": "pg_stat_statements"},
    "window": {"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"},
}


def _profile_batch(head: str | None, **changes: Any) -> SourceBatch:
    batch = SourceBatch(
        kind="profile",
        workload="app",
        workload_class="entry_point",
        commit=head,
        environment="ci",
        entries=_PROFILE_ENTRIES,
        document=_PROFILE_DOC,
    )
    return replace(batch, **changes)


def _statement_batch(**changes: Any) -> SourceBatch:
    batch = SourceBatch(
        kind="statement_stats",
        workload="prod db",
        workload_class=None,
        commit=None,
        entries=_STATEMENTS,
        document=_STATEMENT_DOC,
    )
    return replace(batch, **changes)


@pytest.fixture
def double(monkeypatch: pytest.MonkeyPatch) -> source_double.DoubleSource:
    """The test double, loaded as a plugin through a ``repowise.plugins`` entry point."""
    impls, owners = dict(evidence_sources._impls), dict(evidence_sources._owners)
    eps = [
        importlib.metadata.EntryPoint(
            "double", "tests.unit.evidence.source_double:register", plugins.ENTRY_POINT_GROUP
        )
    ]
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: eps if kw.get("group") == plugins.ENTRY_POINT_GROUP else [],
    )
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    plugins.reset()
    try:
        source = evidence_sources.get("double")
        (record,) = plugins.load_plugins()
        assert (record.status, record.seams) == ("loaded", ["evidence-source:double"])
        yield source
    finally:
        plugins.reset()
        evidence_sources._impls.clear()
        evidence_sources._impls.update(impls)
        evidence_sources._owners.clear()
        evidence_sources._owners.update(owners)


@pytest.fixture
def indexed(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    root = tmp_path / "source"
    return root, _indexed_copy(root, tmp_path_factory)


def _request(cursor: str | None = None, **settings: Any) -> SourceRequest:
    return SourceRequest(
        repository_id="r1",
        repository_name="t",
        cursor=cursor,
        settings=settings,
        credentials=Credentials(SECRET),
    )


def _fetch(
    root: Path, source: Any, request: SourceRequest, repo_path: Path | None = None
) -> FetchOutcome:
    """Fetch into *root*'s store; *repo_path* is the checkout the host names (default *root*)."""

    async def run() -> FetchOutcome:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await session.get(Repository, "r1")
                return await run_fetch(
                    source,
                    request,
                    lambda batch: ingest_batch(session, repo, batch, repo_path=repo_path or root),
                )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _tables(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every evidence and hotness row, with ids, times and the checkout path taken out."""
    volatile = {
        "id",
        "run_id",
        "run_key",
        "ingested_at",
        "measured_at",
        "updated_at",
        "computed_at",
        "created_at",
        "built_at",
        "source",
    }
    paths = {str(root), str(root.resolve())}
    db = sqlite3.connect(root / ".repowise" / "wiki.db")
    db.row_factory = sqlite3.Row
    try:
        names = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND (name LIKE 'evidence_%' OR name LIKE '%hotness%') ORDER BY name"
            )
        ]
        # A row's run, by what identifies it on both sides, so a row on the wrong run differs.
        runs = {
            r["id"]: f"{r['kind']}/{r['workload_name']}"
            for r in db.execute("SELECT id, kind, workload_name FROM evidence_runs")
        }
        out = {}
        for name in names:
            rows = []
            for row in db.execute(f'SELECT * FROM "{name}"'):
                rows.append(
                    {
                        k: ("<root>" if row[k] in paths else row[k])
                        for k in row.keys()  # noqa: SIM118 - a Row iterates values
                        if k not in volatile
                    }
                    | ({"run": runs.get(row["run_id"])} if "run_id" in row.keys() else {})  # noqa: SIM118
                )
            out[name] = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, default=str))
        return out
    finally:
        db.close()


def _functions(root: Path) -> dict[str, Any]:
    async def read() -> dict[str, Any]:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                rows = (await session.execute(select(SymbolHotness))).scalars()
                payload = functions_payload({r.symbol_id: r for r in rows})
        finally:
            await engine.dispose()
        for row in payload["functions"]:
            row["run_id"] = row["measured_at"] = None
        return payload

    return asyncio.run(read())


def _speedscope(root: Path) -> bytes:
    return json.dumps(
        {
            "$schema": "https://www.speedscope.app/file-format-schema.json",
            "exporter": "pyinstrument@5.1.3",
            "shared": {
                "frames": [{"name": "build", "file": str(root / "src" / "app.py"), "line": 1}]
            },
            "profiles": [
                {"type": "sampled", "unit": "seconds", "samples": [[0], [0]], "weights": [2.0, 2.0]}
            ],
        }
    ).encode()


# --- Gate: a source's runs are an upload's runs ---------------------------------------


_TESTS = [
    {
        "id": "tests/test_app.py::TestBuild::test_builds",
        "path": "tests/test_app.py",
        "function": "TestBuild::test_builds",
        "line": 2,
        "duration_s": 1.25,
        "outcome": "passed",
    },
    {
        "id": "tests.test_app.TestBuild::test_gone",
        "classname": "tests.test_app.TestBuild",
        "duration_s": 0.5,
        "outcome": "failed",
    },
]
_BENCHMARKS = [
    {
        "name": "test_build[small]",
        "path": "src/app.py",
        "function": "build",
        "exact": True,
        "unit": "s",
        "median": 0.012,
        "mean": 0.013,
        "min": 0.011,
        "max": 0.02,
        "rounds": 50,
    },
    {"name": "BenchmarkParse-8", "unit": "ns", "mean": 120.5, "allocs_per_op": 3},
]


def _gate_batches(root: Path, head: str) -> list[tuple[SourceBatch, list[str]]]:
    """Each batch, with the ``evidence add`` options that upload the same run as a file."""
    run = ["--commit", head, "--environment", "ci"]
    return [
        (_profile_batch(head), ["--workload", "app", "--class", "entry_point", *run]),
        (_statement_batch(), ["--workload", "prod db"]),
        (
            SourceBatch(
                kind="test_timing",
                workload="suite",
                workload_class="unit_test",
                commit=head,
                environment="ci",
                entries=_TESTS,
                document={"tool": {"name": "junit"}},
            ),
            ["--workload", "suite", "--class", "unit_test", *run],
        ),
        (
            SourceBatch(
                kind="benchmark",
                workload="bench",
                workload_class="benchmark",
                commit=head,
                environment="ci",
                entries=_BENCHMARKS,
                document={"unit": "s", "tool": {"name": "pytest-benchmark"}},
            ),
            ["--workload", "bench", "--class", "benchmark", *run],
        ),
        (
            SourceBatch(
                kind="profile",
                workload="cli",
                workload_class="load_test",
                commit=head,
                environment="ci",
                data=_speedscope(root),
                format="speedscope",
            ),
            ["--workload", "cli", "--class", "load_test", *run],
        ),
    ]


def _upload_file(root: Path, batch: SourceBatch, i: int) -> Path:
    """*batch* written as the file a user would upload, in their own key order."""
    path = root / f"upload{i}.json"
    if batch.data is not None:
        path.write_bytes(batch.data)
    else:
        entries = evidence_kinds[batch.kind].entries
        doc = {"format": "repowise-evidence-v1", "kind": batch.kind, entries: list(batch.entries)}
        path.write_text(json.dumps({**doc, **batch.document}, indent=2))
    return path


def test_a_source_batch_stores_what_uploading_its_entries_as_a_file_stores(
    double, indexed, tmp_path, tmp_path_factory, monkeypatch
) -> None:
    root, head = indexed
    upload = tmp_path / "upload"
    _indexed_copy(upload, tmp_path_factory)
    for checkout in (root, upload):
        _index_test_file(checkout)
    monkeypatch.setattr(evidence_cmd, "_resolve_repo", lambda _p, _n: upload)
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)

    uploaded = [
        _json(_add(str(_upload_file(upload, batch, i)), *options, "--format", "json"))
        for i, (batch, options) in enumerate(_gate_batches(upload, head))
    ]
    double.batches = [batch for batch, _ in _gate_batches(root, head)]
    outcome = _fetch(root, double, _request())

    assert (outcome.status, outcome.cursor, outcome.error) == ("ok", "5", None)
    assert [p["kind"] for p in outcome.stored] == [
        "profile",
        "statement_stats",
        "test_timing",
        "benchmark",
        "profile",
    ]
    assert all(p["stored"] for p in outcome.stored)
    for got, want in zip(outcome.stored, uploaded, strict=True):
        assert {**got, "run_id": None} == {**want, "run_id": None}
    tables = _tables(root)
    assert all(
        tables[t]
        for t in (
            "evidence_symbol_costs",
            "evidence_statement_stats",
            "evidence_test_timings",
            "evidence_benchmark_results",
            "symbol_hotness",
        )
    ), {t: len(rows) for t, rows in tables.items()}
    assert tables == _tables(upload)
    functions = _functions(root)
    assert [f["symbol_id"] for f in functions["functions"]] == [
        "src/app.py::build",
        "src/edited.py::f",
    ]
    assert functions == _functions(upload)
    runs, _ = _stored(root)
    assert {r.source for r in runs} == {SOURCE_COLUMN}


# --- Contract: what every source must pass -------------------------------------------


def test_refetching_from_the_same_cursor_stores_nothing_twice(double, indexed) -> None:
    root, head = indexed
    double.batches = [_profile_batch(head), _statement_batch()]

    first = _fetch(root, double, _request())
    tables = _tables(root)
    again = _fetch(root, double, _request())
    resumed = _fetch(root, double, _request(cursor="1"))

    assert (first.status, first.cursor) == ("ok", "2")
    assert [p["restamped"] for p in first.stored] == [False, False]
    assert [p["restamped"] for p in again.stored] == [True, True]
    assert [p["restamped"] for p in resumed.stored] == [True]
    assert resumed.cursor == "2"
    assert _tables(root) == tables
    assert len(_stored(root)[0]) == 2


def test_an_unavailable_source_keeps_the_stored_batches_and_the_cursor_to_retry_from(
    double, indexed
) -> None:
    root, head = indexed
    double.batches = [_profile_batch(head), _statement_batch()]

    down = _fetch(root, double, _request(unavailable_at=1))
    retried = _fetch(root, double, _request(cursor=down.cursor))

    assert (down.status, down.cursor, len(down.stored)) == ("unavailable", "1", 1)
    assert "timed out" in down.error
    assert (retried.status, retried.cursor, len(retried.stored)) == ("ok", "2", 1)
    assert sorted(r.kind for r in _stored(root)[0]) == ["profile", "statement_stats"]


def test_a_refused_source_stores_nothing_and_says_why(double, indexed) -> None:
    root, head = indexed
    double.batches = [_profile_batch(head)]

    outcome = _fetch(root, double, replace(_request(cursor="0"), credentials=Credentials("x")))

    assert (outcome.status, outcome.cursor, outcome.stored) == ("refused", "0", [])
    assert "refused the login" in outcome.error
    assert _stored(root) == ([], [])


def test_any_other_error_fails_the_fetch_after_the_batches_already_stored(double, indexed) -> None:
    root, head = indexed
    double.batches = [_profile_batch(head), _statement_batch()]

    outcome = _fetch(root, double, _request(fail_at=1))

    assert (outcome.status, outcome.cursor, len(outcome.stored)) == ("failed", "1", 1)
    assert outcome.error == "RuntimeError: unexpected reply"


def test_a_batch_that_fails_while_storing_leaves_nothing_of_it(
    double, indexed, monkeypatch
) -> None:
    root, head = indexed
    double.batches = [_statement_batch(), _profile_batch(head)]
    from repowise.core.evidence import hotness

    async def broken(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    # Runs after the profile's run and rows are written: the batch's savepoint must undo them.
    monkeypatch.setattr(hotness, "refresh_symbol_hotness", broken)
    outcome = _fetch(root, double, _request())

    assert (outcome.status, outcome.cursor, len(outcome.stored)) == ("failed", "1", 1)
    runs, costs = _stored(root)
    assert ([r.kind for r in runs], costs) == (["statement_stats"], [])


def test_a_batch_of_a_kind_the_source_does_not_declare_fails_the_fetch(double, indexed) -> None:
    root, head = indexed
    double.batches = [_profile_batch(head, kind="query_count")]

    outcome = _fetch(root, double, _request())

    assert outcome.status == "failed" and "query_count" in outcome.error
    assert _stored(root) == ([], [])


@pytest.mark.parametrize(
    ("commit", "stored", "stale"),
    [("HEAD", "HEAD", False), ("other", "other", True), ("upper", "HEAD", False)],
)
def test_a_run_keeps_the_commit_its_batch_names(double, indexed, commit, stored, stale) -> None:
    root, head = indexed
    other = "b" * 40
    given = {"HEAD": head, "other": other, "upper": head.upper()}[commit]
    double.batches = [_profile_batch(given)]

    outcome = _fetch(root, double, _request())

    assert outcome.status == "ok"
    (payload,) = outcome.stored
    assert (payload["commit_sha"], payload["stale"]) == (
        {"HEAD": head, "other": other}[stored],
        stale,
    )
    (run,), _ = _stored(root)
    assert run.commit_sha == payload["commit_sha"]


@pytest.mark.parametrize("commit", [None, "abc1234"], ids=["missing", "short"])
def test_a_measured_run_without_a_full_commit_is_refused(double, indexed, commit) -> None:
    root, _head = indexed
    double.batches = [_profile_batch(commit)]

    outcome = _fetch(root, double, _request())

    assert outcome.status == "failed" and "batch commit" in outcome.error
    assert _stored(root) == ([], [])


def test_a_production_window_with_no_deployed_commit_says_so(double, indexed) -> None:
    root, _head = indexed
    double.batches = [_statement_batch()]

    (payload,) = _fetch(root, double, _request()).stored

    assert (payload["class"], payload["commit_sha"]) == ("production", None)
    assert [w["code"] for w in payload["warnings"]] == ["deployed_commit_unknown"]
    assert payload["window"]["start"].startswith("2026-10-01")


@pytest.mark.parametrize(
    ("batch", "stored_class"),
    [
        (lambda head: _profile_batch(head, workload_class="production"), "production"),
        (lambda head: _statement_batch(workload_class="production"), "production"),
        (lambda head: _statement_batch(workload_class="load_test"), None),
        (lambda head: _profile_batch(head, workload_class=None), None),
        (lambda head: _profile_batch(head, workload_class="nightly"), None),
    ],
    ids=["profile_production", "production_kind", "production_kind_typed", "no_class", "unknown"],
)
def test_class_rules(double, indexed, batch, stored_class) -> None:
    root, head = indexed
    double.batches = [batch(head)]

    outcome = _fetch(root, double, _request())

    runs, _ = _stored(root)
    if stored_class is None:
        assert outcome.status == "failed" and runs == []
    else:
        assert outcome.status == "ok"
        assert [r.workload_class for r in runs] == [stored_class]


def test_a_test_timing_batch_cannot_be_production(double, indexed) -> None:
    root, head = indexed
    double.batches = [
        SourceBatch(
            kind="test_timing",
            workload="suite",
            workload_class="production",
            commit=head,
            entries=[
                {
                    "path": "tests/test_app.py",
                    "function": "test_x",
                    "seconds": 1.0,
                    "outcome": "passed",
                }
            ],
        )
    ]

    outcome = _fetch(root, double, _request())

    assert outcome.status == "failed" and "does not apply" in outcome.error
    assert _stored(root) == ([], [])


def test_the_credentials_never_reach_the_store(double, indexed) -> None:
    root, head = indexed
    double.batches = [_profile_batch(head, measured_at=None), _statement_batch()]
    request = _request()

    outcome = _fetch(root, double, request)

    assert outcome.status == "ok"
    assert SECRET not in repr(request)
    dump = json.dumps(_tables(root), default=str) + json.dumps(outcome.stored, default=str)
    assert SECRET not in dump
    db = sqlite3.connect(root / ".repowise" / "wiki.db")
    try:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for name in names:
            for row in db.execute(f'SELECT * FROM "{name}"'):
                assert SECRET not in json.dumps(list(row), default=str), name
    finally:
        db.close()


def test_a_batch_records_when_it_was_measured(double, indexed) -> None:
    from datetime import UTC, datetime

    root, head = indexed
    at = datetime(2026, 11, 1, 12, tzinfo=UTC)
    double.batches = [_profile_batch(head, measured_at=at)]

    assert _fetch(root, double, _request()).status == "ok"

    (run,), _ = _stored(root)
    assert run.measured_at.replace(tzinfo=UTC) == at


# --- Boundary: no fold knows where a run came from -----------------------------------


def _imports_sources(node: ast.AST, package: str) -> bool:
    """Whether *node*, in a module of *package*, imports ``repowise.core.evidence.sources``."""
    seam = "repowise.core.evidence.sources"
    if isinstance(node, ast.Import):
        return any(a.name == seam or a.name.startswith(seam + ".") for a in node.names)
    if not isinstance(node, ast.ImportFrom):
        return False
    base = package.split(".")
    if node.level:
        base = base[: len(base) - node.level + 1]
    module = ".".join([*(base if node.level else []), *([node.module] if node.module else [])])
    return module == seam or any(f"{module}.{a.name}" == seam for a in node.names)


@pytest.mark.parametrize(
    "line",
    [
        "from repowise.core.evidence.sources import SourceBatch",
        "from repowise.core.evidence import sources",
        "from . import sources",
        "from .sources import run_fetch",
        "from ..evidence import sources",
        "import repowise.core.evidence.sources",
    ],
)
def test_the_import_check_sees_every_form(line: str) -> None:
    (node,) = ast.parse(line).body
    assert _imports_sources(node, "repowise.core.evidence")
    assert not _imports_sources(
        ast.parse("from .kinds import evidence_kinds").body[0], "repowise.core.evidence"
    )


def test_only_the_runner_imports_the_source_seam() -> None:
    importers = set()
    for path in (_REPO / "packages").glob("*/src/**/*.py"):
        parts = path.relative_to(_REPO / "packages").parts
        package = ".".join(parts[2:-1])  # <package>/src/<dotted package>/<module>.py
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(_imports_sources(node, package) for node in ast.walk(tree)):
            importers.add(path.relative_to(_REPO).as_posix())

    assert importers == {"packages/core/src/repowise/core/evidence/add.py"}


def test_a_data_batch_must_hold_the_kind_it_names(double, indexed) -> None:
    root, _head = indexed
    raw = json.dumps(
        {"format": "repowise-evidence-v1", "kind": "statement_stats", "statements": _STATEMENTS}
    ).encode()
    double.batches = [
        SourceBatch(
            kind="profile",
            workload="w",
            workload_class="entry_point",
            commit=None,
            data=raw,
            format="repowise-evidence-v1",
        )
    ]

    outcome = _fetch(root, double, _request())

    assert outcome.status == "failed" and "not a profile run" in outcome.error
    assert _stored(root) == ([], [])


def test_a_batch_with_no_cursor_keeps_the_last_one(double, indexed) -> None:
    root, head = indexed
    double.batches = [_profile_batch(head)]
    plain = source_double.DoubleSource.fetch

    def without_cursor(self, request):
        for batch in plain(self, request):
            yield replace(batch, next_cursor=None)

    double.fetch = without_cursor.__get__(double)

    outcome = _fetch(root, double, _request(cursor="0"))

    assert (outcome.status, outcome.cursor) == ("ok", "0")


def test_a_production_profile_still_names_its_commit(double, indexed) -> None:
    root, _head = indexed
    double.batches = [_profile_batch(None, workload_class="production")]

    outcome = _fetch(root, double, _request())

    assert outcome.status == "failed" and "batch commit" in outcome.error
    assert _stored(root) == ([], [])


def test_a_fetch_that_stops_early_closes_the_source(double, indexed) -> None:
    root, _head = indexed
    double.batches = [_profile_batch(None), _statement_batch()]
    closed = []
    plain = source_double.DoubleSource.fetch

    def tracked(self, request):
        try:
            yield from plain(self, request)
        finally:
            closed.append(True)

    double.fetch = tracked.__get__(double)

    outcome = _fetch(root, double, _request())

    assert (outcome.status, closed) == ("failed", [True])


def test_a_batch_for_a_repository_with_no_index_keeps_the_cursor(double, tmp_path) -> None:
    from repowise.core.evidence.add import EvidenceRefusedError

    async def run() -> FetchOutcome:
        async def store(batch: SourceBatch) -> dict[str, Any]:
            return await ingest_batch(None, None, batch, repo_path=tmp_path)

        return await run_fetch(double, _request(cursor="0"), store)

    double.batches = [_statement_batch()]
    outcome = asyncio.run(run())

    assert (outcome.status, outcome.cursor, outcome.stored) == ("failed", "0", [])
    assert outcome.error.startswith(EvidenceRefusedError.__name__)
    assert "no index" in outcome.error


def test_a_measured_time_with_an_offset_is_stored_as_utc(double, indexed) -> None:
    from datetime import UTC, datetime, timedelta, timezone

    root, head = indexed
    at = datetime(2026, 11, 1, 14, tzinfo=timezone(timedelta(hours=2)))
    double.batches = [_profile_batch(head, measured_at=at)]

    assert _fetch(root, double, _request()).status == "ok"

    (run,), _ = _stored(root)
    assert run.measured_at.replace(tzinfo=UTC) == datetime(2026, 11, 1, 12, tzinfo=UTC)


def test_a_batch_over_the_file_size_setting_is_still_stored(double, indexed) -> None:
    root, _head = indexed
    (root / ".repowise" / "config.yaml").write_text("evidence:\n  max_file_mb: 0.0001\n")
    double.batches = [_statement_batch()]

    outcome = _fetch(root, double, _request())

    assert (outcome.status, outcome.cursor) == ("ok", "1")
    assert [r.kind for r in _stored(root)[0]] == ["statement_stats"]


def test_a_production_batch_with_no_environment_ran_in_production(double, indexed) -> None:
    root, head = indexed
    double.batches = [_statement_batch(), _profile_batch(head, environment=None)]

    assert _fetch(root, double, _request()).status == "ok"

    runs, _ = _stored(root)
    assert sorted((r.kind, r.environment) for r in runs) == [
        ("profile", "unknown"),
        ("statement_stats", "production"),
    ]


def test_a_refetch_corrects_when_the_run_was_measured(double, indexed) -> None:
    from datetime import UTC, datetime

    root, head = indexed
    at = datetime(2026, 11, 1, 12, tzinfo=UTC)
    double.batches = [_profile_batch(head)]
    _fetch(root, double, _request())
    double.batches = [_profile_batch(head, measured_at=at)]

    (payload,) = _fetch(root, double, _request()).stored

    assert payload["restamped"] is True
    (run,), _ = _stored(root)
    assert run.measured_at.replace(tzinfo=UTC) == at


# --- The statement-site join: a source's statements name the functions an upload names ---


def _store_sites(root: Path) -> None:
    from repowise.core.persistence.crud import replace_statement_sites
    from tests.unit.evidence.test_statement_ingest import SITES

    async def store() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                await replace_statement_sites(session, "r1", SITES, "fp")
        finally:
            await engine.dispose()

    asyncio.run(store())


def _issuers(root: Path) -> dict[str, tuple[str | None, str | None]]:
    from repowise.core.persistence.models import EvidenceStatementStats

    async def read() -> dict[str, tuple[str | None, str | None]]:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                rows = (await session.execute(select(EvidenceStatementStats))).scalars()
                return {r.statement_fp: (r.symbol_id, r.issuer) for r in rows}
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_a_statement_batch_names_the_issuing_function_an_upload_names(
    double, indexed, tmp_path, tmp_path_factory, monkeypatch
) -> None:
    from tests.unit.evidence.test_statement_ingest import _statements

    root, _head = indexed
    upload = tmp_path / "upload"
    _indexed_copy(upload, tmp_path_factory)
    doc = json.loads(_statements())
    doc["statements"].append(
        # Two functions insert into events through the client: never one picked.
        {
            "statement_fp": "5" * 16,
            "calls": 4,
            "query": 'WITH pgrst_source AS (INSERT INTO "public"."events"("a") SELECT 1) SELECT $1',
        }
    )
    for checkout in (root, upload):
        _store_sites(checkout)
    monkeypatch.setattr(evidence_cmd, "_resolve_repo", lambda _p, _n: upload)
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    path = upload / "statements.json"
    path.write_text(json.dumps(doc))
    uploaded = _json(_add(str(path), "--workload", "prod db", "--format", "json"))
    double.batches = [_statement_batch(entries=doc["statements"], document={})]

    outcome = _fetch(root, double, _request())

    assert outcome.status == "ok"
    assert {**outcome.stored[0], "run_id": None} == {**uploaded, "run_id": None}
    issuers = _issuers(root)
    assert issuers == _issuers(upload)
    assert issuers == {
        "1" * 16: ("src/app.py::build", "function"),
        "2" * 16: (None, "database_schedule"),
        "3" * 16: (None, None),
        "4" * 16: (None, None),
        "5" * 16: (None, "ambiguous"),
    }


# --- A host with no checkout (hosted) ------------------------------------------------


def test_a_host_with_no_checkout_stores_entries_batches_with_no_checkout_root(
    double, indexed, tmp_path
) -> None:
    root, head = indexed
    no_checkout = tmp_path / "no-checkout"  # never created, like hosted's
    v1_bytes = json.dumps(
        {"format": "repowise-evidence-v1", "kind": "statement_stats", "statements": _STATEMENTS}
    ).encode()
    double.batches = [
        _statement_batch(),
        _profile_batch(head, workload_class="production"),
        # A v1 document handed over as bytes reads no source either.
        _statement_batch(
            workload="prod db bytes", entries=None, document=None,
            data=v1_bytes, format="repowise-evidence-v1",
        ),
    ]

    outcome = _fetch(root, double, _request(), repo_path=no_checkout)

    assert outcome.status == "ok", outcome.error
    assert [p["stored"] for p in outcome.stored] == [True, True, True]
    runs, _costs = _stored(root)
    assert sorted((r.kind, r.checkout_root) for r in runs) == [
        ("profile", None),
        ("statement_stats", None),
        ("statement_stats", None),
    ]


def test_a_batch_of_profile_bytes_still_needs_its_checkout(double, indexed, tmp_path) -> None:
    root, head = indexed
    double.batches = [
        SourceBatch(
            kind="profile",
            workload="cli",
            workload_class="load_test",
            commit=head,
            data=_speedscope(root),
            format="speedscope",
        )
    ]

    outcome = _fetch(root, double, _request(), repo_path=tmp_path / "no-checkout")

    assert outcome.status == "failed" and "checkout's source" in outcome.error
    assert _stored(root) == ([], [])


def test_profile_bytes_are_refused_with_no_checkout_whoever_waives_it(indexed, tmp_path) -> None:
    from repowise.core.evidence.add import EvidenceRefusedError, ingest_evidence

    root, head = indexed

    async def run() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await session.get(Repository, "r1")
                await ingest_evidence(
                    session, repo, _speedscope(root), repo_path=tmp_path / "no-checkout",
                    workload="cli", workload_class="load_test", commit=head,
                    require_checkout=False,
                )
        finally:
            await engine.dispose()

    with pytest.raises(EvidenceRefusedError, match="checkout's source"):
        asyncio.run(run())
    assert _stored(root) == ([], [])


# --- A production run names its window ------------------------------------------------


def _served(root: Path, head: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The ``runs`` and ``measured`` routes' bodies, built and serialized as the server does."""
    from repowise.core.evidence.payloads import measured_payload, runs_payload
    from repowise.core.persistence import crud
    from repowise.server.schemas.runtime_evidence import EvidenceRunsResponse, MeasuredResponse

    async def read() -> tuple[dict[str, Any], dict[str, Any]]:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                runs = await crud.list_evidence_runs(session, "r1")
                view = await crud.measured_costs(session, "r1", limit=10)
                status = await crud.measured_status(session, "r1")
        finally:
            await engine.dispose()
        return (
            EvidenceRunsResponse(**runs_payload(runs, head)).model_dump(mode="json", by_alias=True),
            MeasuredResponse(**measured_payload(view, status)).model_dump(
                mode="json", by_alias=True
            ),
        )

    return asyncio.run(read())


def test_only_production_runs_name_their_window_on_the_wire(double, indexed) -> None:
    root, head = indexed
    db = sqlite3.connect(root / ".repowise" / "wiki.db")
    with db:  # indexed at the profiled commit, so its rows rank
        db.execute("UPDATE repositories SET head_commit = ?", (head,))
    db.close()
    double.batches = [
        _statement_batch(),
        _profile_batch(head, workload="api", workload_class="production"),
        _profile_batch(head),
    ]
    assert _fetch(root, double, _request()).status == "ok"

    runs, measured = _served(root, head)

    by_workload = {r["workload"]: r for r in runs["runs"]}
    window = {name: r.get("window") for name, r in by_workload.items()}
    # Absent, not null: a run of any other class serializes as it did.
    assert "window" not in by_workload["app"]
    assert window["prod db"] == {
        "start": "2026-10-01T00:00:00Z",
        "end": "2026-10-02T00:00:00Z",
        "base_commit": None,
        "claim": "in production over 2026-10-01 to 2026-10-02, deployed commit unknown",
    }
    # A production profile with no window in its file still says it ran in production.
    assert window["api"]["claim"] == f"in production, window unknown, deployed {head[:12]}"
    ranked = {r["workload_name"]: r for r in measured["measured"]["runs"]}
    assert ranked["api"]["window"] == window["api"]
    assert "window" not in ranked["app"]
