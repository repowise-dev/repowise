"""``repowise impacted-tests --format args|json``: selection against a real repo and index."""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.main import cli
from repowise.core.ci.base import CI_BASE_VARS, CI_ENV_VARS


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


async def _index(root: Path, head_commit: str) -> None:
    """A wiki.db whose graph has tests/test_a.py importing src/a.py."""
    from repowise.cli.helpers import resolve_command_target
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import GraphEdge, GraphNode, Repository

    db = root / ".repowise" / "wiki.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    await init_db(engine)
    local_path = str(resolve_command_target(path=str(root)).repo_path)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(Repository(id="r1", name="r", local_path=local_path, head_commit=head_commit))
        for path, is_test in (("src/a.py", False), ("src/b.py", False), ("tests/test_a.py", True)):
            # A test the indexer checked and found ordinary carries "".
            reason = "" if is_test else None
            node = GraphNode(
                repository_id="r1",
                node_id=path,
                node_type="file",
                is_test=is_test,
                always_run_reason=reason,
            )
            session.add(node)
        session.add(
            GraphEdge(
                repository_id="r1",
                source_node_id="tests/test_a.py",
                target_node_id="src/a.py",
                edge_type="imports",
            )
        )
        await session.commit()
    await engine.dispose()


@pytest.fixture
def repo(tmp_path):
    """``main`` holds the code and an index built there; ``feat`` edits src/a.py and a doc."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    _write(
        tmp_path,
        {
            "src/a.py": "A = 1\n",
            "src/b.py": "B = 1\n",
            "tests/test_a.py": "from src.a import A\n\ndef test_a():\n    assert A\n",
            "README.md": "docs\n",
            ".gitignore": ".repowise/\n",
        },
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    asyncio.run(_index(tmp_path, _git(tmp_path, "rev-parse", "HEAD")))
    _git(tmp_path, "switch", "-qc", "feat")
    _write(tmp_path, {"src/a.py": "A = 2\n", "README.md": "more docs\n"})
    _git(tmp_path, "commit", "-qam", "feat")
    return tmp_path


def _run(repo, *args: str, env: dict[str, str] | None = None):
    # The host CI's own variables must not pick the change.
    env = {**dict.fromkeys((*CI_ENV_VARS, *CI_BASE_VARS), ""), **(env or {})}
    # Separate streams: click 8.1 mixes stderr into stdout unless told not to.
    split = "mix_stderr" in inspect.signature(CliRunner.__init__).parameters
    runner = CliRunner(env=env, mix_stderr=False) if split else CliRunner(env=env)
    return runner.invoke(cli, ["impacted-tests", "--path", str(repo), *args])


def _err(result) -> str:
    """Stderr with rich's line wrapping undone."""
    return " ".join(result.stderr.split())


def test_args_prints_the_subset_on_one_line(repo) -> None:
    result = _run(repo, "main...feat", "--format", "args")
    assert result.exit_code == 0, result.output
    assert result.stdout == "tests/test_a.py\n"
    assert "1 argument(s) for pytest." in _err(result)
    assert "documentation: no tests needed" in _err(result)


def test_json_adds_the_selection_to_the_report(repo) -> None:
    result = _run(repo, "main...feat", "--format", "json", "--runner", "files")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["inferred_tests"] == [
        {"source_file": "src/a.py", "test_file": "tests/test_a.py", "via": "import-graph"}
    ]
    assert data["run_all"] is False
    assert data["runner"] == "files" and data["args"] == ["tests/test_a.py"]
    assert data["selected"]["skipped_files"] == ["README.md"]
    assert data["selected"]["basis"] == {"README.md": "no-tests-needed", "src/a.py": "import-graph"}


