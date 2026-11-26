"""``repowise evidence add`` against a small real index."""

from __future__ import annotations

import asyncio
import gzip
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.commands import evidence_cmd
from repowise.core.persistence import create_session_factory, get_session
from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.models import (
    EvidenceBenchmarkResult,
    EvidenceRun,
    EvidenceSymbolCost,
    FunctionFact,
    HealthFileMetric,
    Repository,
    SymbolHotness,
    WikiSymbol,
)
from tests.unit.evidence.test_profile_formats import _pprof


def _git_repo(root: Path) -> str:
    import git as gitpython

    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def build():\n    pass\n")
    (root / "src" / "edited.py").write_text("def f():\n    pass\n")
    repo.index.add(["src/app.py", "src/edited.py"])
    head = repo.index.commit("init").hexsha
    repo.close()
    return head


def _index(root: Path) -> None:
    async def build() -> None:
        (root / ".repowise").mkdir()
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                session.add(Repository(id="r1", name="t", local_path=str(root), url=""))
                await session.flush()
                for path, name in (("src/app.py", "build"), ("src/edited.py", "f")):
                    session.add(HealthFileMetric(repository_id="r1", file_path=path))
                    session.add(
                        WikiSymbol(
                            repository_id="r1", file_path=path, symbol_id=f"{path}::{name}",
                            name=name, qualified_name=name, kind="function", start_line=1,
                            end_line=2,
                        )
                    )
        finally:
            await engine.dispose()

    asyncio.run(build())


#: One repo and index per test process, built by the first :func:`_indexed_copy`.
_TEMPLATE: list[tuple[Path, str]] = []


def _indexed_copy(root: Path, factory: pytest.TempPathFactory) -> str:
    """Put a copy of the :func:`_git_repo` + :func:`_index` checkout at *root*; return its head.

    Building the repo and index cost each test that took them about 0.7 s; the
    copy costs a few milliseconds. Every test still gets its own files and
    ``wiki.db``, so tests that write to them stay independent.
    """
    if not _TEMPLATE:
        template = factory.mktemp("indexed-template")
        head = _git_repo(template)
        _index(template)
        _TEMPLATE.append((template, head))
    template, head = _TEMPLATE[0]
    shutil.copytree(template, root, dirs_exist_ok=True)
    db = sqlite3.connect(root / ".repowise" / "wiki.db")
    try:
        with db:
            db.execute("UPDATE repositories SET local_path = ?", (str(root),))
    finally:
        db.close()
    return head


def _stored(root: Path) -> tuple[list, list]:
    async def read() -> tuple[list, list]:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                runs = list((await session.execute(select(EvidenceRun))).scalars())
                costs = list((await session.execute(select(EvidenceSymbolCost))).scalars())
                return runs, costs
        finally:
            await engine.dispose()

    return asyncio.run(read())


