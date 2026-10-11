"""Persisted symbol complexity agrees across full, update, and resume paths."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.helpers import release_update_lock
from repowise.cli.main import cli
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.database import resolve_db_url
from repowise.core.pipeline import run_pipeline
from repowise.core.pipeline.full_index import index_repo_full
from repowise.core.pipeline.modes import OrchestratorMode
from repowise.core.pipeline.resume import ResumeController, ResumeLedger, ResumePhase

SOURCE = """def branch(value):
    if value:
        return 1
    return 0
"""


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Complexity Parity",
        "GIT_AUTHOR_EMAIL": "complexity-parity@example.invalid",
        "GIT_COMMITTER_NAME": "Complexity Parity",
        "GIT_COMMITTER_EMAIL": "complexity-parity@example.invalid",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "probe.py").write_text(SOURCE, encoding="utf-8")
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")


def _copy_without_index(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".repowise"))
    (destination / ".repowise").mkdir()


def _invoke(runner: CliRunner, repo: Path, *args: str) -> None:
    try:
        result = runner.invoke(cli, [*args, str(repo)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        release_update_lock(repo)


def _symbol_snapshot(repo: Path) -> list[tuple[str, int]]:
    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as connection:
        return connection.execute(
            "SELECT symbol_id, complexity_estimate FROM wiki_symbols ORDER BY symbol_id"
        ).fetchall()


async def _leave_index_checkpoint(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(resolve_db_url(repo))
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            row = await upsert_repository(session, name=repo.name, local_path=str(repo))

        import repowise.core.pipeline.orchestrator as orchestrator

        async def _interrupt_analysis(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated interruption after INDEX")

        with monkeypatch.context() as patch:
            patch.setattr(orchestrator, "_run_health_analysis", _interrupt_analysis)
            with pytest.raises(RuntimeError, match="simulated interruption"):
                await run_pipeline(
                    repo,
                    mode=OrchestratorMode.FAST,
                    resume_controller=ResumeController(sf, row.id, resume=False),
                )

        completed = await ResumeLedger(sf, row.id).completed_phases()
        assert ResumePhase.INDEX in completed
        assert ResumePhase.ANALYSIS not in completed
    finally:
        await engine.dispose()


async def _resume_after_index(repo: Path) -> None:
    engine = create_engine(resolve_db_url(repo))
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            row = await upsert_repository(session, name=repo.name, local_path=str(repo))
        await run_pipeline(
            repo,
            mode=OrchestratorMode.FAST,
            resume_controller=ResumeController(sf, row.id, resume=True),
        )
    finally:
        await engine.dispose()


def test_symbol_complexity_parity_across_clean_incremental_resume_and_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    incremental = tmp_path / "incremental" / "repo"
    _make_repo(incremental)
    runner = CliRunner()
    init_args = (
        "init",
        "--mode",
        "fast",
        "--no-editor-setup",
        "--no-hook",
        "--no-agents",
        "--no-codex",
        "--no-claude-md",
        "--no-cost-tracking",
        "--no-workspace",
        "-y",
    )

    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *init_args)
    with (incremental / "probe.py").open("a", encoding="utf-8") as source:
        source.write("\n# harmless incremental edit\n")
    _git(incremental, "add", "-A")
    _git(incremental, "commit", "-q", "-m", "harmless edit")

    clean = tmp_path / "clean" / "repo"
    resume = tmp_path / "resume" / "repo"
    workspace = tmp_path / "workspace" / "repo"
    for target in (clean, resume, workspace):
        _copy_without_index(incremental, target)

    _invoke(runner, incremental, "update", "--index-only", "--no-workspace", "--no-agents")

    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{clean / '.repowise' / 'wiki.db'}")
    import repowise.core.pipeline.resume.controller as resume_controller_module

    async def _fail_checkpoint_reconciliation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated symbol checkpoint failure")

    # The mid-run checkpoint is best-effort. The final required persistence
    # transaction must retry the symbol reconciliation before it marks ANALYSIS
    # complete, so this clean run still converges rather than storing CCN=1.
    with monkeypatch.context() as patch:
        patch.setattr(
            resume_controller_module,
            "persist_symbol_analysis",
            _fail_checkpoint_reconciliation,
        )
        _invoke(runner, clean, *init_args)
    # A later CLI resume re-parses source but reuses the completed analysis.
    # Final persistence must not replace its computed CCN with parser defaults.
    _invoke(runner, clean, *init_args, "--resume")

    resume_db = resume / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{resume_db}")
    asyncio.run(_leave_index_checkpoint(resume, monkeypatch))
    asyncio.run(_resume_after_index(resume))

    workspace_db = workspace / ".repowise" / "wiki.db"
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{workspace_db}")
    asyncio.run(index_repo_full(workspace))

    snapshots = {
        "clean": _symbol_snapshot(clean),
        "incremental": _symbol_snapshot(incremental),
        "resume": _symbol_snapshot(resume),
        "workspace": _symbol_snapshot(workspace),
    }
    assert snapshots == {name: [("probe.py::branch", 2)] for name in snapshots}
