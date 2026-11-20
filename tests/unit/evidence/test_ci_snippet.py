"""The CI file ``evidence setup --write`` adds, for GitHub Actions and GitLab."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from repowise.core.evidence.ci_snippet import ci_snippet, detect_ci
from repowise.core.test_runners.stacks import UNKNOWN, detect_stacks

_FILES = {
    "pyproject.toml": '[project]\nname = "app"\n',
    "web/package.json": json.dumps(
        {"name": "w", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "1"}}
    ),
    "web/.nvmrc": "20\n",
}
_PATHS = ["pyproject.toml", "app/__init__.py", "tests/test_a.py",
          "web/package.json", "web/package-lock.json", "web/.nvmrc", "web/a.ts"]


def _stacks():
    return detect_stacks(_PATHS, _FILES)


def test_github_runs_each_unit_with_coverage_and_junit_and_keeps_both() -> None:
    snippet = ci_snippet(_stacks(), "github", "trunk")

    assert snippet["path"] == ".github/workflows/repowise-evidence.yml"
    assert snippet["units"] == ["", "web"]
    doc = yaml.safe_load(snippet["content"])
    assert doc[True]["push"]["branches"] == ["trunk"]  # YAML 1.1 reads `on` as true
    root, web = doc["jobs"].values()
    run = next(s["run"] for s in root["steps"] if s.get("name") == "Run tests with coverage")
    assert run.endswith("--junitxml=junit.xml")
    kept = root["steps"][-1]["with"]["path"].split()
    assert kept == ["coverage.xml", ".coverage", "junit.xml"]
    # A unit below the root runs there, reads its own version file and keeps repo-relative paths.
    assert web["defaults"]["run"]["working-directory"] == "web"
    node = next(s for s in web["steps"] if s.get("uses", "").startswith("actions/setup-node"))
    assert node["with"] == {"node-version-file": "web/.nvmrc"}
    assert web["steps"][-1]["with"]["path"].split() == ["web/coverage/lcov.info", "web/junit.xml"]


def test_a_jest_unit_installs_jest_junit_and_keeps_repo_relative_results() -> None:
    files = {"web/package.json": json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}})}
    stacks = detect_stacks(["web/package.json", "web/package-lock.json", "web/a.test.ts"], files)

    (job,) = yaml.safe_load(ci_snippet(stacks, "github", "main")["content"])["jobs"].values()
    runs = [s["run"] for s in job["steps"] if "run" in s]
    assert runs == [
        "npm ci",
        "npm install --no-save jest-junit@16",
        "JEST_JUNIT_CLASSNAME={filepath} JEST_JUNIT_FILE_PATH_PREFIX=web/ npm test -- --coverage "
        "--coverageReporters=lcov --reporters=default --reporters=jest-junit",
    ]
    assert job["steps"][-1]["with"]["path"].split() == ["web/coverage/lcov.info", "web/junit.xml"]
    (gitlab,) = yaml.safe_load(ci_snippet(stacks, "gitlab", None)["content"]).values()
    assert gitlab["script"][-1] == runs[-1]
    assert gitlab["artifacts"]["reports"]["junit"] == "web/junit.xml"


def test_a_chained_jest_command_is_told_the_flag_and_its_environment() -> None:
    files = {"web/package.json": json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}})}
    (stack,) = detect_stacks(["web/package.json", "web/package-lock.json"], files)
    chained = replace(stack, test=f"{stack.test} && echo done")

    content = ci_snippet([chained], "github", "main")["content"]

    assert ("# add --reporters=default --reporters=jest-junit to the test command with "
            "JEST_JUNIT_CLASSNAME={filepath} JEST_JUNIT_FILE_PATH_PREFIX=web/ in its environment") in content


def test_gitlab_jobs_cd_into_the_unit_and_report_junit() -> None:
    snippet = ci_snippet(_stacks(), "gitlab", None)

    assert snippet["path"] == ".gitlab/repowise-evidence.yml"
    doc = yaml.safe_load(snippet["content"])
    root, web = doc["repowise-evidence-root-python"], doc["repowise-evidence-web-node"]
    assert root["image"] == "python:3.12"
    assert root["script"][0] == "python -m pip install -e . pytest pytest-cov"
    assert root["script"][-1].endswith("--junitxml=junit.xml")
    assert root["artifacts"]["reports"]["junit"] == "junit.xml"
    assert web["script"][:2] == ["cd web", "npm ci"]
    assert web["artifacts"]["paths"] == ["web/coverage/lcov.info", "web/junit.xml"]


def test_a_single_unit_gets_one_plain_job() -> None:
    stacks = detect_stacks(["go.mod", "main.go"], {"go.mod": "module example.com/m\n"})

    github = yaml.safe_load(ci_snippet(stacks, "github", "main")["content"])
    gitlab = yaml.safe_load(ci_snippet(stacks, "gitlab", "main")["content"])

    assert list(github["jobs"]) == ["evidence"]
    assert list(gitlab) == ["repowise-evidence"]
    assert gitlab["repowise-evidence"]["image"] == "golang:1"
    assert "reports" not in gitlab["repowise-evidence"]["artifacts"]


def test_no_unit_with_a_command_writes_no_content_and_says_why() -> None:
    snippet = ci_snippet([UNKNOWN], "github", "main")

    assert snippet["content"] is None
    assert snippet["skipped"] == [
        {"root": "", "stack": "unknown", "reason": "Repowise could not tell how this repository runs its tests."}
    ]


def test_an_unknown_ci_is_refused() -> None:
    with pytest.raises(ValueError, match="github, gitlab"):
        ci_snippet(_stacks(), "jenkins", "main")


def test_detect_ci_picks_gitlab_only_without_github_workflows(tmp_path: Path) -> None:
    assert detect_ci(tmp_path) == "github"
    (tmp_path / ".gitlab-ci.yml").write_text("test:\n  script: [true]\n")
    assert detect_ci(tmp_path) == "gitlab"
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    assert detect_ci(tmp_path) == "github"


_GRADLE = (["build.gradle", "src/main/java/A.java", "src/test/java/ATest.java"],
           {"build.gradle": "plugins { id 'java' }\n"})
_RUBY = (["Gemfile", "lib/a.rb", "spec/a_spec.rb"], {"Gemfile": "gem 'rspec'\n"})


@pytest.mark.parametrize("ci", ["github", "gitlab"])
@pytest.mark.parametrize("tree", [(_PATHS, _FILES), _GRADLE, _RUBY])
def test_every_snippet_is_valid_yaml(tree, ci: str) -> None:
    content = ci_snippet(detect_stacks(*tree), ci, "main")["content"]

    assert content is None or isinstance(yaml.safe_load(content), dict)


def test_gitlab_skips_a_unit_whose_command_needs_its_actions_step() -> None:
    (stack,) = detect_stacks(*_GRADLE)
    assert stack.shell == "github-actions"

    gitlab = ci_snippet([stack], "gitlab", "main")

    assert gitlab["content"] is None
    assert "github-actions step" in gitlab["skipped"][0]["reason"]
    assert ci_snippet([stack], "github", "main")["content"] is not None


def test_gitlab_installs_the_ruby_bundle() -> None:
    doc = yaml.safe_load(ci_snippet(detect_stacks(*_RUBY), "gitlab", "main")["content"])

    assert doc["repowise-evidence"]["script"][0] == "bundle install"


def test_units_whose_roots_slug_alike_keep_their_own_artifact() -> None:
    package = json.dumps({"name": "w", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "1"}})
    paths = [f"{root}/{name}" for root in ("app/web", "app-web") for name in ("package.json", "a.ts")]
    stacks = detect_stacks(paths, {"app/web/package.json": package, "app-web/package.json": package})

    jobs = yaml.safe_load(ci_snippet(stacks, "github", "main")["content"])["jobs"].values()

    names = [job["steps"][-1]["with"]["name"] for job in jobs]
    assert len(names) == 2 and len(set(names)) == 2


def test_gitlab_quotes_a_unit_root_with_a_space() -> None:
    stacks = detect_stacks(["my app/go.mod", "my app/main.go"], {"my app/go.mod": "module example.com/m\n"})

    doc = yaml.safe_load(ci_snippet(stacks, "gitlab", "main")["content"])

    assert doc["repowise-evidence"]["script"][0] == 'cd "my app"'