@pytest.fixture
def indexed(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    head = _indexed_copy(tmp_path, tmp_path_factory)
    monkeypatch.setattr(evidence_cmd, "_resolve_repo", lambda _p, _n: tmp_path)
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    return tmp_path, head


def _write(root: Path, **fields) -> Path:
    doc = {
        "format": "repowise-evidence-v1", "kind": "profile", "value_kind": "wall",
        "unit": "samples", "total": 100, "explained": 60, "repo_self": 45, "waits": 30,
        "libraries": 10,
        "tool": {"name": "pyinstrument", "version": "4.6.2"},
        "skipped": {"outside_repo": 5},
        "symbols": [
            {"path": "src/app.py", "function": "build", "line": 1, "self": 40, "total": 60},
            {"path": "src/app.py", "function": "missing", "total": 3},
        ],
        **fields,
    }
    path = root / "prof.json"
    path.write_text(json.dumps(doc))
    return path


def _add(*args: str):
    return CliRunner(mix_stderr=False).invoke(evidence_cmd.evidence_group, ["add", *args])


def _json(result) -> dict:
    assert result.stdout.strip(), (result.output, result.exception)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise AssertionError(result.stdout[:2000]) from None


def test_add_stores_the_run_with_its_provenance(indexed) -> None:
    root, head = indexed
    result = _add(
        str(_write(root)), "--workload", "cli build", "--class", "entry_point",
        "--commit", head, "--environment", "local", "--format", "json",
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["stored"] is True
    assert (payload["matched"], payload["stale"], payload["dirty"]) == (1, False, False)
    assert payload["skipped"] == {"outside_repo": 5, "unknown_symbol": 1}

    (run,), (cost,) = _stored(root)
    assert (run.kind, run.workload_name, run.workload_class) == ("profile", "cli build",
                                                                  "entry_point")
    assert (run.commit_sha, run.dirty, run.environment) == (head, False, "local")
    assert run.checkout_root == str(root.resolve())
    assert (run.tool_name, run.tool_version, run.value_kind, run.unit) == (
        "pyinstrument", "4.6.2", "wall", "samples",
    )
    assert (run.units_total, run.units_matched, run.units_skipped, run.units_outside_repo) == (
        7, 1, 1, 5,
    )
    assert json.loads(run.skipped_json) == {"outside_repo": 5, "unknown_symbol": 1}
    assert (run.total_value, run.explained_value, run.source) == (100, 60, "cli")
    assert (run.repo_self_value, run.wait_value, run.library_value) == (45, 30, 10)
    assert (cost.symbol_id, cost.total_value, cost.self_value) == ("src/app.py::build", 60, 40)

    again = _add(
        str(_write(root)), "--workload", "cli build", "--class", "entry_point",
        "--commit", head, "--environment", "local", "--format", "json",
    )
    assert _json(again)["restamped"] is True
    assert len(_stored(root)[0]) == 1


def _db(root: Path, work):
    async def run():
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                return await work(session)
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_a_profile_stamps_lineage_and_body_and_rebuilds_hotness(indexed) -> None:
    root, head = indexed

    async def at_head(session) -> None:
        (await session.get(Repository, "r1")).head_commit = head
        session.add(FunctionFact(repository_id="r1", symbol_id="src/app.py::build",
                                 body_digest="d" * 32, lineage_id="L" * 16))

    _db(root, at_head)
    result = _add(str(_write(root)), "--workload", "cli build", "--class", "entry_point",
                  "--commit", head, "--format", "json")
    assert result.exit_code == 0, result.output

    async def read(session):
        cost = (await session.execute(select(EvidenceSymbolCost))).scalar_one()
        hot = (await session.execute(select(SymbolHotness))).scalar_one()
        return cost, hot

    cost, hot = _db(root, read)
    assert (cost.lineage_id, cost.body_digest) == ("L" * 16, "d" * 32)
    assert (hot.symbol_id, hot.freshness, hot.rank, hot.tier) == (
        "src/app.py::build", "current", 1, "lead_candidate",
    )
    # Ranked by its cost (self samples), with the run's split beside it.
    assert (hot.samples, hot.run_samples, hot.repo_self_share) == (40, 100, 0.45)
    assert (hot.wait_share, hot.library_share) == (0.3, 0.1)


def test_code_outside_production_is_counted_before_the_cap(indexed) -> None:
    root, head = indexed
    (root / ".repowise" / "config.yaml").write_text("evidence:\n  max_symbols_per_run: 1\n")

    async def a_test(session) -> None:
        (await session.get(Repository, "r1")).head_commit = head
        session.add(WikiSymbol(
            repository_id="r1", file_path="tests/test_app.py",
            symbol_id="tests/test_app.py::test_build", name="test_build",
            qualified_name="test_build", kind="function", start_line=1, end_line=2,
        ))

    _db(root, a_test)
    path = _write(root, symbols=[
        {"path": "src/app.py", "function": "build", "line": 1, "self": 10, "total": 60},
        {"path": "tests/test_app.py", "function": "test_build", "line": 1, "self": 1,
         "total": 20},
    ])
    result = _add(str(path), "--workload", "w", "--class", "unit_test", "--commit", head,
                  "--format", "json")
    assert result.exit_code == 0, result.output
    assert _json(result)["truncated"] == 1

    async def read(session):
        run = (await session.execute(select(EvidenceRun))).scalar_one()
        hot = (await session.execute(select(SymbolHotness))).scalar_one()
        return run, hot

    run, hot = _db(root, read)
    # The test function was cut by the cap and is still counted.
    assert run.unlisted_symbols == 1
    assert (hot.symbol_id, hot.run_unlisted) == ("src/app.py::build", 1)


def test_an_uppercase_commit_is_stored_lowercase(indexed) -> None:
    root, head = indexed
    result = _add(str(_write(root)), "--workload", "w", "--class", "entry_point",
                  "--commit", head.upper(), "--format", "json")
    assert result.exit_code == 0, result.output
    assert _json(result)["dirty"] is False  # read as HEAD, so the tree was checked
    (run,), _ = _stored(root)
    assert run.commit_sha == head


def test_production_is_never_set_by_a_flag(indexed) -> None:
    root, head = indexed
    result = _add(str(_write(root)), "--workload", "w", "--class", "production",
                  "--commit", head)
    assert result.exit_code == 2
    assert "verified source" in result.stderr
    assert _stored(root) == ([], [])


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--class", "entry_point"], "which commit was measured"),
        (["--class", "entry_point", "--commit", "abc"], "40- or 64-character"),
        (["--class", "smoke"], "is not one of"),
        (["--class", "entry_point", "--commit", "b" * 40, "--environment", "my laptop"],
         "one word"),
    ],
)
def test_usage_errors_exit_2(indexed, args, message) -> None:
    root, _head = indexed
    result = _add(str(_write(root)), "--workload", "w", *args)
    assert result.exit_code == 2
    assert message in result.stderr


def test_a_file_commit_that_disagrees_with_the_flag_is_refused(indexed) -> None:
    root, head = indexed
    result = _add(str(_write(root, commit_sha="c" * 40)), "--workload", "w",
                  "--class", "entry_point", "--commit", head)
    assert result.exit_code == 2
    assert "pass the same commit" in result.stderr


def test_invalid_files_are_refused_as_json(indexed) -> None:
    root, head = indexed
    bad = root / "bad.json"
    bad.write_text(json.dumps({"format": "repowise-evidence-v1", "kind": "trace"}))
    result = _add(str(bad), "--workload", "w", "--class", "entry_point", "--commit", head,
                  "--format", "json")
    assert result.exit_code == 1
    assert _json(result)["error"] == "invalid_evidence"


def test_a_profile_of_bundled_code_is_refused_against_the_index(indexed) -> None:
    root, head = indexed
    (root / "src" / "bundle.js").write_text("function a() {}\n//# sourceMappingURL=bundle.js.map\n")
    url = (root / "src" / "bundle.js").as_uri()
    profile = root / "app.cpuprofile"
    profile.write_text(json.dumps({
        "nodes": [
            {"id": 1, "callFrame": {"functionName": "(root)", "url": "", "lineNumber": -1,
                                    "columnNumber": -1}, "children": [2]},
            {"id": 2, "callFrame": {"functionName": "a", "url": url, "lineNumber": 0,
                                    "columnNumber": 10}},
        ],
        "startTime": 0, "endTime": 2000, "samples": [2], "timeDeltas": [1000],
    }))
    result = _add(str(profile), "--workload", "w", "--class", "entry_point", "--commit", head,
                  "--format", "json")
    assert result.exit_code == 1
    out = _json(result)
    assert out["error"] == "invalid_evidence" and "source map" in out["message"]
    assert _stored(root) == ([], [])


