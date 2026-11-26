"""The ``evidence setup`` plan: commands per unit, reports on disk, and the next steps."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from repowise.core.evidence.runtime_setup import RuntimeInputs
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
    # The per-test map's file is a field, not only a hint in prose.
    assert unit["coverage"]["per_test_map"] == ".coverage"
    assert not (tmp_path / ".repowise").exists()


def test_an_indexed_repository_runs_then_ingests(tmp_path: Path) -> None:
    _python(tmp_path)

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)

    steps = plan["next_call"]["steps"]
    # One run gives coverage and test timings, and both are ingested.
    assert [s["run"] for s in steps][-3:] == [
        "repowise coverage add coverage.xml .coverage",
        f'repowise evidence add junit.xml --workload "pytest tests" --class unit_test --commit {"a" * 40}',
        "repowise coverage status",
    ]
    # The tests import the project, so it is installed with the test tools.
    assert steps[0]["run"] == "python -m pip install -e . pytest pytest-cov"
    assert steps[1]["run"].startswith("pytest --cov=app")
    assert steps[1]["run"].endswith(" --junitxml=junit.xml")
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
    # Go coverage has no per-test contexts.
    assert unit["coverage"]["per_test_map"] is None


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


def test_coverage_and_test_timings_measured_at_head_end_the_loop(tmp_path: Path) -> None:
    _python(tmp_path)
    (tmp_path / "coverage.xml").write_text("<coverage/>")
    timed = RuntimeInputs(measured=frozenset({("pytest tests", "a" * 40)}))

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40, coverage_commit="a" * 40, coverage_only=True)

    assert plan["next_call"] is None
    assert plan["reason"].endswith("nothing left to run.")
    assert evidence_setup(tmp_path, indexed=True, head="a" * 40, coverage_commit="b" * 40)["next_call"]
    # Coverage alone at HEAD still runs the tests once more, for their timings.
    again = evidence_setup(tmp_path, indexed=True, head="a" * 40, coverage_commit="a" * 40,
                           runtime=RuntimeInputs(measured=frozenset({("python tests", "a" * 40)})))
    assert again["next_call"]["steps"][-2]["run"].startswith('repowise evidence add junit.xml --workload "pytest tests"')
    done = evidence_setup(tmp_path, indexed=True, head="a" * 40, coverage_commit="a" * 40,
                          runtime=replace(timed, measured=timed.measured | {("python tests", "a" * 40)}))
    assert done["next_call"] is None


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
        "command": "pytest --cov=app --cov-report=xml --cov-context=test --junitxml=junit.xml",
        "workload": "pytest tests",
        "ingest": 'repowise evidence add junit.xml --workload "pytest tests" --class unit_test '
        "--commit " + "a" * 40,
    }
    # Surefire writes its reports by default.
    assert timings["svc"]["flag"] is None
    assert timings["svc"]["report"] == "svc/target/surefire-reports"
    assert timings["svc"]["command"] == plan["units"][1]["coverage"]["command"]
    # The loop's test step is that command: one run, coverage and timings.
    steps = [s["run"] for s in plan["next_call"]["steps"]]
    assert timings[""]["command"] in steps and timings[""]["ingest"] in steps


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
        "env": {"JEST_JUNIT_CLASSNAME": "{filepath}", "JEST_JUNIT_FILE_PATH_PREFIX": "web/"},
        "report": "web/junit.xml",
        "command": "npm test -- --coverage --coverageReporters=lcov --reporters=default --reporters=jest-junit",
        "workload": "web: jest tests",
        "ingest": 'repowise evidence add web/junit.xml --workload "web: jest tests" --class unit_test '
        "--commit " + "a" * 40,
    }
    assert "npm install --no-save jest-junit@16" in unit["coverage"]["prerequisites"]
    # The loop's test step sets the environment itself: one spelling for every shell.
    (run,) = [s for s in plan["next_call"]["steps"] if s["run"] == unit["test_timings"]["command"]]
    assert run["env"] == unit["test_timings"]["env"]


def test_coverage_setup_leaves_test_timings_out(tmp_path: Path) -> None:
    _python(tmp_path)

    (unit,) = evidence_setup(tmp_path, indexed=True, coverage_only=True)["units"]

    assert "test_timings" not in unit


def test_timed_targets_reuses_passed_go_mains_without_rebuilding_build_facts(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.core.analysis import published_packages
    from repowise.core.evidence.runtime_setup import RuntimeInputs
    from repowise.core.evidence.setup import detect_go_mains, timed_targets
    from repowise.core.test_runners.stacks import detect_repo_stacks

    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22\n")
    app = tmp_path / "cmd" / "app"
    app.mkdir(parents=True)
    (app / "main.go").write_text("package main\n\nfunc main() {}\n")
    (app / "h.go").write_text("package main\n\nfunc Helper() {}\n")
    (app / "h_test.go").write_text("package main\n\nimport \"testing\"\n\nfunc BenchmarkH(b *testing.B) {}\n")

    constructions = 0
    real_build_facts = published_packages.BuildFacts

    def counting_build_facts(root: Path):
        nonlocal constructions
        constructions += 1
        return real_build_facts(root)

    monkeypatch.setattr(published_packages, "BuildFacts", counting_build_facts)

    stacks = detect_repo_stacks(tmp_path, None)
    mains = detect_go_mains(tmp_path, stacks)
    first = timed_targets(
        tmp_path, ["cmd/app/h.go"], units=None, runtime=RuntimeInputs(), stacks=stacks, go_mains=mains
    )
    second = timed_targets(
        tmp_path, ["cmd/app/h.go"], units=None, runtime=RuntimeInputs(), stacks=stacks, go_mains=mains
    )

    assert constructions == 1
    assert first == second


def test_detect_go_mains_skips_build_facts_for_non_go_repos(
    tmp_path: Path, monkeypatch
) -> None:
    from repowise.core.analysis import published_packages
    from repowise.core.evidence.setup import detect_go_mains
    from repowise.core.test_runners.stacks import detect_repo_stacks

    _python(tmp_path)

    def fail_build_facts(root: Path):
        raise AssertionError("BuildFacts should not be constructed for non-Go repos")

    monkeypatch.setattr(published_packages, "BuildFacts", fail_build_facts)

    stacks = detect_repo_stacks(tmp_path, None)
    assert detect_go_mains(tmp_path, stacks) == []


def test_the_per_test_map_file_sits_in_the_unit_root_and_needs_a_command() -> None:
    from repowise.core.evidence.setup import _coverage_unit
    from repowise.core.test_runners.stacks import Stack

    test = "pytest --cov-context=test"
    nested = Stack("python", "pytest", test=test, push_reports=".coverage", root="svc")
    no_command = Stack("python", "pytest", push_reports=".coverage", root="svc")

    assert _coverage_unit(nested, [], False)["coverage"]["per_test_map"] == "svc/.coverage"
    assert _coverage_unit(no_command, [], False)["coverage"]["per_test_map"] is None
    no_contexts = Stack("python", "pytest", test="pytest --cov --cov-report=xml", root="svc")
    assert _coverage_unit(no_contexts, [], False)["coverage"]["per_test_map"] is None


def test_go_and_a_multi_module_build_ingest_their_timings_in_the_same_loop(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
    (tmp_path / "x_test.go").write_text("package x\n")
    svc = tmp_path / "svc"
    (svc / "a" / "src" / "test" / "java").mkdir(parents=True)
    (svc / "pom.xml").write_text("<project><modules><module>a</module></modules></project>")
    (svc / "a" / "src" / "test" / "java" / "ATest.java").write_text("class ATest {}")

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)

    go, java = plan["units"]
    assert go["test_timings"]["workload"] == "go tests"
    steps = [s["run"] for s in plan["next_call"]["steps"]]
    assert "go test -coverprofile=coverage.out ./... -json > go-test.json" in steps
    assert 'repowise evidence add go-test.json --workload "go tests"' in steps[-2]
    # The glob reaches ``evidence add`` whole, which expands it itself.
    assert java["test_timings"]["ingest"].startswith(
        "repowise evidence add 'svc/**/target/surefire-reports/*.xml' --workload \"svc: maven tests\""
    )


def test_the_test_step_sets_its_environment_in_the_step(tmp_path: Path) -> None:
    _python(tmp_path)

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)

    (run,) = [s for s in plan["next_call"]["steps"] if "--junitxml" in s["run"]]
    assert run["env"] == {"COVERAGE_CORE": "ctrace"} == plan["units"][0]["coverage"]["env"]
    # Never in the command text: sh and PowerShell spell an assignment differently.
    assert "COVERAGE_CORE" not in run["run"]


def test_a_timings_report_written_at_head_ends_the_rerun(tmp_path: Path) -> None:
    _python(tmp_path)
    head = {"indexed": True, "head": "a" * 40, "coverage_commit": "a" * 40}
    assert evidence_setup(tmp_path, **head)["next_call"] is not None

    # The run wrote its report and the ingest was refused (every test errored, say):
    # running the suite again would write the same, so the loop ends instead of repeating.
    (tmp_path / "junit.xml").write_text("<testsuite/>")

    assert evidence_setup(tmp_path, **head)["next_call"] is None


def test_dotnet_unit_says_how_its_tests_write_junit_timings(tmp_path: Path) -> None:
    proj = tmp_path / "tests" / "App.Tests"
    proj.mkdir(parents=True)
    (proj / "App.Tests.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"></Project>')
    (proj / "CartTest.cs").write_text("class CartTest {}")

    plan = evidence_setup(tmp_path, indexed=True, head="a" * 40)
    (unit,) = plan["units"]
    assert unit["test_timings"] == {
        "flag": '--logger "junit;LogFilePath=junit.xml"',
        "env": None,
        "report": "junit.xml",
        "command": (
            'dotnet test tests/App.Tests/App.Tests.csproj --collect:"XPlat Code Coverage" '
            '--results-directory coverage --logger "junit;LogFilePath=junit.xml"'
        ),
        "workload": "dotnet tests",
        "ingest": 'repowise evidence add junit.xml --workload "dotnet tests" --class unit_test --commit '
        + "a" * 40,
    }