def test_a_lockfile_change_runs_its_ecosystem_tests_and_says_why(repo) -> None:
    _write(repo, {"uv.lock": "lock\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "deps")
    result = _run(repo, "main...feat", "--format", "args")
    assert result.exit_code == 0, result.output
    assert result.stdout == "tests/test_a.py\n"
    assert "uv.lock changed: it can change any Python test; 1 test file(s)" in _err(result)


def test_a_build_file_change_runs_everything_and_says_why(repo) -> None:
    _write(repo, {"Dockerfile": "FROM python\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "image")
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert "Dockerfile changed: build or test configuration can change any test." in _err(result)


def test_a_new_untested_file_runs_everything(repo) -> None:
    _write(repo, {"src/c.py": "C = 1\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "new")
    result = _run(repo, "main...feat", "--format", "json")
    data = json.loads(result.stdout)
    assert data["run_all"] is True and data["args"] == [":all"]
    assert data["reasons"][0].startswith("src/c.py: no coverage")


def test_no_index_runs_everything(repo) -> None:
    (repo / ".repowise" / "wiki.db").unlink()
    result = _run(repo, "main...feat", "--format", "args")
    assert result.exit_code == 0, result.output
    assert result.stdout == ":all\n"
    assert "No index" in _err(result)


def _move_main(repo, files: dict[str, str]) -> None:
    """main moves past the indexed commit, and the branch is rebased onto it."""
    _git(repo, "switch", "-q", "main")
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves")
    _git(repo, "switch", "-q", "feat")
    _git(repo, "rebase", "-q", "main")


def test_an_index_behind_the_base_adds_the_tests_reaching_the_gap(repo) -> None:
    # src/c.py was added since, so no test in the index reaches it.
    _move_main(repo, {"src/b.py": "import os\n\nB = 2\n", "src/c.py": "C = 1\n"})
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == "tests/test_a.py\n"
    assert "The index predates 1 changed file(s) outside this change; the tests reaching" in (
        _err(result)
    )


def test_a_lockfile_changed_after_the_index_runs_everything(repo) -> None:
    _move_main(repo, {"src/b.py": "B = 2\n", "uv.lock": "lock\n"})
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert "uv.lock changed after the index was built (1 such): dependencies" in _err(result)


def test_in_ci_the_default_change_is_the_pull_requests(repo) -> None:
    # Nothing is staged, so the local default would select nothing at all.
    result = _run(repo, "--format", "args", env={"CI": "true"})
    assert result.exit_code == 0, result.output
    assert result.stdout == "tests/test_a.py\n"
    # Locally the default is the staged changes, and nothing is staged: that is
    # no change to select from, never "no tests needed".
    local = _run(repo, "--format", "args")
    assert local.stdout == ":all\n"
    assert "No change against staged changes; nothing to select from." in _err(local)


def test_a_bad_config_cannot_evaluate(repo) -> None:
    _write(repo, {".repowise/config.yaml": "tests:\n  full_run_on: schema/**\n"})
    result = _run(repo, "main...feat", "--format", "json")
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert data["error"] == "config_invalid"
    assert data["message"].startswith("tests.full_run_on must be a list")
    args = _run(repo, "main...feat", "--format", "args")
    assert args.exit_code == 2 and args.stdout == ""


def test_config_extends_the_full_run_triggers(repo) -> None:
    _write(repo, {".repowise/config.yaml": "tests:\n  full_run_on: ['README.md']\n"})
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert "README.md changed: it matches tests.full_run_on." in _err(result)


def test_an_unknown_revision_cannot_evaluate(repo) -> None:
    result = _run(repo, "nope...feat", "--format", "args")
    assert result.exit_code == 2
    assert result.stdout == ""


def test_json_reports_the_indexed_commit_and_map_state(repo) -> None:
    data = json.loads(_run(repo, "main...feat", "--format", "json").stdout)
    assert data["indexed_commit"] == _git(repo, "rev-parse", "main")
    assert data["map_current"] is True


def test_list_prints_one_test_per_line(repo) -> None:
    result = _run(repo, "main...feat", "--format", "list")
    assert result.exit_code == 0, result.output
    assert result.stdout == "tests/test_a.py\n"


def test_an_index_that_disagrees_about_its_commit_runs_everything(repo) -> None:
    (repo / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": _git(repo, "rev-parse", "feat")}), encoding="utf-8"
    )
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert "The index records two commits" in _err(result)


def test_a_graph_that_cannot_be_read_runs_everything(repo, monkeypatch) -> None:
    from repowise.cli.commands import impacted_tests_cmd

    async def _broken(*_a, **_k):
        raise RuntimeError("edge table locked")

    monkeypatch.setattr(impacted_tests_cmd, "_graph_candidates", _broken)
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert "The graph could not be read (RuntimeError: edge table locked)" in _err(result)


def test_a_changed_file_in_the_test_tree_that_is_not_a_test_runs_everything(repo) -> None:
    _write(repo, {"tests/data/users.json": "[]\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "data")
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert "tests/data/users.json is in a test tree but is not code" in _err(result)


def _land_on_main(repo, files: dict[str, str]) -> None:
    """Commit *files* on main, re-index there, and rebase the change onto it."""
    _git(repo, "switch", "-q", "main")
    _write(repo, files)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves")
    (repo / ".repowise" / "wiki.db").unlink()
    asyncio.run(_index(repo, _git(repo, "rev-parse", "HEAD")))
    _git(repo, "switch", "-q", "feat")
    _git(repo, "rebase", "-q", "main")


def test_a_doc_a_test_reads_selects_that_test(repo) -> None:
    _land_on_main(repo, {"tests/test_readme.py": 'README = "README.md"\n'})
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == "tests/test_a.py tests/test_readme.py\n"
    assert "README.md changed: it is named by tests/test_readme.py; 1 test file(s)" in _err(result)


def test_a_doc_named_by_code_no_test_reaches_runs_everything(repo) -> None:
    _land_on_main(repo, {"src/b.py": 'DOC = "README.md"\n'})
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert "README.md is named by src/b.py, and no test is known to reach it." in _err(result)


def test_tests_the_graph_cannot_see_run_and_production_modules_do_not(repo) -> None:
    # Neither file is in the index; src/test_util.py is outside testpaths.
    _land_on_main(
        repo,
        {
            "tests/test_cli.py": "def test_cli():\n    pass\n",
            "src/test_util.py": "def helper():\n    pass\n",
            "pytest.ini": "[pytest]\ntestpaths = tests\n",
        },
    )
    result = _run(repo, "main...feat", "--format", "args", "--runner", "pytest")
    assert result.exit_code == 0, result.output
    assert result.stdout == "tests/test_a.py tests/test_cli.py\n"
    # src/test_util.py is production code, not a test the graph cannot see into.
    assert "1 test file(s) the graph cannot see into" in _err(result)


def test_a_changed_test_package_init_selects_the_tests_under_it(repo) -> None:
    _write(repo, {"tests/__init__.py": "# package\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "tests package")
    result = _run(repo, "main...feat", "--format", "json")
    data = json.loads(result.stdout)
    assert data["run_all"] is False, data["reasons"]
    assert data["selected"]["basis"]["tests/__init__.py"] == "test-package"
    assert data["args"] == ["tests/test_a.py"]


async def _add_to_index(root: Path, test: str, source: str) -> None:
    """*test* imports *source* in the graph, and *source* has health metrics."""
    from repowise.core.persistence.models import GraphEdge, GraphNode, HealthFileMetric

    db = root / ".repowise" / "wiki.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(
            GraphNode(
                repository_id="r1",
                node_id=test,
                node_type="file",
                is_test=True,
                always_run_reason="",
            )
        )
        session.add(
            GraphEdge(
                repository_id="r1", source_node_id=test, target_node_id=source, edge_type="imports"
            )
        )
        session.add(HealthFileMetric(repository_id="r1", file_path=source))
        await session.commit()
    await engine.dispose()


@pytest.mark.parametrize(
    ("b_after", "expected"),
    [
        ("B = 2\n", "tests/test_a.py\n"),
        ("import os\n\nB = 2\n", "tests/test_a.py tests/test_b.py\n"),
    ],
)
def test_only_files_whose_imports_moved_since_the_index_add_their_tests(
    repo, b_after, expected
) -> None:
    _land_on_main(repo, {"tests/test_b.py": "from src.b import B\n\ndef test_b():\n    assert B\n"})
    asyncio.run(_add_to_index(repo, "tests/test_b.py", "src/b.py"))
    _move_main(repo, {"src/b.py": b_after})
    result = _run(repo, "main...feat", "--format", "args", "--runner", "pytest")
    assert result.exit_code == 0, result.output
    assert result.stdout == expected


def _add_test(repo, path: str, imports: str, reason: str | None = "") -> None:
    """Land test *path* on main, index it there importing *imports*, rebase the change."""
    from sqlalchemy import update

    from repowise.core.persistence.models import GraphEdge, GraphNode, Repository

    _git(repo, "switch", "-q", "main")
    _write(repo, {path: "def test_x():\n    pass\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"add {path}")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "feat")
    _git(repo, "rebase", "-q", "main")

    async def add() -> None:
        db = (repo / ".repowise" / "wiki.db").as_posix()
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            node = GraphNode(
                repository_id="r1",
                node_id=path,
                node_type="file",
                is_test=True,
                always_run_reason=reason,
            )
            edge = GraphEdge(
                repository_id="r1", source_node_id=path, target_node_id=imports, edge_type="imports"
            )
            session.add_all([node, edge])
            await session.execute(update(Repository).values(head_commit=head))
            await session.commit()
        await engine.dispose()

    asyncio.run(add())


def test_a_test_the_indexer_found_walking_the_tree_runs_with_every_subset(repo) -> None:
    walks = "it lists and reads files under a source directory"
    _add_test(repo, "tests/test_lint.py", "src/b.py", walks)
    _add_test(repo, "tests/test_b.py", "src/b.py")
    result = _run(repo, "main...feat", "--format", "args", "--runner", "pytest")
    assert result.exit_code == 0, result.output
    # src/a.py changed: tests/test_b.py stays out, the tree walker comes in.
    assert result.stdout == "tests/test_a.py tests/test_lint.py\n"
    assert (
        "1 test file(s) that list source files or run the project in a child process run with "
        "every selection (e.g. tests/test_lint.py: it lists and reads files under a source "
        "directory)." in _err(result)
    )


def test_an_index_that_never_checked_its_tests_runs_everything(repo) -> None:
    _add_test(repo, "tests/test_b.py", "src/b.py", reason=None)
    result = _run(repo, "main...feat", "--format", "args")
    assert result.stdout == ":all\n"
    assert (
        "The index has not checked 1 test file(s) for walking the source tree or running "
        "the project (e.g. tests/test_b.py); run `repowise update`." in _err(result)
    )


def test_explain_names_the_changed_file_and_the_route(repo) -> None:
    _add_test(repo, "tests/test_b.py", "src/b.py")
    result = _run(repo, "main...feat", "--explain", "tests/test_a.py")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Selected: src/a.py changed (import-graph).",
        "Route: tests/test_a.py -> src/a.py",
    ]
    missed = _run(repo, "main...feat", "--explain", "./tests/test_b.py")
    assert missed.stdout.startswith("Not selected: no changed file reaches it")


def test_explain_joins_the_json_report(repo) -> None:
    result = _run(repo, "main...feat", "--format", "json", "--explain", "tests/test_a.py")
    data = json.loads(result.stdout)
    assert data["explain"] == {
        "test": "tests/test_a.py",
        "selected": True,
        "lines": ["Selected: src/a.py changed (import-graph).", "Route: tests/test_a.py -> src/a.py"],
        "route": ["tests/test_a.py", "src/a.py"],
    }
    assert data["selected"]["why"] == {"tests/test_a.py": "src/a.py changed (import-graph)"}


def test_a_runner_is_told_of_always_run_tests_for_another(repo) -> None:
    (repo / ".repowise" / "config.yaml").write_text(
        "tests:\n  always_run: [web/app.test.ts]\n", encoding="utf-8"
    )
    result = _run(repo, "main...feat", "--format", "args", "--runner", "pytest")
    assert result.exit_code == 0, result.output
    assert result.stdout == "tests/test_a.py\n"
    assert "not in these pytest arguments (1 for jest; e.g. web/app.test.ts)" in _err(result)
    data = json.loads(_run(repo, "main...feat", "--format", "json", "--runner", "pytest").stdout)
    assert data["left_out"] == {"jest": ["web/app.test.ts"]}
    files = _run(repo, "main...feat", "--format", "args", "--runner", "files")
    assert files.stdout == "tests/test_a.py web/app.test.ts\n"


def test_unreadable_store_cannot_evaluate(repo) -> None:
    db = repo / ".repowise" / "wiki.db"
    subprocess.run(
        ["sqlite3", str(db), "DROP TABLE health_file_metrics;"],
        check=True,
    )
    result = _run(repo, "main...feat")
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "repowise update" in _err(result)
    assert "Could not read the index" in _err(result)

    # Format JSON stays well-formed
    result_json = _run(repo, "main...feat", "--format", "json")
    assert result_json.exit_code == 2
    data = json.loads(result_json.stdout)
    assert data["error"] == "index_unreadable"
    assert "repowise update" in data["message"]