@pytest.mark.parametrize("given", [(), ("--input-format", "pprof")])
def test_an_empty_profile_is_named_as_such(indexed, given) -> None:
    # A test binary that panicked leaves a zero-byte profile behind.
    root, head = indexed
    empty = root / "cpu.pprof"
    empty.write_bytes(b"")
    result = _add(str(empty), "--workload", "w", "--class", "entry_point", "--commit", head,
                  *given, "--format", "json")
    assert result.exit_code == 1
    out = _json(result)
    assert out["error"] == "invalid_evidence" and "is empty" in out["message"]
    assert "crash" in out["remedy"] and "JSON object" not in out["message"]


def test_entries_in_files_edited_since_the_commit_are_skipped(indexed) -> None:
    root, head = indexed
    (root / "src" / "edited.py").write_text("def f():\n    return 1\n")
    result = _add(
        str(_write(root, symbols=[
            {"path": "src/app.py", "function": "build", "total": 5},
            {"path": "src/edited.py", "function": "f", "total": 5},
        ])),
        "--workload", "w", "--class", "unit_test", "--commit", head, "--format", "json",
    )
    payload = _json(result)
    assert payload["dirty"] is True
    assert payload["skipped"]["dirty_file"] == 1


def test_another_commit_is_kept_and_reads_stale(indexed) -> None:
    root, _head = indexed
    other = "d" * 40
    result = _add(str(_write(root)), "--workload", "w", "--class", "benchmark",
                  "--commit", other, "--format", "json")
    payload = _json(result)
    assert (payload["stored"], payload["stale"], payload["dirty"]) == (True, True, None)
    (run,), _ = _stored(root)
    assert (run.commit_sha, run.dirty) == (other, None)


def test_a_mostly_unmapped_file_stores_nothing_unless_allowed(indexed) -> None:
    root, head = indexed
    path = _write(root, symbols=[
        {"path": "src/app.py", "function": "build", "total": 5},
        {"path": "lib/a.py", "function": "x", "total": 5},
        {"path": "lib/b.py", "function": "y", "total": 5},
    ])
    refused = _add(str(path), "--workload", "w", "--class", "entry_point", "--commit", head,
                   "--format", "json")
    assert refused.exit_code == 1
    assert _json(refused)["reason"] == "mostly_unmapped"
    assert _stored(root) == ([], [])

    allowed = _add(str(path), "--workload", "w", "--class", "entry_point", "--commit", head,
                   "--allow-partial", "--format", "json")
    assert allowed.exit_code == 0
    (run,), _ = _stored(root)
    assert run.mapping_partial is True


def test_a_file_over_the_size_cap_is_refused_before_it_is_read(indexed) -> None:
    root, head = indexed
    (root / ".repowise" / "config.yaml").write_text("evidence:\n  max_file_mb: 0.0001\n")
    result = _add(str(_write(root)), "--workload", "w", "--class", "entry_point",
                  "--commit", head, "--format", "json")
    assert result.exit_code == 1
    refusal = _json(result)
    assert refusal["error"] == "file_too_large"
    assert "repowise config set evidence.max_file_mb 1" in refusal["remedy"]


def _status(*args: str):
    return CliRunner(mix_stderr=False).invoke(evidence_cmd.evidence_group, ["status", *args])


def test_status_names_the_newest_run_of_each_workload(indexed) -> None:
    root, head = indexed
    empty = _json(_status("--format", "json"))
    assert (empty["indexed"], empty["workloads"]) == (True, [])

    for workload in ("cli build", "cli build", "cli test"):
        fields = {"total": 200, "repo_self": 90} if workload == "cli test" else {}
        _add(str(_write(root, **fields)),
             "--workload", workload, "--class", "entry_point", "--commit", head)
    payload = _json(_status("--format", "json"))
    assert payload["runs_stored"] == {"profile": 2}
    assert [w["workload"] for w in payload["workloads"]] == ["cli test", "cli build"]
    newest = payload["workloads"][0]
    # The fixture's index row names no commit, so freshness is unknown, never current.
    assert newest["freshness"] == "unknown"
    assert (newest["repo_self_share"], newest["wait_share"]) == (0.45, 0.15)
    assert newest["units"] == {
        "total": 7, "matched": 1, "skipped": 1, "outside_repo": 5, "truncated": 0,
    }

    table = _status()
    assert table.exit_code == 0
    assert "repo code itself 45%, waits 15%, libraries 5% of 200 samples" in table.stdout


def test_status_json_keeps_its_keys_without_an_index(tmp_path, monkeypatch) -> None:
    async def empty_store() -> None:
        (tmp_path / ".repowise").mkdir()
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / '.repowise' / 'wiki.db'}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(empty_store())
    monkeypatch.setattr(evidence_cmd, "_resolve_repo", lambda _p, _n: tmp_path)
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)

    payload = _json(_status("--format", "json"))

    assert payload == {
        "repo": str(tmp_path), "indexed": False,
        "reference_commit": None, "runs_stored": {}, "workloads": [],
    }


@pytest.mark.parametrize(
    ("value", "shown"), [(200, "200"), (1234567, "1,234,567"), (1234567.0, "1,234,567"), (0.5, "0.50")]
)
def test_status_total_never_uses_exponent_form(value, shown) -> None:
    assert evidence_cmd._total(value) == shown


def _collapsed(root: Path, other: Path) -> Path:
    path = root / "stacks.txt"
    path.write_text(
        "\n".join([
            f"main (/usr/lib/python3/runpy.py:5);build ({root / 'src' / 'app.py'}:2) 6",
            f"main (/usr/lib/python3/runpy.py:5);build ({other / 'src' / 'app.py'}:2) 3",
            f"main (/usr/lib/python3/runpy.py:5);gone ({root / 'src' / 'app.py'}:2) 1",
        ])
    )
    return path


