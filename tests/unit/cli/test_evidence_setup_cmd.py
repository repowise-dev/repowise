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


def test_test_timings_name_the_environment_the_report_needs() -> None:
    timings = {"flag": "--reporters=jest-junit", "env": "A={x}", "report": "junit.xml", "ingest": None}
    (line,) = evidence_cmd._timings_lines(timings)
    assert line == (
        "  test timings: add [cyan]--reporters=jest-junit[/cyan] to the test command "
        "with [cyan]A={x}[/cyan] in its environment, report junit.xml"
    )


def test_the_text_shows_every_note_the_json_has() -> None:
    hooks = "This runs the test runner directly: `npm test` would also run the `pretest` script."
    unit = {
        "root": "", "stack": "node", "runner": "jest", "package_manager": "npm",
        "coverage": {"command": "npx jest --coverage", "prerequisites": [], "found": [], "ingest": None,
                     "reason": None, "notes": [hooks]},
        "runtime": [{"class": "unit_test", "workload": "python tests", "command": "python -c x",
                     "reason": None, "ingest": None, "note": "A subset of the tests is enough."}],
    }
    text = " ".join(evidence_cmd._setup_lines(unit))
    assert f"note: {hooks}" in text
    assert "note: A subset of the tests is enough." in text

    # Without a command, the last note is the reason, shown once.
    unit["coverage"].update(command=None, reason="No runner.", notes=["First.", "No runner."])
    lines = evidence_cmd._setup_lines(unit)
    assert sum("No runner." in line for line in lines) == 1
    assert any("note: First." in line for line in lines)


def test_coverage_setup_prints_the_coverage_part_of_the_plan(repo: Path) -> None:
    alias = CliRunner(mix_stderr=False).invoke(coverage_group, ["setup", "--format", "json"])

    assert alias.exit_code == 0, (alias.output, alias.exception)
    full = json.loads(_setup("--format", "json").stdout)
    for unit in full["units"]:
        # Runtime targets and test timings are evidence, not coverage.
        assert unit.pop("runtime") == []
        assert unit.pop("test_timings")["report"] == "junit.xml"
    assert json.loads(alias.stdout) == full


def test_an_indexed_repository_lists_runtime_targets_and_profiles_first(repo: Path) -> None:
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "app"\n[project.scripts]\napp = "app.cli:main"\n'
    )
    _index(repo)

    payload = json.loads(_setup("--format", "json").stdout)

    (unit,) = payload["units"]
    assert [t["class"] for t in unit["runtime"]] == ["entry_point", "unit_test"]
    # The entry point waits for the arguments of a real run, so the tests are profiled first.
    assert payload["next_call"]["purpose"].startswith("Profile python tests (unit_test)")
    assert payload["next_call"]["steps"][-1]["run"] == "repowise evidence status"
    table = _setup()
    assert "entry_point app: python -c" in table.stdout
    assert "Replace <args> with the arguments" in " ".join(table.stdout.split())
    assert payload["next_call"]["optional"]["workloads"] == ["app"]
    assert "Optional: app. Fill <args>" in " ".join(table.stdout.split())


def test_an_index_that_cannot_be_read_is_not_mistaken_for_none(repo: Path) -> None:
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "wiki.db").write_bytes(b"not a database " * 100)

    payload = json.loads(_setup("--format", "json").stdout)

    assert payload["next_call"]["steps"] == [{"cwd": str(repo), "run": "repowise doctor"}]
    assert payload["reason"].startswith("The index could not be read: ")
    assert "init" not in json.dumps(payload["next_call"])


def test_a_store_without_this_repository_is_told_to_index(repo: Path) -> None:
    async def empty_store() -> None:
        (repo / ".repowise").mkdir()
        engine = create_engine(f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")
        try:
            await init_db(engine)
        finally:
            await engine.dispose()

    asyncio.run(empty_store())

    payload = json.loads(_setup("--format", "json").stdout)

    assert payload["indexed"] is False
    assert payload["next_call"]["steps"][0]["run"] == "repowise init --yes --no-prose"
    assert payload["reason"] is None


def test_an_index_from_an_older_repowise_is_told_to_update(repo: Path, monkeypatch) -> None:
    import contextlib

    from repowise.cli import helpers

    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "wiki.db").write_bytes(b"")

    @contextlib.asynccontextmanager
    async def stale(_root):
        raise helpers.StaleIndexError()
        yield  # pragma: no cover

    monkeypatch.setattr(helpers, "repo_index_session", stale)

    result = _setup("--format", "json")

    assert result.exit_code == 0, (result.output, result.exception)
    payload = json.loads(result.stdout)
    assert payload["next_call"]["steps"] == [{"cwd": str(repo), "run": "repowise update"}]
    assert "run `repowise update`" in payload["reason"]


def test_setup_writes_nothing_without_write(repo: Path) -> None:
    assert _setup().exit_code == 0
    assert not (repo / ".github").exists()
    assert not (repo / ".gitlab").exists()


def test_write_adds_the_ci_file_once_and_never_overwrites_a_different_one(repo: Path) -> None:
    first = _setup("--write", "--format", "json")

    assert first.exit_code == 0, (first.output, first.exception)
    ci = json.loads(first.stdout)["ci"]
    assert ci["ci"] == "github" and ci["written"] is True and "content" not in ci
    workflow = repo / ".github" / "workflows" / "repowise-evidence.yml"
    assert "--junitxml=junit.xml" in workflow.read_text(encoding="utf-8")

    again = _setup("--write")
    assert again.exit_code == 0
    assert "Unchanged .github/workflows/repowise-evidence.yml" in again.stdout

    workflow.write_text("name: mine\n", encoding="utf-8")
    refused = _setup("--write", "--format", "json")
    assert refused.exit_code == 1
    assert json.loads(refused.stdout)["error"] == "ci_file_exists"
    assert workflow.read_text(encoding="utf-8") == "name: mine\n"


def test_write_picks_gitlab_for_a_gitlab_repository(repo: Path) -> None:
    (repo / ".gitlab-ci.yml").write_text("test:\n  script: [pytest]\n")

    result = _setup("--write")

    assert result.exit_code == 0, (result.output, result.exception)
    assert (repo / ".gitlab" / "repowise-evidence.yml").is_file()
    assert "include it from .gitlab-ci.yml" in result.stdout


def test_ci_needs_write(repo: Path) -> None:
    result = _setup("--ci", "gitlab")

    assert result.exit_code == 2
    assert not (repo / ".gitlab").exists()
