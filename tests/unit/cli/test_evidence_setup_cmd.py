"""``repowise evidence setup`` and its ``coverage setup`` alias, with and without an index."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands import evidence_cmd
from repowise.cli.commands.coverage_cmd import coverage_group
from repowise.core.persistence import create_session_factory, get_session
from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.models import Repository


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\n')
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    monkeypatch.setattr(evidence_cmd, "_resolve_repo", lambda _p, _n: tmp_path)
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    return tmp_path


def _index(root: Path) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    repo.index.add(["pyproject.toml", "app/__init__.py"])
    repo.index.commit("init")
    repo.close()

    async def build() -> None:
        (root / ".repowise").mkdir()
        engine = create_engine(f"sqlite+aiosqlite:///{root / '.repowise' / 'wiki.db'}")
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                session.add(Repository(id="r1", name="t", local_path=str(root), url=""))
        finally:
            await engine.dispose()

    asyncio.run(build())


def _setup(*args: str):
    return CliRunner(mix_stderr=False).invoke(evidence_cmd.evidence_group, ["setup", *args])


def test_a_repository_never_indexed_gets_a_plan_and_no_store(repo: Path) -> None:
    result = _setup("--format", "json")

    assert result.exit_code == 0, (result.output, result.exception)
    payload = json.loads(result.stdout)
    assert payload["indexed"] is False
    assert payload["next_call"]["steps"][0]["run"] == "repowise init --yes --no-prose"
    assert payload["units"][0]["stack"] == "python"
    assert not (repo / ".repowise").exists()


def test_an_indexed_repository_is_told_to_run_and_ingest(repo: Path) -> None:
    _index(repo)

    payload = json.loads(_setup("--format", "json").stdout)

    assert payload["indexed"] is True
    assert payload["units"][0]["coverage"]["ingest"] == "repowise coverage add coverage.xml .coverage"
    # No console script: the unit-test suite is the one runtime target.
    assert payload["next_call"]["purpose"].startswith("Profile python tests (unit_test)")


def test_the_table_names_the_command_the_ingest_line_and_the_next_steps(repo: Path) -> None:
    result = _setup()

    assert result.exit_code == 0
    assert "python (pytest, pip) at the repository root" in result.stdout
    assert "ingest (from the repository root): repowise coverage add coverage.xml .coverage" in result.stdout
    assert "repowise init --yes --no-prose" in result.stdout


def test_coverage_setup_prints_the_coverage_part_of_the_plan(repo: Path) -> None:
    alias = CliRunner(mix_stderr=False).invoke(coverage_group, ["setup", "--format", "json"])

    assert alias.exit_code == 0, (alias.output, alias.exception)
    full = json.loads(_setup("--format", "json").stdout)
    for unit in full["units"]:
        assert unit.pop("runtime") == []
    assert json.loads(alias.stdout) == full


def test_an_indexed_repository_lists_runtime_targets_and_profiles_first(repo: Path) -> None:
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "app"\n[project.scripts]\napp = "app.cli:main"\n'
    )
    _index(repo)

    payload = json.loads(_setup("--format", "json").stdout)

    (unit,) = payload["units"]
    assert [t["class"] for t in unit["runtime"]] == ["entry_point", "unit_test"]
    assert payload["next_call"]["purpose"].startswith("Profile app (entry_point)")
    assert payload["next_call"]["steps"][-1]["run"] == "repowise evidence status"
    table = _setup()
    assert "entry_point app: python -c" in table.stdout