def test_a_collapsed_profile_is_converted_and_stored(indexed, tmp_path_factory) -> None:
    root, head = indexed
    other = tmp_path_factory.mktemp("other-checkout")
    result = _add(
        str(_collapsed(root, other)), "--workload", "cli build", "--class", "entry_point",
        "--commit", head, "--value-kind", "cpu", "--format", "json",
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["input_format"], payload["matched"]) == ("collapsed", 1)
    assert payload["skipped"] == {"outside_repo": 2, "second_signal_mismatch": 1}
    conversion = payload["conversion"]
    assert (conversion["total"], conversion["unit"]) == (10.0, "samples")
    assert conversion["explained_share"] == 0.6
    # runpy only sits above the repo frame: all of build's time is repo code itself.
    assert (conversion["repo_self_share"], conversion["wait_share"]) == (0.6, 0.0)
    assert conversion["skip_rate"] == round(1 / 7, 4)

    (run,), (cost,) = _stored(root)
    assert (run.value_kind, run.unit, run.total_value, run.explained_value) == (
        "cpu", "samples", 10.0, 6.0,
    )
    assert (run.repo_self_value, run.wait_value, run.library_value) == (6.0, 0.0, 0.0)
    assert (cost.symbol_id, cost.total_value, cost.self_value, cost.total_samples) == (
        "src/app.py::build", 6.0, 6.0, 6,
    )


def test_a_converted_profile_needs_its_clock(indexed, tmp_path_factory) -> None:
    root, head = indexed
    other = tmp_path_factory.mktemp("other-checkout")
    result = _add(
        str(_collapsed(root, other)), "--workload", "w", "--class", "entry_point",
        "--commit", head,
    )
    assert result.exit_code == 2
    assert "--value-kind" in result.stderr
    assert _stored(root) == ([], [])


def test_input_format_is_checked_against_the_registry(indexed) -> None:
    root, head = indexed
    result = _add(
        str(_write(root)), "--workload", "w", "--class", "entry_point", "--commit", head,
        "--input-format", "nosuch",
    )
    assert result.exit_code == 2
    assert all(name in result.stderr for name in ("speedscope", "pprof", "pytest-benchmark", "collapsed"))


def test_input_format_overrides_the_sniff(indexed) -> None:
    root, head = indexed
    result = _add(
        str(_write(root)), "--workload", "w", "--class", "entry_point", "--commit", head,
        "--input-format", "speedscope", "--format", "json",
    )
    assert result.exit_code == 1
    assert _json(result)["error"] == "invalid_evidence"


def test_a_gzipped_pprof_profile_is_sniffed_and_keeps_its_own_clock(indexed) -> None:
    root, head = indexed
    data = _pprof(
        samples=[([2, 1], [4, 40_000_000]), ([1], [1, 10_000_000])],
        locations={1: [(1, 0)], 2: [(2, 2)]},
        functions={1: ("runtime.main", "/usr/local/go/src/runtime/proc.go", 1),
                   2: ("build", str(root / "src" / "app.py"), 1)},
    )
    path = root / "cpu.pprof"
    path.write_bytes(gzip.compress(data))
    result = _add(
        str(path), "--workload", "w", "--class", "entry_point", "--commit", head,
        "--format", "json",
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["input_format"], payload["matched"]) == ("pprof", 1)
    assert payload["conversion"]["unit"] == "ns"
    assert payload["conversion"]["explained_share"] == 0.8
    (run,), (cost,) = _stored(root)
    assert (run.value_kind, run.total_value) == ("cpu", 50_000_000)
    assert (cost.symbol_id, cost.total_samples) == ("src/app.py::build", 4)


_BENCH_FIXTURE = Path(__file__).parents[2] / "fixtures" / "evidence" / "pytest_benchmark.json"


def _index_benchmarks(root: Path) -> None:
    async def add() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                path = "benchmarks/test_speed.py"
                for sid, name, line in (
                    ("test_sum", "test_sum", 8),
                    ("TestCache::test_get", "test_get", 13),
                    ("test_sized", "test_sized", 18),
                ):
                    session.add(
                        WikiSymbol(
                            repository_id="r1", file_path=path, symbol_id=f"{path}::{sid}",
                            name=name, qualified_name="benchmarks.test_speed."
                            + sid.replace("::", "."), kind="function", start_line=line,
                            end_line=line + 1,
                        )
                    )
        finally:
            await engine.dispose()

    asyncio.run(add())


def _bench_file(root: Path, commit: str) -> Path:
    data = json.loads(_BENCH_FIXTURE.read_text())
    data["commit_info"]["id"] = commit
    path = root / "bench.json"
    path.write_text(json.dumps(data, indent=4))
    return path


def _benchmarks(root: Path) -> list:
    async def read() -> list:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                return list((await session.execute(select(EvidenceBenchmarkResult))).scalars())
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_pytest_benchmark_output_is_stored_as_a_benchmark_series(indexed) -> None:
    root, head = indexed
    _index_benchmarks(root)
    result = _add(
        str(_bench_file(root, head)), "--workload", "pytest bench", "--class", "benchmark",
        "--commit", head, "--format", "json",
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["input_format"], payload["kind"], payload["matched"]) == (
        "pytest-benchmark", "benchmark", 4,
    )
    assert (payload["skipped"], payload["unlinked"], payload["dirty"]) == ({}, {}, False)
    assert "conversion" not in payload

    (run,), costs = _stored(root)
    assert costs == []
    assert (run.kind, run.workload_class, run.unit, run.tool_name) == (
        "benchmark", "benchmark", None, "pytest-benchmark",
    )
    rows = {r.benchmark_key: r for r in _benchmarks(root)}
    assert {k: r.symbol_id for k, r in rows.items()} == {
        "benchmarks/test_speed.py::test_sum": "benchmarks/test_speed.py::test_sum",
        "benchmarks/test_speed.py::TestCache::test_get":
            "benchmarks/test_speed.py::TestCache::test_get",
        "benchmarks/test_speed.py::test_sized[10]": "benchmarks/test_speed.py::test_sized",
        "benchmarks/test_speed.py::test_sized[20]": "benchmarks/test_speed.py::test_sized",
    }
    get = rows["benchmarks/test_speed.py::TestCache::test_get"]
    stats = json.loads(_BENCH_FIXTURE.read_text())["benchmarks"][1]["stats"]
    assert (get.unit, get.median, get.rounds, get.iterations) == (
        "s", stats["median"], stats["rounds"], stats["iterations"],
    )


