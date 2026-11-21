"""``ingest_evidence``: what ``evidence add`` does, called without the command line."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.core.evidence.add import EvidenceRefusedError, ingest_evidence
from repowise.core.persistence import create_session_factory, get_session
from repowise.core.persistence.database import create_engine
from repowise.core.persistence.models import (
    EvidenceTestTiming,
    HealthFileMetric,
    Repository,
    WikiSymbol,
)
from tests.unit.cli.test_evidence_cmd import _git_repo, _index, _stored


def _speedscope(root: Path) -> bytes:
    return json.dumps({
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "exporter": "pyinstrument@5.1.3",
        "shared": {"frames": [{"name": "build", "file": str(root / "src" / "app.py"), "line": 1}]},
        "profiles": [{"type": "sampled", "unit": "seconds", "samples": [[0], [0]],
                      "weights": [2.0, 2.0]}],
    }).encode()


def _ingest(root: Path, data: bytes, **options) -> dict:
    async def run() -> dict:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await session.get(Repository, "r1")
                return await ingest_evidence(session, repo, data, repo_path=root, **options)
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[Path, str]:
    head = _git_repo(tmp_path)
    _index(tmp_path)
    return tmp_path, head


def test_an_uploaded_profile_is_converted_and_stored(indexed) -> None:
    root, head = indexed

    payload = _ingest(
        root, _speedscope(root), workload="app", workload_class="entry_point",
        commit=head.upper(), environment="ci", source="ci",
    )

    assert payload["stored"] is True
    assert (payload["kind"], payload["input_format"], payload["matched"]) == ("profile", "speedscope", 1)
    assert payload["commit_sha"] == head
    assert payload["conversion"]["total"] == 4.0
    (run,), costs = _stored(root)
    assert (run.workload_name, run.environment, run.source) == ("app", "ci", "ci")
    assert [c.symbol_id for c in costs] == ["src/app.py::build"]


@pytest.mark.parametrize(
    ("data", "options", "code", "option"),
    [
        (b"", {"workload_class": "production"}, "invalid_option", "--class"),
        (b"not evidence", {"workload_class": "entry_point"}, "invalid_evidence", None),
        (b"{}" * 600_000, {"workload_class": "entry_point", "max_file_mb": 1}, "file_too_large", None),
    ],
    ids=["production_class", "unreadable_file", "too_large"],
)
def test_a_refused_upload_raises_and_stores_nothing(indexed, data, options, code, option) -> None:
    root, head = indexed

    with pytest.raises(EvidenceRefusedError) as refused:
        _ingest(root, data, workload="app", commit=head, **options)

    assert (refused.value.code, refused.value.option) == (code, option)
    assert _stored(root) == ([], [])


def test_no_repository_row_reads_as_no_index(tmp_path: Path) -> None:
    async def run() -> dict:
        return await ingest_evidence(
            None, None, b"", repo_path=tmp_path, workload="app", workload_class="entry_point"
        )

    assert asyncio.run(run()) == {"stored": False, "reason": "no_index"}


def _index_test_file(root: Path) -> None:
    async def add() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                path = "tests/test_app.py"
                session.add(HealthFileMetric(repository_id="r1", file_path=path, code_origin="test"))
                session.add(
                    WikiSymbol(
                        repository_id="r1", file_path=path,
                        symbol_id=f"{path}::TestBuild::test_builds", name="test_builds",
                        qualified_name="TestBuild.test_builds", kind="method", start_line=2,
                        end_line=3,
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(add())


def _timings(root: Path) -> list:
    async def read() -> list:
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            async with get_session(create_session_factory(engine)) as session:
                rows = await session.execute(select(EvidenceTestTiming))
                return sorted(rows.scalars(), key=lambda r: r.test_id)
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_a_junit_report_is_stored_as_test_timings_on_its_test_functions(indexed) -> None:
    root, head = indexed
    _index_test_file(root)
    report = b"""<testsuites><testsuite name="pytest">
    <testcase classname="tests.test_app.TestBuild" name="test_builds[x]" time="2.5" />
    <testcase classname="tests.test_app.TestBuild" name="test_gone" time="0.1" />
    <testcase classname="nowhere.Mod" name="test_y" time="0.2" />
    </testsuite></testsuites>"""

    payload = _ingest(root, report, workload="pytest tests", workload_class="unit_test", commit=head)

    assert payload["stored"] is True
    assert (payload["kind"], payload["input_format"], payload["matched"]) == ("test_timing", "junit", 3)
    assert payload["unlinked"] == {"unknown_symbol": 1, "unplaced": 1}
    assert [(r.test_id, r.test_file, r.symbol_id, r.duration_s, r.first_in_class) for r in _timings(root)] == [
        ("nowhere.Mod::test_y", None, None, 0.2, True),
        ("tests/test_app.py::TestBuild::test_builds[x]", "tests/test_app.py",
         "tests/test_app.py::TestBuild::test_builds", 2.5, True),
        ("tests/test_app.py::TestBuild::test_gone", "tests/test_app.py", None, 0.1, False),
    ]


# ---------------------------------------------------------------------------
# Several profiles of one run, the environment tag, unprofiled workers
# ---------------------------------------------------------------------------

_POOL = "/usr/lib/python3.12/multiprocessing/pool.py"


def _sampled(root: Path, stacks: list[list[str]], weights: list[float], **top) -> bytes:
    """A speedscope profile whose frames are ``build`` in the repo or a stdlib path."""
    files = sorted({f for stack in stacks for f in stack})
    frames = [
        {"name": "build", "file": str(root / "src" / "app.py"), "line": 1} if f == "build"
        else {"name": "ApplyResult.get", "file": f, "line": 1}
        for f in files
    ]
    return json.dumps({
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "exporter": "pyinstrument@5.1.3",
        "shared": {"frames": frames},
        "profiles": [{"type": "sampled", "unit": "seconds",
                      "samples": [[files.index(f) for f in s] for s in stacks],
                      "weights": weights}],
        **top,
    }).encode()


def _parent(root: Path) -> bytes:
    # build hands its work to a pool and waits on it.
    return _sampled(root, [["build", _POOL], ["build"]], [3.0, 1.0])


def _worker(root: Path, seconds: float = 6.0) -> bytes:
    return _sampled(root, [["build"]], [seconds])


def test_a_single_profile_that_waited_on_workers_is_stored_with_a_warning(indexed) -> None:
    root, head = indexed

    payload = _ingest(root, _parent(root), workload="app", workload_class="entry_point", commit=head)

    assert payload["stored"] is True and payload["profiles"] == 1
    assert payload["conversion"]["child_process_share"] == 0.75
    (warning,) = payload["warnings"]
    assert warning["code"] == "unprofiled_child_processes"
    assert warning["child_process_share"] == 0.75
    assert "75%" in warning["message"] and "evidence add" in warning["remedy"]


def test_a_worker_profile_idle_in_its_pool_gets_no_warning(indexed) -> None:
    root, head = indexed
    # The worker's idle stacks have no repo frame: parked in its pool, not waiting on a child.
    worker = _sampled(root, [["build"], [_POOL]], [4.0, 4.0])

    payload = _ingest(root, worker, workload="app", workload_class="entry_point", commit=head)

    assert payload["conversion"]["child_process_share"] == 0.0
    assert "warnings" not in payload


def test_a_parent_and_its_workers_merge_into_one_run(indexed) -> None:
    root, head = indexed
    files = [("parent.json", _parent(root)), ("w1.json", _worker(root)), ("w2.json", _worker(root, 2.0))]

    payload = _ingest(root, files, workload="app", workload_class="entry_point", commit=head)

    assert payload["stored"] is True
    assert (payload["profiles"], payload["conversion"]["total"]) == (3, 12.0)
    assert "warnings" not in payload
    (run,), (cost,) = _stored(root)
    assert run.total_value == 12.0
    # The pool wait is the parent's; build's own time is every process's.
    assert (cost.symbol_id, cost.self_value, cost.total_value) == ("src/app.py::build", 9.0, 12.0)

    # The same profiles named in another order are the same run.
    again = _ingest(root, files[::-1], workload="app", workload_class="entry_point", commit=head)
    assert again["restamped"] is True
    assert len(_stored(root)[0]) == 1


def test_a_merged_run_is_one_run_for_hotness(indexed) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.models import SymbolHotness
    from tests.unit.cli.test_evidence_cmd import _db

    root, head = indexed
    _ingest(root, [("p", _parent(root)), ("w", _worker(root))], workload="app",
            workload_class="entry_point", commit=head)

    async def read(session):
        return list((await session.execute(select(SymbolHotness))).scalars())

    (run,), _ = _stored(root)
    (row,) = _db(root, read)
    # One basis run carrying every process's samples, never two agreeing runs.
    assert (row.symbol_id, row.run_id, row.run_count) == ("src/app.py::build", run.id, 1)
    assert row.run_samples == 3


def _collapsed(root: Path) -> bytes:
    return f"build ({root / 'src' / 'app.py'}:1) 5\n".encode()


@pytest.mark.parametrize(
    ("files", "options", "says"),
    [
        (lambda r: [("a", _parent(r)), ("b", _collapsed(r))], {"value_kind": "wall"},
         "different formats"),
        (lambda r: [("a", _parent(r)), ("b", _sampled(r, [["build"]], [1.0], exporter="py-spy@0.3.14"))],
         {"value_kind": "wall"}, "different tools"),
        (lambda r: [("a", _parent(r)), ("b", _sampled(r, [["build"]], [1.0]).replace(
            b'"unit": "seconds"', b'"unit": "none"'))], {}, "different units"),
        (lambda r: [("a", _parent(r)), ("b", _parent(r))], {}, "byte-identical"),
        (lambda r: [("a", _speedscope(r)), ("b", json.dumps({
            "format": "repowise-evidence-v1", "kind": "profile", "value_kind": "wall",
            "unit": "samples", "total": 1, "symbols": []}).encode())], {}, "different formats"),
    ],
    ids=["formats", "tools", "units", "same_file", "v1_beside_a_profile"],
)
def test_profiles_that_do_not_add_up_are_refused_with_the_reason(indexed, files, options, says) -> None:
    root, head = indexed

    with pytest.raises(EvidenceRefusedError) as refused:
        _ingest(root, files(root), workload="app", workload_class="entry_point", commit=head, **options)

    assert refused.value.code == "invalid_evidence"
    assert says in str(refused.value)
    assert _stored(root) == ([], [])


def test_benchmark_results_do_not_merge(indexed) -> None:
    root, head = indexed

    def bench(name: str) -> bytes:
        return json.dumps({"machine_info": {}, "commit_info": {}, "benchmarks": [{
            "name": name, "fullname": f"tests/test_b.py::{name}", "stats": {
                "min": 1, "max": 1, "mean": 1, "median": 1, "stddev": 0, "rounds": 1,
                "iterations": 1, "ops": 1}}]}).encode()

    with pytest.raises(EvidenceRefusedError) as refused:
        _ingest(root, [("a", bench("test_a")), ("b", bench("test_b"))], workload="b",
                workload_class="benchmark", commit=head)

    assert "do not merge" in str(refused.value)


@pytest.mark.parametrize(
    ("env", "platform", "tag"),
    [
        ({}, "linux", "linux-local"),
        ({"GITHUB_ACTIONS": "true"}, "linux", "linux-ci"),
        ({"CI": "1"}, "darwin", "macos-ci"),
        ({"CI": "false"}, "win32", "windows-local"),
        ({}, "freebsd14", "freebsd-local"),
    ],
)
def test_the_environment_tag_is_the_os_and_ci_or_local(env, platform, tag) -> None:
    from repowise.core.evidence.add import check_options, detect_environment

    assert detect_environment(env, platform) == tag
    check_options("app", "entry_point", tag, None)


def test_a_directory_stands_for_the_files_in_it(tmp_path: Path) -> None:
    from repowise.core.evidence.add import evidence_paths

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    for name in ("b.cpuprofile", "a.cpuprofile", ".hidden"):
        (profiles / name).write_text("{}")
    (profiles / "nested").mkdir()

    got = evidence_paths([profiles, profiles / "a.cpuprofile"])

    assert [p.name for p in got] == ["a.cpuprofile", "b.cpuprofile"]
    (tmp_path / "empty").mkdir()
    with pytest.raises(EvidenceRefusedError) as refused:
        evidence_paths([tmp_path / "empty"])
    assert refused.value.option == "FILE"
