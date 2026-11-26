"""``repowise risk REVSPEC`` naming the measured functions a change edits."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.commands.risk_cmd import risk_command
from repowise.core.persistence.crud import replace_workload_hotness
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Repository
from tests.unit.persistence.helpers import make_wiki_symbol
from tests.unit.persistence.test_measured_costs import _hot, _run

APP = "src/app.py"
SEED = "def serve():\n    a = 1\n    return a\n\n\ndef helper():\n    b = 2\n    return b\n"
RERUN = "repowise perf measure --save before -- w-r1 before the change"


@pytest.fixture(autouse=True)
def _repo_local_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, text: str, message: str) -> None:
    path = repo / APP
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(["add", APP], repo)
    _git(["-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-m", message], repo)


def _repo(tmp_path: Path, edit: tuple[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, SEED, "seed")
    _commit(repo, SEED.replace(*edit), "work")
    asyncio.run(_write_index(repo))
    return repo


async def _write_index(repo: Path) -> None:
    """``serve`` (lines 1-3) ranked by an entry-point run; ``helper`` (6-8) not."""
    db_path = repo / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Repository(id="repo1", name="repo", local_path=str(repo.resolve())))
        session.add_all(
            [
                make_wiki_symbol("repo1", APP, "serve", start_line=1, end_line=3),
                make_wiki_symbol("repo1", APP, "helper", start_line=6, end_line=8),
                _run("repo1", "r1"),
            ]
        )
        await session.flush()
        await replace_workload_hotness(session, "repo1", [_hot("serve")])
        await session.commit()
    await engine.dispose()


def _risk(repo: Path, fmt: str):
    return CliRunner().invoke(
        risk_command, ["HEAD", "--path", str(repo), "--baseline", "0", "--format", fmt]
    )


def test_a_measured_function_is_named_with_its_rerun(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ("a = 1", "a = 2"))

    table = _risk(repo, "table")
    machine = _risk(repo, "json")

    assert table.exit_code == 0, table.output
    assert "This change edits 1 measured function" in table.output
    assert f'{APP}::serve (rank 1 in entry_point "w-r1"): {RERUN}' in " ".join(table.output.split())
    assert machine.exit_code == 0, machine.output
    [row] = json.loads(machine.output)["measured"]["functions"]
    assert row["fn"] == f"{APP}::serve"
    assert row["verify"].startswith(RERUN)


def test_a_change_beside_it_prints_nothing_extra(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ("b = 2", "b = 3"))

    table = _risk(repo, "table")
    machine = _risk(repo, "json")

    assert table.exit_code == 0, table.output
    assert "measured function" not in table.output
    assert "measured" not in json.loads(machine.output)


def test_a_file_the_score_left_out_is_never_measured(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ("a = 1", "a = 2"))

    result = CliRunner().invoke(
        risk_command, ["HEAD", "--path", str(repo), "--baseline", "0", "--ext", ".go"]
    )

    assert result.exit_code == 0, result.output
    assert "measured function" not in result.output