def test_criterion_output_is_stored_using_its_sibling_benchmark_id(indexed) -> None:
    root, head = indexed
    fixture = _BENCH_FIXTURE.parent / "criterion/sort/32/new"
    path = root / "renamed-estimates.json"
    path.write_bytes((fixture / "estimates.json").read_bytes())
    path.with_name("benchmark.json").write_bytes((fixture / "benchmark.json").read_bytes())

    result = _add(
        str(path), "--workload", "rust bench", "--class", "benchmark",
        "--commit", head, "--format", "json",
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["stored"], payload["input_format"], payload["kind"], payload["rows_written"]) == (
        True, "criterion", "benchmark", 1,
    )
    (row,) = _benchmarks(root)
    assert (row.benchmark_key, row.unit, row.lower_is_better, row.symbol_id) == (
        "sort/32", "ns", True, None,
    )
    point_estimates = json.loads(path.read_bytes())
    assert (row.mean, row.median, row.stddev) == tuple(
        point_estimates[key]["point_estimate"] for key in ("mean", "median", "std_dev")
    )


def test_a_benchmark_function_not_indexed_keeps_its_series(indexed) -> None:
    root, head = indexed
    result = _add(
        str(_bench_file(root, head)), "--workload", "w", "--class", "benchmark",
        "--commit", head, "--format", "json",
    )
    assert result.exit_code == 0, result.output
    assert _json(result)["unlinked"] == {"unknown_file": 4}
    assert {r.symbol_id for r in _benchmarks(root)} == {None}


def test_pytest_benchmark_needs_the_commit_flag_and_cross_checks_it(indexed) -> None:
    root, head = indexed
    path = _bench_file(root, head)
    result = _add(str(path), "--workload", "w", "--class", "benchmark")
    assert result.exit_code == 2
    assert "--commit" in result.stderr and "commit_sha in the file" not in result.stderr

    other = "e" * 40
    result = _add(str(path), "--workload", "w", "--class", "benchmark", "--commit", other)
    assert result.exit_code == 2
    assert f"records commit {head}" in result.stderr
    assert _stored(root) == ([], [])


def test_value_kind_on_a_benchmark_file_says_it_has_no_clock(indexed) -> None:
    root, head = indexed
    result = _add(
        str(_bench_file(root, head)), "--workload", "w", "--class", "benchmark",
        "--commit", head, "--value-kind", "wall",
    )
    assert result.exit_code == 2
    assert "records no clock" in result.stderr
    assert _stored(root) == ([], [])


def test_a_benchmark_never_links_to_a_method_sharing_its_bare_name(indexed) -> None:
    root, head = indexed
    _index_benchmarks(root)
    data = json.loads(_bench_file(root, head).read_text())
    bench = data["benchmarks"][0]
    bench["name"], bench["fullname"] = "test_get", "benchmarks/test_speed.py::test_get"
    data["benchmarks"] = [bench]
    path = root / "bench.json"
    path.write_text(json.dumps(data, indent=4))
    result = _add(
        str(path), "--workload", "w", "--class", "benchmark", "--commit", head, "--format", "json",
    )
    assert result.exit_code == 0, result.output
    assert _json(result)["unlinked"] == {"unknown_symbol": 1}
    assert [r.symbol_id for r in _benchmarks(root)] == [None]


_GO_BENCH = Path(__file__).parents[2] / "fixtures" / "go_bench"


def _go_bench_repo(root: Path) -> str:
    """The Go fixture module committed into *root*, its benchmarks indexed; the new HEAD."""
    import shutil

    import git as gitpython

    shutil.copy(_GO_BENCH / "go.mod", root / "go.mod")
    shutil.copytree(_GO_BENCH / "strs", root / "strs")
    repo = gitpython.Repo(root)
    repo.index.add(["go.mod", "strs/join.go", "strs/join_test.go"])
    head = repo.index.commit("go").hexsha
    repo.close()

    async def add() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                path = "strs/join_test.go"
                session.add(HealthFileMetric(repository_id="r1", file_path=path, code_origin="test"))
                for name, line in (("BenchmarkJoin", 10), ("BenchmarkConcat", 16)):
                    session.add(
                        WikiSymbol(
                            repository_id="r1", file_path=path, symbol_id=f"{path}::{name}",
                            name=name, qualified_name=name, kind="function", start_line=line,
                            end_line=line + 5,
                        )
                    )
        finally:
            await engine.dispose()

    asyncio.run(add())
    return head


