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
