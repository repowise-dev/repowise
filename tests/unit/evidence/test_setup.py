"""The ``evidence setup`` plan: commands per unit, reports on disk, and the next steps."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.evidence.setup import (
    INIT_COMMAND,
    SETUP_COMMAND,
    coverage_ingest_line,
    evidence_setup,
)


def _python(root: Path) -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "app"\n')
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text("def test_x():\n    pass\n")


def test_a_repository_never_indexed_is_told_to_index_first(tmp_path: Path) -> None:
    _python(tmp_path)

    plan = evidence_setup(tmp_path, indexed=False)

    assert [s["run"] for s in plan["next_call"]["steps"]] == [INIT_COMMAND, SETUP_COMMAND]
    (unit,) = plan["units"]
    assert unit["stack"] == "python"
    assert unit["coverage"]["command"].startswith("pytest --cov=app")
    # The plan still says what runs, so a person can read it before indexing.
    assert unit["coverage"]["ingest"] == "repowise coverage add coverage.xml .coverage"
    assert not (tmp_path / ".repowise").exists()


def test_an_indexed_repository_runs_then_ingests(tmp_path: Path) -> None:
    _python(tmp_path)

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)

    steps = plan["next_call"]["steps"]
    assert [s["run"] for s in steps][-2:] == [
        "repowise coverage add coverage.xml .coverage",
        "repowise coverage status",
    ]
    # The tests import the project, so it is installed with the test tools.
    assert steps[0]["run"] == "python -m pip install -e . pytest pytest-cov"
    assert steps[1]["run"].startswith("pytest --cov=app")
    assert all(s["cwd"] == str(tmp_path) for s in steps)
    assert plan["commit"] == "a" * 40


def test_a_report_on_disk_is_ingested_first(tmp_path: Path) -> None:
    _python(tmp_path)
    (tmp_path / "coverage.xml").write_text("<coverage/>")

    plan = evidence_setup(tmp_path, indexed=True)

    assert plan["units"][0]["coverage"]["found"] == ["coverage.xml"]
    assert plan["next_call"]["steps"][0]["run"] == "repowise coverage add coverage.xml"


def test_a_nested_unit_keeps_its_own_report_and_runs_in_its_root(tmp_path: Path) -> None:
    (tmp_path / "go.work").write_text("go 1.22\nuse ./svc\n")
    svc = tmp_path / "svc"
    svc.mkdir()
    (svc / "go.mod").write_text("module example.com/svc\n\ngo 1.22\n")
    (svc / "main.go").write_text("package main\n\nfunc main() {}\n")
    (svc / "main_test.go").write_text("package main\n")
    (svc / "coverage.out").write_text("mode: set\n")

    plan = evidence_setup(tmp_path, indexed=True)

    (unit,) = [u for u in plan["units"] if u["stack"] == "go"]
    assert unit["root"] == "svc"
    assert unit["coverage"]["found"] == ["svc/coverage.out"]
    assert unit["coverage"]["ingest"] == "repowise coverage add svc/coverage.out"


def test_an_unknown_stack_says_why_and_offers_no_command(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello\n")

    plan = evidence_setup(tmp_path, indexed=True)

    (unit,) = plan["units"]
    assert unit["stack"] == "unknown"
    assert unit["coverage"]["command"] is None
    assert unit["coverage"]["ingest"] is None
    assert unit["coverage"]["reason"]
    assert plan["next_call"] is None
    assert plan["reason"]


def test_the_default_plan_stays_small(tmp_path: Path) -> None:
    _python(tmp_path)

    assert len(json.dumps(evidence_setup(tmp_path, indexed=True))) < 2000


def test_report_globs_reach_coverage_add_unexpanded_and_spaces_stay_one_argument() -> None:
    assert coverage_ingest_line(["coverage.xml"]) == "repowise coverage add coverage.xml"
    assert coverage_ingest_line(["**/jacoco.xml"]) == "repowise coverage add '**/jacoco.xml'"
    assert coverage_ingest_line(["a.xml", "b c.xml"]) == 'repowise coverage add a.xml "b c.xml"'


def test_the_ingest_line_resolves_from_the_repository_root(tmp_path: Path, monkeypatch) -> None:
    import shlex

    from repowise.core.analysis.health.coverage import expand_report_args

    (tmp_path / "go.work").write_text("go 1.22\nuse ./svc\n")
    svc = tmp_path / "svc"
    svc.mkdir()
    (svc / "go.mod").write_text("module example.com/svc\n\ngo 1.22\n")
    (svc / "main_test.go").write_text("package main\n")
    (svc / "coverage.out").write_text("mode: set\n")
    line = evidence_setup(tmp_path, indexed=True)["next_call"]["steps"][0]["run"]
    monkeypatch.chdir(tmp_path)

    args = shlex.split(line)[3:]
    assert [p.resolve() for p in expand_report_args(args, Path())] == [(svc / "coverage.out").resolve()]


def test_coverage_measured_at_head_ends_the_loop(tmp_path: Path) -> None:
    _python(tmp_path)
    (tmp_path / "coverage.xml").write_text("<coverage/>")

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40, coverage_commit="a" * 40)

    assert plan["next_call"] is None
    assert plan["reason"].endswith("nothing left to run.")
    assert evidence_setup(tmp_path, indexed=True, head="a" * 40, coverage_commit="b" * 40)["next_call"]


def _commit(root: Path, message: str, when: str) -> str:
    import git as gitpython

    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    repo.git.add("--all")
    repo.index.commit(message, author_date=when, commit_date=when)
    head = repo.head.commit.hexsha
    repo.close()
    return head


def test_a_report_from_an_older_commit_is_regenerated_not_reingested(tmp_path: Path) -> None:
    import os

    _python(tmp_path)
    (tmp_path / "coverage.xml").write_text("<coverage/>")
    report_time = 1_700_000_000
    os.utime(tmp_path / "coverage.xml", (report_time, report_time))
    (tmp_path / ".gitignore").write_text("coverage.xml\n")
    old = _commit(tmp_path, "one", "2023-11-13T12:00:00")
    (tmp_path / "app" / "more.py").write_text("x = 1\n")
    head = _commit(tmp_path, "two", "2023-11-17T12:00:00")

    plan = evidence_setup(tmp_path, indexed=True, head=head)
    # The agent already ingested the stale report: coverage sits at the old commit.
    again = evidence_setup(tmp_path, indexed=True, head=head, coverage_commit=old)

    cov = plan["units"][0]["coverage"]
    assert cov["found"] == ["coverage.xml"] and cov["found_current"] is False
    assert cov["ingest"] == "repowise coverage add coverage.xml .coverage"
    for call in (plan["next_call"], again["next_call"]):
        assert call["purpose"].startswith("Run the python tests")


def test_reports_of_every_unit_are_ingested_in_one_call(tmp_path: Path) -> None:
    (tmp_path / "go.work").write_text("go 1.22\nuse ./a\nuse ./b\n")
    for unit in ("a", "b"):
        (tmp_path / unit).mkdir()
        (tmp_path / unit / "go.mod").write_text(f"module example.com/{unit}\n\ngo 1.22\n")
        (tmp_path / unit / "x_test.go").write_text(f"package {unit}\n")
        (tmp_path / unit / "coverage.out").write_text("mode: set\n")

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)

    assert plan["next_call"]["steps"][0]["run"] == "repowise coverage add a/coverage.out b/coverage.out"


def test_each_unit_says_how_its_tests_write_junit_timings(tmp_path: Path) -> None:
    _python(tmp_path)
    java = tmp_path / "svc"
    (java / "src" / "test" / "java").mkdir(parents=True)
    (java / "pom.xml").write_text("<project></project>")
    (java / "src" / "test" / "java" / "CartTest.java").write_text("class CartTest {}")

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)

    timings = {unit["root"]: unit["test_timings"] for unit in plan["units"]}
    assert timings[""] == {
        "flag": "--junitxml=junit.xml",
        "env": None,
        "report": "junit.xml",
        "ingest": 'repowise evidence add junit.xml --workload "pytest tests" --class unit_test '
        "--commit " + "a" * 40,
    }
    # Surefire writes its reports by default.
    assert timings["svc"]["flag"] is None
    assert timings["svc"]["report"] == "svc/target/surefire-reports"
    # Timings rank tests; they are never a step of the loop.
    assert "junit" not in json.dumps(plan["next_call"])


def test_a_jest_unit_says_how_jest_junit_writes_its_timings(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    (web / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}})
    )
    (web / "package-lock.json").write_text("{}")
    (web / "a.test.ts").write_text("test('a', () => {})")

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)

    (unit,) = plan["units"]
    assert unit["test_timings"] == {
        "flag": "--reporters=default --reporters=jest-junit",
        "env": "JEST_JUNIT_CLASSNAME={filepath} JEST_JUNIT_FILE_PATH_PREFIX=web/",
        "report": "web/junit.xml",
        "ingest": 'repowise evidence add web/junit.xml --workload "web: jest tests" --class unit_test '
        "--commit " + "a" * 40,
    }
    assert "npm install --no-save jest-junit@16" in unit["coverage"]["prerequisites"]


def test_coverage_setup_leaves_test_timings_out(tmp_path: Path) -> None:
    _python(tmp_path)

    (unit,) = evidence_setup(tmp_path, indexed=True, coverage_only=True)["units"]

    assert "test_timings" not in unit