def test_go_bench_output_is_stored_with_exact_allocations(indexed) -> None:
    root, _ = indexed
    head = _go_bench_repo(root)
    result = _add(
        str(_GO_BENCH / "bench.txt"), "--workload", "go bench results ./strs",
        "--class", "benchmark", "--commit", head, "--format", "json",
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["input_format"], payload["kind"], payload["matched"], payload["unlinked"]) == (
        "go-bench", "benchmark", 3, {},
    )
    rows = {r.benchmark_key: r for r in _benchmarks(root)}
    join = rows["example.com/gobench/strs.BenchmarkJoin"]
    assert (join.symbol_id, join.unit, join.rounds) == ("strs/join_test.go::BenchmarkJoin", "ns", 2)
    assert (join.allocs_per_op, join.bytes_per_op) == (1, 24)
    concat = rows["example.com/gobench/strs.BenchmarkConcat/n=5"]
    assert (concat.symbol_id, concat.allocs_per_op) == ("strs/join_test.go::BenchmarkConcat", 4)


def test_a_red_suites_profile_is_stored_with_its_failed_count(indexed) -> None:
    root, head = indexed
    path = root / "suite.speedscope.json"
    path.write_text(json.dumps({
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "exporter": "pyinstrument@5.1.3",
        "shared": {"frames": [{"name": "build", "file": str(root / "src" / "app.py"), "line": 1}]},
        "profiles": [{"type": "sampled", "unit": "seconds", "samples": [[0], [0]], "weights": [0.5, 0.5]}],
        "testsFailed": 2,
    }))

    result = _add(str(path), "--workload", "python tests", "--class", "unit_test",
                  "--commit", head, "--format", "json")

    assert result.exit_code == 0, result.output
    assert _json(result)["tests_failed"] == 2
    (run,), _ = _stored(root)
    assert run.tests_failed == 2
    assert "2 tests failed" in _status().stdout


def _speedscope(root: Path, seconds: float) -> Path:
    path = root / "app.speedscope.json"
    path.write_text(json.dumps({
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "exporter": "pyinstrument@5.1.3",
        "shared": {"frames": [{"name": "build", "file": str(root / "src" / "app.py"), "line": 1}]},
        "profiles": [{"type": "sampled", "unit": "seconds", "samples": [[0], [0]],
                      "weights": [seconds / 2, seconds / 2]}],
    }))
    return path


@pytest.mark.parametrize(("seconds", "warned"), [(0.4, True), (12.0, False)])
def test_a_short_entry_point_run_is_stored_with_a_warning(indexed, seconds, warned) -> None:
    root, head = indexed

    result = _add(str(_speedscope(root, seconds)), "--workload", "app update", "--class",
                  "entry_point", "--commit", head)

    assert result.exit_code == 0, result.output
    assert ("profile covers 0.4 s" in result.output) is warned
    assert len(_stored(root)[0]) == 1


def test_a_directory_of_junit_reports_is_one_run(indexed) -> None:
    root, head = indexed
    reports = root / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    for name in ("A", "B"):
        (reports / f"TEST-com.acme.{name}Test.xml").write_text(
            f'<testsuite name="com.acme.{name}Test">'
            f'<testcase classname="com.acme.{name}Test" name="works()" time="0.5"/></testsuite>'
        )
    (reports / "com.acme.ATest.txt").write_text("not read")

    result = _add(
        str(reports), "--workload", "maven tests", "--class", "unit_test",
        "--commit", head, "--format", "json",
    )

    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["input_format"], payload["kind"], payload["entries"]) == ("junit", "test_timing", 2)


