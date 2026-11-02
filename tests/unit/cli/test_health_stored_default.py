"""Bare `repowise health` reads the stored analysis and writes nothing.

The in-process recompute parsed and analyzed the whole tree (minutes on a
large repository) and wrote its result back to the index. The default now
reads the rows the last `init`/`update` stored, as MCP and the web UI do;
`--recompute` keeps the in-process path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from repowise.cli.commands.health_cmd import command as health_cmd
from repowise.cli.commands.health_cmd import health_command
from repowise.cli.helpers import run_async

_PATHS = ("pkg/a.py", "pkg/b.py", "tests/test_a.py")


async def _store(repo_path: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        crud,
        get_session,
        init_db,
        upsert_repository,
    )

    engine = create_engine(f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            await crud.save_health_metrics(
                session,
                repo.id,
                [
                    {"file_path": p, "score": 4.0 + i, "nloc": 100, "max_ccn": 12,
                     "is_test": p.startswith("tests/")}
                    for i, p in enumerate(_PATHS)
                ],
            )
            await crud.save_health_findings(
                session,
                repo.id,
                [
                    {"file_path": "pkg/a.py", "biomarker_type": "complex_method",
                     "severity": "high", "function_name": "f", "line_start": 1, "line_end": 40,
                     "details": {"ccn": 16}, "health_impact": 2.0, "reason": "seeded",
                     "dimension": "defect"}
                ],
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = tmp_path.resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".repowise").mkdir()
    return root


def _read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if the command walks the tree or writes to the index."""
    import repowise.core.ingestion as ingestion
    from repowise.cli import helpers

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the stored read must not traverse, reconcile or persist")

    monkeypatch.setattr(ingestion, "FileTraverser", boom)
    monkeypatch.setattr(helpers, "reconcile_schema_best_effort", boom)
    monkeypatch.setattr(health_cmd, "_persist_health", boom)


def test_json_reports_the_stored_rows(repo, monkeypatch):
    run_async(_store(repo))
    _read_only(monkeypatch)

    result = CliRunner().invoke(health_command, [str(repo), "--format", "json", "--no-workspace"])

    assert result.exit_code == 0, result.output
    out = json.loads(result.output[result.output.index("{") :])
    assert {m["file_path"] for m in out["metrics"]} == set(_PATHS)
    assert out["kpis"]["file_count"] == len(_PATHS)
    assert [(f["file_path"], f["details"]) for f in out["findings"]] == [
        ("pkg/a.py", {"ccn": 16})
    ]


def test_the_table_reads_without_writing(repo, monkeypatch):
    run_async(_store(repo))
    _read_only(monkeypatch)

    result = CliRunner().invoke(health_command, [str(repo), "--no-workspace", "--scope", "production"])

    assert result.exit_code == 0, result.output
    assert "Lowest-scoring files (2)" in result.output
    assert "tests/test_a.py" not in result.output


def test_no_stored_analysis_names_both_ways_forward(repo, monkeypatch):
    _read_only(monkeypatch)

    result = CliRunner().invoke(health_command, [str(repo), "--no-workspace"])

    assert result.exit_code != 0
    assert "--recompute" in result.output


def test_the_table_scores_the_stored_rows_for_defect_accuracy(repo, monkeypatch):
    """The "does the score find the bugs?" line reads the same stored rows."""
    run_async(_store(repo))
    _read_only(monkeypatch)
    seen: list = []
    monkeypatch.setattr(health_cmd, "_render_defect_accuracy_line", seen.append)

    result = CliRunner().invoke(health_command, [str(repo), "--no-workspace"])

    assert result.exit_code == 0, result.output
    (report,) = seen
    assert {m.file_path for m in report.metrics} == set(_PATHS)
    assert [f.file_path for f in report.findings] == ["pkg/a.py"]


def test_a_store_an_older_version_wrote_says_so(repo, monkeypatch):
    """Not "no stored analysis": the analysis is there, this version cannot read it."""
    import sqlite3

    from repowise.cli import helpers

    run_async(_store(repo))
    with sqlite3.connect(repo / ".repowise" / "wiki.db") as conn:
        conn.execute("ALTER TABLE health_file_metrics DROP COLUMN analyzed_commit")

    async def cannot_repair(_url: str) -> None:
        return None

    monkeypatch.setattr(helpers, "reconcile_schema_best_effort", cannot_repair)
    result = CliRunner().invoke(health_command, [str(repo), "--no-workspace"])

    assert result.exit_code != 0
    assert "older version" in result.output and "repowise update" in result.output
    assert "No stored health analysis" not in result.output