def test_several_profiles_and_a_directory_merge_into_one_tagged_run(
    indexed, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.evidence.test_add import _parent, _worker

    root, head = indexed
    monkeypatch.setenv("CI", "true")
    workers = root / "workers"
    workers.mkdir()
    (workers / "w1.speedscope.json").write_bytes(_worker(root, 6.0))
    (workers / "w2.speedscope.json").write_bytes(_worker(root, 2.0))
    parent = root / "parent.speedscope.json"
    parent.write_bytes(_parent(root))

    result = _add(str(parent), str(workers), "--workload", "app", "--class", "entry_point",
                  "--commit", head, "--format", "json")

    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert (payload["profiles"], payload["conversion"]["total"]) == (3, 12.0)
    assert payload["environment"].endswith("-ci") and "warnings" not in payload
    (run,), _ = _stored(root)
    assert run.environment == payload["environment"]

    text = _add(str(parent), str(workers), "--workload", "app", "--class", "entry_point",
                "--commit", head)
    assert "3 profiles merged into one run" in text.output


def test_a_parent_profile_alone_warns_its_workers_were_not_profiled(indexed) -> None:
    from tests.unit.evidence.test_add import _parent

    root, head = indexed
    parent = root / "parent.speedscope.json"
    parent.write_bytes(_parent(root))

    result = _add(str(parent), "--workload", "app", "--class", "entry_point", "--commit", head)

    assert result.exit_code == 0, result.output
    assert "75% of this run went to starting or waiting on other processes" in result.output


def test_mixed_formats_in_one_run_are_refused_as_json(indexed) -> None:
    from tests.unit.evidence.test_add import _parent

    root, head = indexed
    parent = root / "parent.speedscope.json"
    parent.write_bytes(_parent(root))
    stacks = root / "stacks.txt"
    stacks.write_text(f"build ({root / 'src' / 'app.py'}:1) 5\n")

    result = _add(str(parent), str(stacks), "--workload", "app", "--class", "entry_point",
                  "--commit", head, "--value-kind", "wall", "--format", "json")

    assert result.exit_code == 1, result.output
    assert "different formats" in result.stdout
    assert _stored(root) == ([], [])


def _tests(*args: str):
    return CliRunner(mix_stderr=False).invoke(evidence_cmd.evidence_group, ["tests", *args])


def test_tests_lists_the_slowest_tests_of_the_stored_junit_run(indexed) -> None:
    root, head = indexed
    assert _json(_tests("--format", "json"))["status"] == "no_test_timings"
    report = root / "junit.xml"
    report.write_text(
        '<testsuite name="pytest">'
        '<testcase classname="tests.test_app" name="test_fast" time="0.25"/>'
        '<testcase classname="tests.test_app" name="test_slow" time="4.5"/>'
        "</testsuite>"
    )
    added = _add(str(report), "--workload", "pytest", "--class", "unit_test", "--commit", head)
    assert added.exit_code == 0, added.output

    payload = _json(_tests("--format", "json", "--limit", "1"))

    assert payload["status"] == "measured"
    (slowest,) = payload["slow_tests"]
    assert (slowest["test_id"].endswith("::test_slow"), slowest["duration_s"]) == (True, 4.5)
    assert payload["tests_total"] == 2
    assert payload["cost"]["verdict"] == "small_suite"
    table = _tests()
    assert table.exit_code == 0
    # A suite under a minute is one line: no list to read.
    assert table.stdout.strip() == (
        "Tests take 4.8 s in all (2 timed): under a minute, nothing to speed up."
    )


def test_a_capped_junit_run_keeps_its_slowest_tests_and_folds_them_all(indexed) -> None:
    root, head = indexed
    (root / ".repowise" / "config.yaml").write_text("evidence:\n  max_rows_per_run: 1\n")
    report = root / "junit.xml"
    report.write_text(
        '<testsuite name="pytest">'
        '<testcase classname="tests.test_app" name="test_fast" time="2.5"/>'
        '<testcase classname="tests.test_app" name="test_slow" time="45"/>'
        '<testcase classname="tests.test_app" name="test_mid" time="12.5"/>'
        "</testsuite>"
    )
    added = _add(str(report), "--workload", "pytest", "--class", "unit_test", "--commit", head,
                 "--format", "json")
    assert added.exit_code == 0, added.output
    assert _json(added)["truncated"] == 2

    payload = _json(_tests("--format", "json"))

    # The report lists the slow test second; the cap keeps it, not the first.
    assert [t["test_id"].rsplit("::", 1)[1] for t in payload["slow_tests"]] == ["test_slow"]
    suite = payload["suite"]
    assert (suite["tests"], suite["timed"], suite["seconds"]) == (3, 3, 60.0)
    # A minute of tests and no per-test map: the slow list, and what the cap left out.
    assert "2 faster or untimed tests were left out at the per-run cap" in _tests().stdout



@pytest.mark.parametrize("elsewhere", [False, True])
def test_a_vitest_report_below_the_root_lands_on_the_units_test_files(
    indexed, tmp_path_factory: pytest.TempPathFactory, elsewhere: bool
) -> None:
    from repowise.core.persistence.models import EvidenceTestTiming

    root, head = indexed
    # The run may come from another checkout of the repository (--checkout-root).
    checkout = tmp_path_factory.mktemp("checkout") if elsewhere else root

    async def index_test_file() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                session.add(HealthFileMetric(repository_id="r1", file_path="web/src/cart.test.ts",
                                             code_origin="test"))
        finally:
            await engine.dispose()

    asyncio.run(index_test_file())
    # Vitest ran in web/ and names its files from there.
    (checkout / "web").mkdir()
    (checkout / "web" / "junit.xml").write_text(
        '<testsuites><testsuite name="src/cart.test.ts">'
        '<testcase classname="src/cart.test.ts" name="cart &gt; adds" time="0.5"/></testsuite></testsuites>'
    )

    where = ["--checkout-root", str(checkout)] if elsewhere else []
    result = _add(str(checkout / "web" / "junit.xml"), "--workload", "web: vitest tests", "--class",
                  "unit_test", "--commit", head, *where)

    assert result.exit_code == 0, result.output

    async def stored() -> list:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                return list((await session.execute(select(EvidenceTestTiming))).scalars())
        finally:
            await engine.dispose()

    (row,) = asyncio.run(stored())
    assert (row.test_id, row.test_file) == ("web/src/cart.test.ts::cart > adds", "web/src/cart.test.ts")


_NO_MAP = {"verdict": "no_test_map", "seconds": 90.0, "timed": 4,
           "concentration": {"tests": 1, "share": 0.5}, "clusters": [],
           "command": "pytest --cov --cov-context=test"}


def test_tests_leads_with_the_first_group_what_it_costs_and_its_fixture() -> None:
    cluster = {
        "files": ["src/db.py", "src/store.py"], "files_total": 4, "tests": 29,
        "mean_s": 1.44, "others_mean_s": 0.09, "excess_s": 39.2, "share": 0.55,
        "slowest": [{"test_id": "tests/unit/test_add.py::test_a", "duration_s": 3.73}],
        "fixture": {"name": "indexed", "file_path": "tests/unit/test_add.py", "line": 48,
                    "tests": 15, "others": 0},
        "within": {"files": ["src/plugins.py"], "files_total": 1, "tests": 161},
    }
    histogram = [{"le": le, "tests": n, "seconds": 0.0}
                 for le, n in zip([0.001, 0.01, 0.1, 1.0, 10.0, 100.0, None], [0, 0, 311, 39, 22, 0, 0], strict=True)]
    payload = {
        "runs": [], "tests_total": 372, "slow_tests": [],
        "suite": {"timed": 372, "seconds": 71.0, "histogram": histogram},
        "cost": {"verdict": "clusters", "seconds": 71.0, "timed": 372,
                 "concentration": {"tests": 37, "share": 0.735}, "clusters": [cluster], "command": None},
    }

    lines = evidence_cmd._tests_lines(payload)

    assert lines[0] == (
        "[bold]Start with the 29 tests that run src/db.py and 3 more files[/bold]: they take "
        "39.2 s more than the rest's pace (55% of the suite), 15 of them through fixture "
        "[cyan]indexed[/cyan] (tests/unit/test_add.py:48)."
    )
    assert lines[1] == (
        "Tests take 71.0 s (372 timed); the slowest 37 take 74% of it. "
        "[dim]By duration: 311 at 10-100 ms, 39 at 0.1-1 s, 22 at 1-10 s.[/dim]"
    )
    text = "\n".join(lines)
    assert "29 tests at 1.44 s each vs 0.09 s: +39.2 s (55%)" in text
    assert "run: src/db.py, src/store.py and 2 more files" in text
    assert "inside the 161 tests that run src/plugins.py" in text
    assert "asked for by 15 of them and no other timed test" in text
    assert "slowest: test_add.py::test_a 3.73 s" in text


def test_tests_without_a_map_says_how_to_record_one() -> None:
    payload = {"runs": [], "tests_total": 4, "slow_tests": [], "suite": None, "cost": _NO_MAP}

    text = " ".join(evidence_cmd._tests_lines(payload))

    assert "Tests take 90.0 s (4 timed); the slowest 1 take 50% of it." in text
    assert "record a per-test coverage map: [cyan]pytest --cov --cov-context=test[/cyan]" in text


def test_tests_with_no_group_worth_a_list_is_one_line() -> None:
    payload = {"runs": [], "tests_total": 4, "slow_tests": [{"test_id": "t", "duration_s": 30.0}],
               "cost": {**_NO_MAP, "verdict": "small_excess", "command": None}}

    assert evidence_cmd._tests_lines(payload) == [
        "Tests take 90.0 s (4 timed), spread evenly: no group of tests runs 5 s over the rest."
    ]


def test_tests_says_how_many_the_cap_left_out_without_a_fold() -> None:
    run = {"workload": "pytest", "freshness": "current", "commit_sha": "a" * 40,
           "units": {"truncated": 7}}
    payload = {
        "runs": [run], "tests_total": 2, "slow_tests": [], "suite": None, "cost": _NO_MAP,
    }

    text = " ".join(evidence_cmd._tests_lines(payload))

    assert "7 faster or untimed tests were left out at the per-run cap." in text


def test_tests_names_a_suite_total_over_several_workloads_as_a_sum() -> None:
    def run(name: str) -> dict:
        return {"workload": name, "freshness": "current", "commit_sha": "a" * 40,
                "units": {"truncated": 1}}

    payload = {
        "runs": [run("pytest"), run("vitest")], "tests_total": 2, "slow_tests": [],
        "suite": {"timed": 4, "seconds": 9.0, "histogram": []}, "cost": _NO_MAP,
    }

    text = " ".join(evidence_cmd._tests_lines(payload))

    assert "the 4 timed tests take 9.0s, summed over 2 workloads." in text


_PRODUCTION = Path(__file__).parents[1] / "evidence" / "fixtures" / "production"


def test_a_statement_export_is_stored_as_production_with_no_class(indexed) -> None:
    root, _head = indexed
    csv = root / "statements.csv"
    csv.write_bytes((_PRODUCTION / "pg_stat_statements.csv").read_bytes())

    result = _add(str(csv), "--workload", "prod db")

    assert result.exit_code == 0, (result.output, result.exception)
    assert "in production" in result.stderr + result.stdout
    assert "unknown deployed commit" in result.stderr + result.stdout
    (run,), _ = _stored(root)
    # Production ran where users are, not on the machine adding the file.
    assert (run.kind, run.workload_class, run.environment) == (
        "statement_stats", "production", "production",
    )


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--class", "production"], "never from --class"),
        (["--class", "load_test"], "production by what it measures"),
    ],
)
def test_a_production_file_refuses_a_typed_class_in_one_line(indexed, args, message) -> None:
    root, _head = indexed
    path = root / "statements.json"
    path.write_bytes((_PRODUCTION / "statement_stats.json").read_bytes())

    result = _add(str(path), "--workload", "prod db", *args)

    assert result.exit_code == 2
    error = [line for line in result.stderr.splitlines() if line.startswith("Error")]
    assert len(error) == 1 and message in error[0]


def test_a_profile_still_needs_its_class(indexed) -> None:
    root, head = indexed
    result = _add(str(_write(root)), "--workload", "w", "--commit", head)
    assert result.exit_code == 2
    assert "name the workload class" in result.stderr
    assert _stored(root) == ([], [])


def test_add_and_status_compare_a_run_with_its_band(indexed) -> None:
    import git as gitpython

    root, _ = indexed
    base = _go_bench_repo(root)
    text = (_GO_BENCH / "bench.txt").read_text()
    workload = ("--workload", "go bench results ./strs", "--class", "benchmark")
    for i, ns in enumerate(("50.75", "50.80", "50.70")):
        path = root / f"band{i}.txt"
        path.write_text(text.replace("50.75 ns/op", f"{ns} ns/op"))
        result = _add(str(path), *workload, "--commit", base, "--format", "json")
        assert result.exit_code == 0, result.output
        # Runs of one commit have no earlier run to compare with.
        assert "compare" not in _json(result)
    repo = gitpython.Repo(root)
    head = repo.index.commit("strs: join twice").hexsha
    repo.close()
    slow = root / "slow.txt"
    slow.write_text(text.replace("56.20 ns/op", "156.2 ns/op").replace("50.75 ns/op", "150.7 ns/op"))

    added = _add(str(slow), *workload, "--commit", head, "--format", "json")

    assert added.exit_code == 0, added.output
    compared = _json(added)["compare"]
    assert (compared["verdict"], compared["band_runs"], compared["head_commit"]) == (
        "slower", 3, head,
    )
    assert compared["slower"][0]["name"] == "example.com/gobench/strs.BenchmarkJoin"
    assert compared["cause"]["commits_in_range"] == 1
    status = _json(_status("--compare", "--format", "json"))
    assert [c["verdict"] for c in status["compare"]] == ["slower"]
    assert "BenchmarkJoin 2.87x" in _status("--compare").stdout
    assert "compare" not in _json(_status("--format", "json"))
