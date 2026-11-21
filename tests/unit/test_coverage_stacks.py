"""Coverage stacks: the command, prerequisites and report each unit of a repository needs.

Each directory under ``fixtures/coverage_stacks`` is a small repository: real
manifests plus ``tree.txt`` for paths whose content does not matter.
``expected.json`` holds, per case, the root stack the GitHub setup flow has
always produced for it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

import pytest

from repowise.core.build_units import BuildUnit, BuildUnitIndex
from repowise.core.test_runners import stacks
from repowise.core.test_runners.packages import quote_arg
from repowise.core.test_runners.stacks import detect_repo_stacks, detect_stacks, files_to_read

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "coverage_stacks"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


def _listing(root: Path) -> list[str]:
    paths = [
        (Path(d) / name).relative_to(root).as_posix()
        for d, _, names in os.walk(root)
        for name in names
        if name != "tree.txt"
    ]
    tree = root / "tree.txt"
    if tree.is_file():
        paths += tree.read_text(encoding="utf-8").split()
    return sorted(paths)


def _load(case: str) -> tuple[list[str], dict[str, str]]:
    root = FIXTURES / case
    paths = _listing(root)
    files = {
        p: (root / p).read_text(encoding="utf-8")
        for p in files_to_read(paths)
        if (root / p).is_file()
    }
    return paths, files


def _write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_the_root_stack_matches_every_stack_fixture(case):
    stack = detect_stacks(*_load(case))[0]
    want = EXPECTED[case]
    assert stack.root == ""
    assert (stack.language, stack.runner, stack.package_manager, stack.reports) == (
        want["language"],
        want["runner"],
        want["package_manager"],
        want["reports"],
    )
    assert (stack.test or None) == want["test"]


@pytest.mark.parametrize(
    "case, line, expected, reports",
    [
        ("python_pip", "pytest -q", "pytest -q --cov=app --cov-report=xml --cov-context=test", "coverage.xml"),
        ("python_pip", "pytest --cov=app", "pytest --cov=app --cov-report=xml --cov-context=test", "coverage.xml"),
        ("python_pip", "pytest --cov=app --cov-report=xml:out/cov.xml", None, "out/cov.xml"),
        ("python_pip", "pytest tests \\", "pytest --cov=app --cov-report=xml --cov-context=test tests \\", "coverage.xml"),
        ("python_uv_workspace", "uv run pytest -x && echo ok",
         "uv run --with pytest-cov pytest -x --cov=packages --cov-report=xml --cov-context=test && echo ok", "coverage.xml"),
        ("node_jest_npm", "npm test -- --ci", "npm test -- --ci --coverage --coverageReporters=lcov",
         "coverage/lcov.info"),
        ("node_jest_npm", "npx jest --ci", "npx jest --coverage --coverageReporters=lcov --ci", "coverage/lcov.info"),
        # pnpm reads ``pnpm test --flag`` as its own option and passes a ``--`` on to the script.
        ("node_vitest_pnpm", "pnpm test",
         "pnpm run test --coverage.enabled --coverage.reporter=lcov --coverage.reportOnFailure --coverage.reportsDirectory=coverage", "coverage/lcov.info"),
        ("node_vitest_pnpm", "pnpm test -- --ci",
         "pnpm run test --coverage.enabled --coverage.reporter=lcov --coverage.reportOnFailure --coverage.reportsDirectory=coverage -- --ci",
         "coverage/lcov.info"),
        ("node_mocha_yarn", "yarn test", "npx --yes c8 --reporter=lcov yarn test", "coverage/lcov.info"),
        ("go", "go test -race ./...", "go test -coverprofile=coverage.out -race ./...", "coverage.out"),
        ("go", "go test -coverprofile=c.out ./...", None, "c.out"),
        ("maven", "./mvnw -B verify", f"./mvnw {stacks.JACOCO}:prepare-agent -B verify {stacks.JACOCO}:report",
         "target/site/jacoco/jacoco.xml"),
        ("gradle_jacoco", "./gradlew build --scan",
         "./gradlew build jacocoTestReport --scan", "build/reports/jacoco/test/jacocoTestReport.xml"),
        ("rust", "cargo test --workspace", "cargo llvm-cov --lcov --output-path lcov.info --workspace", "lcov.info"),
        ("dotnet", "dotnet test -c Release",
         'dotnet test --collect:"XPlat Code Coverage" --results-directory coverage -c Release',
         "coverage/*/coverage.cobertura.xml"),
        ("php", "vendor/bin/phpunit", "vendor/bin/phpunit --coverage-clover clover.xml", "clover.xml"),
        ("ruby_cobertura", "bundle exec rake test", None, "coverage/coverage.xml"),
    ],
)
def test_coverage_is_added_to_a_test_command(case, line, expected, reports):
    edit = detect_stacks(*_load(case))[0].edit(line)
    assert (edit.text, edit.reports) == (expected or line, reports)


@pytest.mark.parametrize(
    "case, line",
    [
        ("node_jest_npm", "npm test --workspaces"),  # a monorepo run
        ("maven", "mvn -B verify \\"),  # the report goal would land on the next line
        ("gradle_plain", "./gradlew test"),  # no JaCoCo plugin to call
        ("gradle_jacoco", "./gradlew build -x test"),
    ],
)
def test_commands_it_will_not_guess_at(case, line):
    assert detect_stacks(*_load(case))[0].edit(line) is None


def test_tools_that_need_an_install_say_so():
    poetry = detect_stacks(*_load("python_poetry"))[0]
    assert poetry.edit("poetry run pytest").prepare == ("- run: poetry run pip install pytest-cov",)
    vitest = detect_stacks(*_load("node_vitest_pnpm"))[0]
    assert vitest.prepare == ('- run: pnpm add -D "@vitest/coverage-v8@^2.1.0"',)
    assert vitest.requires == ("pnpm install --frozen-lockfile", 'pnpm add -D "@vitest/coverage-v8@^2.1.0"')
    rust = detect_stacks(*_load("rust"))[0]
    assert rust.edit("cargo test").prepare == ("- uses: taiki-e/install-action@cargo-llvm-cov",)
    assert rust.requires == ("cargo install cargo-llvm-cov",)


@pytest.mark.parametrize(
    ("case", "install"),
    [
        ("python_pip", "python -m pip install -r requirements-dev.txt -r requirements.txt pytest pytest-cov"),
        ("python_hatch", "hatch run python -m pip install pytest pytest-cov"),
    ],
)
def test_a_workstation_installs_what_the_ci_setup_installs(case, install):
    """pip and hatch install pytest-cov in their CI setup lines, which a workstation never runs."""
    python = detect_stacks(*_load(case))[0]
    assert python.requires[-1] == install
    assert python.to_dict()["prerequisites"][-1] == install


_PROJECT = '[project]\nname = "pkg"\nversion = "0"\n'


@pytest.mark.parametrize(
    ("files", "install"),
    [
        # A flat package with a dev requirements file: the project and the file, not the docs one.
        ({"pyproject.toml": _PROJECT, "requirements-dev.txt": "pytest-httpbin\n", "requirements-docs.txt": "sphinx\n"},
         "-e . -r requirements-dev.txt pytest pytest-cov"),
        ({"setup.py": "", "requirements-py3-dev.txt": "x\n", "requirements-unit-tests.txt": "y\n"},
         "-e . -r requirements-py3-dev.txt -r requirements-unit-tests.txt pytest pytest-cov"),
        # "requirements" after the dev or test word; a docs file stays out either way.
        ({"setup.py": "", "dev-requirements.txt": "x\n", "test-requirements.txt": "y\n", "docs-requirements.txt": "z\n"},
         "-e . -r dev-requirements.txt -r test-requirements.txt pytest pytest-cov"),
        # setup.cfg extras: the test extra over dev.
        ({"setup.py": "from setuptools import setup\nsetup()\n",
          "setup.cfg": "[metadata]\nname = pkg\n\n[options.extras_require]\ndev =\n    flake8\ntest =\n    pytest\n"},
         '-e ".[test]" pytest pytest-cov'),
        # pyproject extras; a dependency group is not an extra pip can name.
        ({"pyproject.toml": _PROJECT + '[project.optional-dependencies]\ndev = ["pytest"]\n'
                                       '[dependency-groups]\ntest = ["pytest"]\n'},
         '-e ".[dev]" pytest pytest-cov'),
        # A pyproject that only configures tools, over a flat tree, is not planned as an install.
        ({"pyproject.toml": "[tool.ruff]\nline-length = 100\n", "requirements.txt": "flask\n"},
         "-r requirements.txt pytest pytest-cov"),
        # It still builds a src layout, or setup.cfg's metadata, through setuptools.
        ({"pyproject.toml": "[tool.ruff]\nline-length = 100\n", "src/lib/__init__.py": ""},
         "-e . pytest pytest-cov"),
        ({"pyproject.toml": "[tool.ruff]\n",
          "setup.cfg": "[metadata]\nname = pkg\n[options.extras_require]\ntest =\n    pytest\n"},
         '-e ".[test]" pytest pytest-cov'),
        # pip refuses a setup.cfg with neither setup.py nor pyproject.toml.
        ({"setup.cfg": "[metadata]\nname = pkg\n", "src/lib/__init__.py": "", "requirements-dev.txt": "x\n"},
         "-r requirements-dev.txt pytest pytest-cov"),
        # Test requirements in a requirements/ directory.
        ({"setup.py": "", "requirements/test.txt": "pytest\n", "requirements/docs.txt": "sphinx\n"},
         "-e . -r requirements/test.txt pytest pytest-cov"),
    ],
)
def test_a_pip_unit_installs_the_project_and_its_test_dependencies(files, install):
    paths = sorted([*files, "pkg/__init__.py", "tests/test_pkg.py"])
    (stack,) = detect_stacks(paths, {p: files[p] for p in files_to_read(paths) if p in files})
    assert stack.to_dict()["prerequisites"] == [f"python -m pip install {install}"]
    assert f"- run: pip install {install}   # adjust to your install" in stack.setup


def test_a_uv_test_group_is_synced_and_integration_tests_are_left_out():
    paths = sorted([
        "pyproject.toml", "uv.lock", "langchain_core/__init__.py",
        "tests/unit_tests/test_a.py", "tests/integration_tests/test_b.py",
    ])
    pyproject = _PROJECT + '[dependency-groups]\nlint = ["ruff"]\ntest = ["pytest"]\n'
    (stack,) = detect_stacks(paths, {"pyproject.toml": pyproject})
    assert stack.test == (
        "uv run --with pytest-cov --group test pytest tests/unit_tests "
        "--cov=langchain_core --cov-report=xml --cov-context=test"
    )
    assert stack.to_dict()["prerequisites"] == []


def test_a_uv_dev_group_needs_no_flag():
    # ``uv run`` syncs the dev group by default.
    paths = ["pyproject.toml", "uv.lock", "pkg/__init__.py", "tests/test_a.py"]
    (stack,) = detect_stacks(paths, {"pyproject.toml": _PROJECT + '[dependency-groups]\ndev = ["pytest-cov"]\n'})
    assert stack.test == "uv run pytest --cov=pkg --cov-report=xml --cov-context=test"


def test_python_records_test_contexts_and_sends_the_coverage_db_on_pushes():
    stack = detect_stacks(*_load("python_pip"))[0]
    assert "--cov-context=test" in stack.test and stack.push_reports == ".coverage"


@pytest.mark.parametrize("case", ["go", "node_jest_npm", "rust", "maven", "unknown"])
def test_other_stacks_send_only_their_aggregate_report(case):
    assert not detect_stacks(*_load(case))[0].push_reports


@pytest.mark.parametrize(
    ("line", "push"),
    [
        ("pytest --cov=app --cov-report=xml", ""),
        ("pytest --cov=app --cov-report=xml --cov-context=test", ".coverage"),
        ("pytest --cov=app --cov-context=test", ".coverage"),
    ],
)
def test_a_pytest_command_sends_the_coverage_db_only_when_it_records_contexts(line, push):
    edit = detect_stacks(*_load("python_pip"))[0].edit(line)
    assert edit.push_reports == push and edit.text.count("--cov-context=test") == (1 if push else 0)


def test_unknown_says_why_and_guesses_no_command():
    (stack,) = detect_stacks(*_load("unknown"))
    out = stack.to_dict()
    assert out["stack"] == "unknown" and out["command"] is None and out["reason"]


def test_a_unit_whose_runner_is_unclear_has_no_command_and_a_reason():
    out = detect_stacks(*_load("node_monorepo"))[0].to_dict()
    assert out["command"] is None and "monorepo" in out["reason"]


def test_workspace_members_are_run_by_the_workspace_root():
    # Cargo, Maven and Gradle members are built and tested from the root.
    for case in ("rust", "maven_jacoco_modules", "gradle_plain"):
        assert [s.root for s in detect_stacks(*_load(case))] == [""], case


def test_npm_workspace_members_get_their_own_stacks():
    # The root delegates to turbo, so members with tests are units; packages/b has none.
    root, member = detect_stacks(*_load("node_monorepo"))
    assert (root.root, member.root) == ("", "packages/a")
    # The member has no lockfile of its own: the workspace root's picks npm and its install.
    assert (member.package_manager, member.requires) == ("npm", ("npm ci", "npm install --no-save jest-junit@16"))


MONOREPO = {
    "README.md": "# mono\n",
    "services/api/pyproject.toml": '[project]\nname = "api"\n',
    "services/api/uv.lock": "",
    "services/api/api/__init__.py": "",
    "services/api/tests/test_api.py": "def test_x():\n    pass\n",
    "services/worker/go.mod": "module example.com/worker\n\ngo 1.22\n",
    "services/worker/main.go": "package main\n",
    "services/worker/main_test.go": "package main\n",
    "tests/fixtures/sample/package.json": "{}",
    "docs/site/package.json": "{}",
}


def _assert_members(found: list[stacks.Stack]) -> None:
    by_root = {s.root: s.to_dict() for s in found}
    assert set(by_root) == {"services/api", "services/worker"}
    api, worker = by_root["services/api"], by_root["services/worker"]
    assert (api["stack"], api["package_manager"]) == ("python", "uv")
    assert api["command"] == "uv run --with pytest-cov pytest --cov=api --cov-report=xml --cov-context=test"
    assert api["reports"] == ["services/api/coverage.xml"]
    assert worker["stack"] == "go" and worker["command"] == "go test -coverprofile=coverage.out ./..."
    assert worker["reports"] == ["services/worker/coverage.out"]


def test_monorepo_members_are_found_from_the_listing():
    paths = sorted(MONOREPO)
    files = {p: MONOREPO[p] for p in files_to_read(paths)}
    _assert_members(detect_stacks(paths, files))


def test_monorepo_members_come_from_the_build_unit_index(tmp_path):
    _write(tmp_path, MONOREPO)
    units = BuildUnitIndex.from_units(
        [
            BuildUnit("python:api", "python", "api", "services/api", "services/api/pyproject.toml", "pyproject"),
            BuildUnit("go:example.com/worker", "go", "example.com/worker", "services/worker",
                      "services/worker/go.mod", "go-mod"),
        ],
        sorted(MONOREPO),
    )
    _assert_members(detect_repo_stacks(tmp_path, units))


def test_the_index_decides_which_manifests_are_units(tmp_path):
    # A package.json the index ruled out (outside the npm workspace) starts no stack.
    files = {**MONOREPO, "tools/script/package.json": '{"scripts": {"test": "jest"}}'}
    _write(tmp_path, files)
    units = BuildUnitIndex.from_units(
        [BuildUnit("go:w", "go", "w", "services/worker", "services/worker/go.mod", "go-mod")], sorted(files)
    )
    assert [s.root for s in detect_repo_stacks(tmp_path, units)] == ["services/worker"]
    assert "tools/script" in {s.root for s in detect_repo_stacks(tmp_path)}


def test_an_empty_index_counts_as_none(tmp_path):
    _write(tmp_path, MONOREPO)
    _assert_members(detect_repo_stacks(tmp_path, BuildUnitIndex()))


def test_to_dict_is_json_ready():
    for found in (detect_stacks(*_load("python_uv_workspace")), detect_stacks(*_load("gradle_plain"))):
        for stack in found:
            assert json.loads(json.dumps(stack.to_dict())) == stack.to_dict()
    # Its CI command needs the init script its CI step writes; a workstation runs the shipped copy.
    gradle = detect_stacks(*_load("gradle_plain"))[0]
    assert gradle.shell == "github-actions" and "$RUNNER_TEMP" in gradle.test
    shown = gradle.to_dict()
    script = quote_arg(stacks.JACOCO_INIT_SCRIPT.as_posix())
    assert shown["command"] == f"./gradlew test jacocoTestReport --init-script {script}"
    assert shown["shell"] is None and shown["reason"] is None


def _index(*units: tuple[str, str, str]) -> BuildUnitIndex:
    return BuildUnitIndex.from_units(
        [BuildUnit(f"{eco}:{root}", eco, root, root, manifest, eco) for eco, root, manifest in units], []
    )


def test_a_virtual_cargo_workspace_runs_its_members_with_an_index(tmp_path):
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["a"]\n',
        "a/Cargo.toml": '[package]\nname = "a"\n',
        "a/src/lib.rs": "",
    }
    _write(tmp_path, files)
    (stack,) = detect_repo_stacks(tmp_path, _index(("cargo", "a", "a/Cargo.toml")))
    assert (stack.root, stack.test) == ("", "cargo llvm-cov --workspace --lcov --output-path lcov.info")


def test_a_gradle_settings_root_runs_its_projects_with_an_index(tmp_path):
    files = {
        "settings.gradle.kts": 'include("app")\n',
        "app/build.gradle.kts": "plugins { java\n jacoco }\n",
        "app/src/main/java/A.java": "",
    }
    _write(tmp_path, files)
    found = detect_repo_stacks(tmp_path, _index(("gradle", "app", "app/build.gradle.kts")))
    assert [(s.root, s.runner) for s in found] == [("", "gradle")]


def test_a_root_package_json_without_a_runner_beside_another_stack_is_tooling():
    paths = ["pyproject.toml", "package.json", "app/__init__.py"]
    files = {"pyproject.toml": "[project]\nname = 'x'\n", "package.json": "{}"}
    assert [s.language for s in detect_stacks(paths, files)] == ["python"]


def test_a_nested_solution_does_not_hide_the_root_solution():
    paths = ["A.sln", "src/B.sln", "A/A.csproj", "tests/A.Tests/A.Tests.csproj"]
    (stack,) = detect_stacks(paths, {})
    assert stack.test.startswith("dotnet test A.sln ")


def test_dotnet_without_a_solution_or_test_project_says_so():
    (stack,) = detect_stacks(["App/App.csproj", "App/Program.cs"], {})
    assert not stack.test and "no solution or test project" in stack.to_dict()["reason"]


@pytest.mark.parametrize(
    ("script", "test", "reports"),
    [
        # c8 and nyc write text and html by default: their report step writes lcov.
        ("c8 mocha", "npm test && npx --yes c8 report --reporter=lcov", "coverage/lcov.info"),
        ("nyc ava", "npm test && npx --yes nyc report --reporter=lcov", "coverage/lcov.info"),
        # An explicit lcov reporter already writes it.
        ("c8 --reporter=lcov mocha", "npm test", "coverage/lcov.info coverage/clover.xml"),
        ("nyc -r lcov ava", "npm test", "coverage/lcov.info coverage/clover.xml"),
        ("c8 --reporter=text-lcov mocha", "npm test && npx --yes c8 report --reporter=lcov", "coverage/lcov.info"),
    ],
)
def test_c8_and_nyc_scripts_write_an_lcov_report(script, test, reports):
    files = {"package.json": json.dumps({"scripts": {"test": script}})}
    (stack,) = detect_stacks(["package.json", "package-lock.json"], files)
    assert (stack.test, stack.reports) == (test, reports)


def test_testpaths_come_from_the_config_pytest_picks():
    # pytest.ini governs even with no options, so pyproject's testpaths are never read.
    paths = ["pytest.ini", "pyproject.toml", "tests/unit/test_a.py", "tests/integration/test_b.py"]
    pyproject = '[project]\nname = "x"\n[tool.pytest.ini_options]\ntestpaths = ["other"]\n'
    (stack,) = detect_stacks(paths, {"pytest.ini": "", "pyproject.toml": pyproject})
    assert stack.test.startswith("pytest tests/unit ")
    (stack,) = detect_stacks(paths[1:], {"pyproject.toml": pyproject})
    assert stack.test.startswith("pytest --cov")


def test_the_dotnet_sdk_covers_the_app_projects_too():
    files = {
        "src/App/App.csproj": "<Project><PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>",
        "tests/App.Tests/App.Tests.csproj": (
            "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>"
            '<ItemGroup><PackageReference Include="coverlet.collector" /></ItemGroup></Project>'
        ),
    }
    paths = ["App.sln", *files]
    assert set(files) <= set(files_to_read(paths))
    (stack,) = detect_stacks(paths, files)
    assert "      8.0.x" in stack.setup and "      9.0.x" in stack.setup
    assert not any("coverlet" in note for note in stack.notes)


# ---------------------------------------------------------------------------
# JavaScript workspaces, installs and runners, in the layouts real repositories use
# ---------------------------------------------------------------------------

_VITEST = "--coverage.enabled --coverage.reporter=lcov --coverage.reportOnFailure --coverage.reportsDirectory=coverage"


def _package(scripts: dict | None = None, **fields) -> str:
    return json.dumps({"name": "x", **({"scripts": scripts} if scripts is not None else {}), **fields})


PNPM_WORKSPACE = {
    "package.json": _package({"test": "vitest"}, private=True,
                             devDependencies={"vitest": "^3", "@vitest/coverage-v8": "^3"}),
    "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n",
    "pnpm-lock.yaml": "",
    "vitest.config.ts": "",
    "packages/reactivity/package.json": _package(),
    "packages/reactivity/src/index.ts": "",
    "packages/shared/package.json": _package({"build": "tsc"}),
    "packages/shared/src/index.ts": "",
}


def test_a_pnpm_workspace_root_runs_its_packages_tests(tmp_path):
    _write(tmp_path, PNPM_WORKSPACE)
    members = _index(("npm", "packages/reactivity", "packages/reactivity/package.json"),
                     ("npm", "packages/shared", "packages/shared/package.json"))
    # The index lists only the members; the root's vitest still runs them all.
    for found in (detect_repo_stacks(tmp_path, members), detect_repo_stacks(tmp_path)):
        (stack,) = found
        assert (stack.root, stack.package_manager, stack.runner) == ("", "pnpm", "vitest")
        assert stack.test == f"pnpm run test {_VITEST}"
        assert stack.requires == ("pnpm install --frozen-lockfile",)


def test_a_workspaces_root_with_its_own_runner_runs_the_members_too():
    files = {
        "package.json": _package({"test": "vitest run"}, workspaces=["packages/*"]),
        "packages/zod/package.json": _package({"test": "vitest run"}),
        "packages/docs/package.json": _package({"dev": "next dev"}),
    }
    paths = [*files, "pnpm-lock.yaml"]
    (stack,) = detect_stacks(paths, files)
    assert (stack.root, stack.package_manager, stack.test) == ("", "pnpm", f"pnpm run test {_VITEST}")


@pytest.mark.parametrize("script", ["yarn workspaces run test", "node scripts/test.js"])
def test_a_workspaces_root_without_a_known_runner_leaves_the_members_theirs(script):
    files = {
        "package.json": _package({"test": script}, workspaces=["packages/*"]),
        "packages/a/package.json": _package({"test": "vitest"}, devDependencies={"@vitest/coverage-v8": "1"}),
    }
    found = detect_stacks([*files, "yarn.lock"], files)
    member = next(s for s in found if s.root == "packages/a")
    assert (member.package_manager, member.test) == ("yarn", f"yarn test {_VITEST}")


def test_a_package_with_no_lockfile_installs_before_its_tests():
    files = {"package.json": _package({"test": "mocha --check-leaks test/"}, devDependencies={"mocha": "1"})}
    (stack,) = detect_stacks(["package.json", "test/app.js"], files)
    assert stack.to_dict()["prerequisites"] == ["npm install"]
    assert stack.test == "npx --yes c8 --reporter=lcov npm test"


@pytest.mark.parametrize(
    ("script", "runner", "command"),
    [
        # Arguments land on the last command of a chain, where the runner is.
        ("tsc -p tsconfig.spec.json && vp test --run", "vitest", f"pnpm run test {_VITEST}"),
        ("tsc -p tsconfig.json && bun test", "bun", "pnpm run test --coverage --coverage-reporter=lcov"),
        ("cross-env CI=1 jest", "jest", "pnpm run test --coverage --coverageReporters=lcov"),
        # A runner before the last command would run without the flags.
        ("vitest run && node scripts/check.js", None, ""),
        ("jest && vitest run", None, ""),
    ],
)
def test_a_runner_at_the_end_of_a_chain_is_recognised(script, runner, command):
    files = {"package.json": _package({"test": script}, devDependencies={"@vitest/coverage-v8": "1"})}
    (stack,) = detect_stacks(["package.json", "pnpm-lock.yaml"], files)
    assert (stack.runner, stack.test) == (runner, command)


@pytest.mark.parametrize(
    ("script", "runner"),
    [
        # An exec wrapper runs the command after its own flags, whatever the tool.
        ("nub exec --node vitest run", "vitest"),
        ("pnpm exec vitest run", "vitest"),
        ("npm exec -- jest --ci", "jest"),
        ("npx --yes vitest", "vitest"),
        ("bunx vitest run", "vitest"),
        ("cross-env CI=1 yarn exec jest", "jest"),
        # A flag that takes a value leaves the value where the runner would be.
        ("npx -p vitest node run.js", None),
        ("npx --package=vitest vitest run", None),
        # Only ``-p`` and ``--package`` themselves: a longer flag starting the same way is a plain flag.
        ("npx --package-lock=false vitest run", "vitest"),
        ("npx -p-x vitest run", "vitest"),
        ("npm exec --prefix pkg vitest", None),
        # The wrapped command keeps its own reading: c8 still gets its lcov report step.
        ("npx c8 mocha", "report:c8"),
        ("bundle exec rspec", None),
    ],
)
def test_an_exec_wrapper_runs_the_runner_it_names(script, runner):
    files = {"package.json": _package({"test": script}, devDependencies={"@vitest/coverage-v8": "1"})}
    (stack,) = detect_stacks(["package.json", "pnpm-lock.yaml"], files)
    assert stack.runner == runner
    if runner == "vitest":
        assert stack.test == f"pnpm run test {_VITEST}"


def test_vitest_writes_its_report_where_the_ingest_line_looks():
    # The config's own directory would put lcov.info under coverage/raw/default.
    files = {
        "package.json": _package({"test": "vitest run"}, devDependencies={"@vitest/coverage-v8": "1"}),
        "vitest.config.ts": "export default { test: { coverage: { reportsDirectory: './coverage/raw/default' } } }",
    }
    (stack,) = detect_stacks([*files, "pnpm-lock.yaml"], files)
    assert stack.test.endswith(" --coverage.reportsDirectory=coverage")
    assert stack.reports == "coverage/lcov.info"


def test_a_test_script_chaining_other_scripts_falls_back_to_the_one_running_the_runner():
    scripts = {
        "test": "npm-run-all build lint test:unit",
        "test:unit": "run-p test:vitest:min test:ts",
        "test:vitest": "cross-env COVERAGE=true vitest run",
        "test:vitest:min": "cross-env MINIFY=true vitest run",
        "test:vitest:watch": "vitest",
        "test:ts": "tsc -p test/ts/",
    }
    files = {"package.json": _package(scripts, devDependencies={"@vitest/coverage-v8": "1"})}
    (stack,) = detect_stacks(["package.json", "pnpm-lock.yaml"], files)
    assert stack.test == f"pnpm run test:vitest {_VITEST}"
    # Two runners among the candidates is a guess too far.
    scripts["test:e2e"] = "jest -c e2e"
    files = {"package.json": _package(scripts)}
    (stack,) = detect_stacks(["package.json", "pnpm-lock.yaml"], files)
    assert stack.test == "" and stack.to_dict()["reason"]


@pytest.mark.parametrize("scripts", [{"build": "vite build"}, {"test": 'echo "Error: no test specified" && exit 1'}])
def test_a_package_with_no_tests_is_no_unit(scripts):
    files = {"go.mod": "module example.com/app\n", "static/admin/package.json": _package(scripts)}
    paths = [*files, "main.go", "main_test.go", "static/admin/yarn.lock"]
    assert [(s.root, s.language) for s in detect_stacks(paths, files)] == [("", "go")]


def test_npm_passes_flags_after_a_separator_and_yarn_as_they_are():
    files = {"package.json": _package({"test": "vitest run"}, devDependencies={"@vitest/coverage-v8": "1"})}
    (npm,) = detect_stacks(["package.json", "package-lock.json"], files)
    (yarn,) = detect_stacks(["package.json", "yarn.lock"], files)
    assert npm.test == f"npm test -- {_VITEST}" and npm.requires == ("npm ci",)
    assert yarn.test == f"yarn test {_VITEST}" and yarn.requires == ("yarn install --frozen-lockfile",)


def test_a_pnpm_workspace_root_adds_the_coverage_provider_to_the_root():
    files = {"package.json": _package({"test": "vitest"}, devDependencies={"vitest": "3.0.0"})}
    (stack,) = detect_stacks(["package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"],
                             {**files, "pnpm-workspace.yaml": "packages: []\n"})
    assert stack.requires == ("pnpm install --frozen-lockfile", 'pnpm add -D -w "@vitest/coverage-v8@3.0.0"')


_BERRY_LOCK = (
    "# This file is generated by running \"yarn install\" inside your project.\n"
    "# Manual changes might be lost - proceed with caution!\n\n"
    "__metadata:\n  version: 8\n  cacheKey: 10c0\n"
)
_CLASSIC_LOCK = "# THIS IS AN AUTOGENERATED FILE. DO NOT EDIT THIS FILE DIRECTLY.\n# yarn lockfile v1\n"


@pytest.mark.parametrize(
    ("extra", "package_manager", "install", "add"),
    [
        # Yarn classic refuses to add to a workspace root without -W.
        ({}, None, "yarn install --frozen-lockfile", "yarn add -D -W"),
        # Yarn Berry has no -W; its config file or packageManager field says it is Berry.
        ({".yarnrc.yml": "nodeLinker: node-modules\n"}, None, "yarn install --immutable", "yarn add -D"),
        ({}, "yarn@4.5.0", "yarn install --immutable", "yarn add -D"),
        ({}, "yarn@1.22.22", "yarn install --frozen-lockfile", "yarn add -D -W"),
        # With neither, the lockfile tells: a Berry one opens with __metadata.
        ({"yarn.lock": _BERRY_LOCK}, None, "yarn install --immutable", "yarn add -D"),
        ({"yarn.lock": _CLASSIC_LOCK}, None, "yarn install --frozen-lockfile", "yarn add -D -W"),
    ],
)
def test_a_yarn_workspace_root_adds_with_the_flag_its_yarn_needs(tmp_path, extra, package_manager, install, add):
    fields = {"workspaces": ["packages/*"], **({"packageManager": package_manager} if package_manager else {})}
    files = {"package.json": _package({"test": "jest"}, devDependencies={"jest": "29", "vitest": "1"}, **fields),
             **extra}
    (stack,) = detect_stacks(["yarn.lock", *files], files)
    assert stack.requires == (install, f"{add} jest-junit@16")


def test_a_berry_lockfile_above_a_member_is_read_from_the_checkout(tmp_path):
    # The member's install comes from the workspace root's lockfile, and only its head is read.
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(
        _package({"test": "vitest run"}, devDependencies={"vitest": "1"}), encoding="utf-8"
    )
    (tmp_path / "yarn.lock").write_text(_BERRY_LOCK + '\n"left-pad@npm:1.3.0":\n' * 5000, encoding="utf-8")
    assert "yarn.lock" in files_to_read(["yarn.lock", "web/package.json"])
    (stack,) = detect_repo_stacks(tmp_path)
    assert stack.requires[0] == "yarn install --immutable"


def test_a_yarn_member_adds_without_the_root_flag():
    files = {"web/package.json": _package({"test": "vitest run"}, devDependencies={"vitest": "1"})}
    (stack,) = detect_stacks(["yarn.lock", "web/package.json"], files)
    assert stack.requires[-1] == 'yarn add -D "@vitest/coverage-v8@1"'


# ---------------------------------------------------------------------------
# Fixtures, stubs and samples are not units
# ---------------------------------------------------------------------------


def test_a_manifest_in_a_testdata_directory_is_no_unit():
    files = {
        "go.mod": "module example.com/scanner\n",
        "cmd/scanner/update/testdata/pom.xml": "<project/>",
        "cmd/scanner/scan/image/testdata/java-fixture/app/pom.xml": "<project/>",
        "internal/analysis/testdata/rust/rust-project/Cargo.toml": '[package]\nname = "p"\n',
    }
    paths = [*files, "main.go", "main_test.go"]
    assert [(s.root, s.language) for s in detect_stacks(paths, files)] == [("", "go")]


def test_a_go_mod_with_no_module_line_or_no_go_files_is_no_unit():
    files = {
        "go.mod": "module example.com/site\n",
        "internal/warpc/genavif/go.mod": "module example.com/site/internal/warpc/genavif\n\ngo 1.21\n",
        "internal/warpc/genwebp/go.mod": "",
        "internal/warpc/gen/go.mod": "module example.com/site/internal/warpc/gen\n",
    }
    paths = [*files, "main.go", "internal/warpc/genavif/avif.c", "internal/warpc/genwebp/webp.c",
             "internal/warpc/gen/gen.go"]
    assert [s.root for s in detect_stacks(paths, files)] == ["", "internal/warpc/gen"]


def test_dotnet_runs_the_root_solution_not_a_samples_one_and_names_the_sdk_pin():
    files = {"global.json": json.dumps({"sdk": {"version": "11.0.100"}})}
    paths = ["Polly.slnx", "samples/Samples.slnx", "global.json", "src/Polly/Polly.csproj",
             "test/Polly.Specs/Polly.Specs.csproj"]
    (stack,) = detect_stacks(paths, files)
    assert stack.test.startswith("dotnet test Polly.slnx ")
    assert "11.0.100" in stack.notes[0]


def test_dotnet_prefers_the_solution_at_its_root_over_nested_ones():
    paths = ["Clean.Architecture.slnx", "MinimalClean/MinimalClean.Architecture.slnx",
             "sample/NimblePros.SampleToDo.slnx", "src/Web/Web.csproj"]
    (stack,) = detect_stacks(paths, {})
    assert stack.test.startswith("dotnet test Clean.Architecture.slnx ")


def test_dotnet_without_a_pin_names_no_sdk():
    # Target frameworks of the projects read are only a lower bound, so no SDK is named from them.
    files = {"src/App/App.csproj": "<TargetFramework>net11.0</TargetFramework>"}
    (stack,) = detect_stacks(["App.slnx", *files], files)
    assert not any("SDK" in note for note in stack.notes)


def test_a_go_module_is_judged_by_its_module_line_only_when_it_was_read():
    assert [s.language for s in detect_stacks(["go.mod", "main.go"], {})] == ["go"]


def test_a_module_in_a_test_directory_is_no_unit():
    # Its tests cover test code; the production units run their own.
    files = {"go.mod": "module example.com/app\n", "integration-tests/go.mod": "module example.com/it\n"}
    paths = [*files, "main.go", "integration-tests/it_test.go"]
    assert [s.root for s in detect_stacks(paths, files)] == [""]


@pytest.mark.parametrize("pin", ['{"sdk": "11.0.100"}', "[1]", "// comment\n{}", '{"sdk": {"version": 11}}'])
def test_an_odd_global_json_names_no_sdk(pin):
    files = {"global.json": pin, "App/App.csproj": "<TargetFramework>net8.0</TargetFramework>"}
    (stack,) = detect_stacks(["App.sln", *files], files)
    assert not any("SDK" in note for note in stack.notes)


def test_a_global_json_saved_with_a_bom_still_names_its_pin():
    files = {"global.json": "\ufeff" + json.dumps({"sdk": {"version": "10.0.100"}})}
    (stack,) = detect_stacks(["App.sln", "global.json"], files)
    assert "10.0.100" in stack.notes[0]


def test_a_go_module_whose_go_code_is_all_in_nested_modules_is_no_unit():
    files = {"go.mod": "module example.com/tools\n", "a/go.mod": "module example.com/a\n"}
    paths = [*files, "a/a.go", "a/a_test.go"]
    assert [s.root for s in detect_stacks(paths, files)] == ["a"]


def test_the_shipped_jacoco_init_script_is_the_one_the_workflow_writes():
    assert stacks.JACOCO_INIT_SCRIPT.is_file()
    body = stacks.JACOCO_INIT_SCRIPT.read_text(encoding="utf-8").splitlines()
    (stack,) = detect_stacks(*_load("gradle_plain"))
    assert [line[4:] for line in stack.prepare[3:-1]] == body
    assert "apply plugin: 'jacoco'" in body[2] and "xml.required = true" in body[3]


@pytest.mark.parametrize(
    ("scripts", "command"),
    [
        # Two suites with one runner: neither alone is the whole run.
        ({"test": "run-s test:unit test:integration", "test:unit": "vitest run",
          "test:integration": "vitest run -c integration.config.ts"}, ""),
        ({"test": "npm-run-all test:*", "test:a": "jest", "test:b": "jest -c b"}, ""),
        # The one suite test names is the run, beside others it does not name.
        ({"test": "npm run lint && npm run test:unit", "test:unit": "vitest run",
          "test:smoke": "vitest run -c smoke.config.ts"}, f"pnpm run test:unit {_VITEST}"),
        # A Playwright suite writes no source coverage; the unit suite is what these flags can measure.
        ({"test": "run-s test:unit test:e2e", "test:unit": "vitest run", "test:e2e": "playwright test"},
         f"pnpm run test:unit {_VITEST}"),
    ],
)
def test_a_fallback_script_is_picked_only_when_it_is_the_whole_run(scripts, command):
    files = {"package.json": _package(scripts, devDependencies={"@vitest/coverage-v8": "1"})}
    (stack,) = detect_stacks(["package.json", "pnpm-lock.yaml"], files)
    assert stack.test == command



def test_a_shipped_test_utils_package_is_a_unit():
    # The manifest makes test-utils/ a package root, which code_origin keeps production.
    files = {"package.json": _package({"test": "vitest run"}),
             "test-utils/package.json": _package({"test": "vitest run"}),
             "x/testdata/app/package.json": _package({"test": "vitest run"})}
    paths = [*files, "test-utils/src/index.js", "pnpm-lock.yaml"]
    assert [s.root for s in detect_stacks(paths, files)] == ["", "test-utils"]


def test_a_dotnet_unit_below_the_root_reads_the_nearest_sdk_pin_above_it():
    # dotnet walks up from the working directory to the first global.json.
    files = {
        "global.json": json.dumps({"sdk": {"version": "10.0.100"}}),
        "src/Svc/Svc.sln": "",
        "src/Svc/App/App.csproj": "<TargetFramework>net8.0</TargetFramework>",
    }
    paths = list(files)
    assert "global.json" in files_to_read(paths)
    (stack,) = detect_stacks(paths, {p: files[p] for p in files_to_read(paths)})
    assert stack.root == "src/Svc" and "10.0.100" in stack.notes[0]
    # A nearer pin wins.
    files["src/Svc/global.json"] = json.dumps({"sdk": {"version": "9.0.100"}})
    paths = list(files)
    (stack,) = detect_stacks(paths, {p: files[p] for p in files_to_read(paths)})
    assert "9.0.100" in stack.notes[0]


@pytest.mark.parametrize(
    ("installed", "argument"),
    [
        ("/opt/py lib/repowise/jacoco.gradle", '"/opt/py lib/repowise/jacoco.gradle"'),
        ("C:/Users/a b/repowise/jacoco.gradle", '"C:/Users/a b/repowise/jacoco.gradle"'),
        # A shell would expand `$u`, so the path is single-quoted like any other.
        ("/home/$u/jacoco.gradle", "'/home/$u/jacoco.gradle'"),
    ],
)
def test_the_init_script_path_is_quoted_like_every_setup_path(monkeypatch, installed, argument):
    monkeypatch.setattr(stacks, "JACOCO_INIT_SCRIPT", PurePosixPath(installed))
    # The fake path has no file to read: keep the cached CI step from reading it on a cold cache.
    monkeypatch.setattr(stacks, "_gradle_init", lambda: ())
    (stack,) = detect_stacks(*_load("gradle_plain"))
    assert stack.to_dict()["command"] == f"./gradlew test jacocoTestReport --init-script {argument}"


_MONOREPO = {
    "backend/requirements.txt": "fastapi\n",
    "backend/requirements-dev.txt": "pytest\npytest-cov\n",
    "backend/pytest.ini": "[pytest]\nmarkers =\n    e2e: end to end\n",
    "backend/app.py": "",
    "backend/tests/test_app.py": "",
    # A deploy's dependencies, no tests: not a unit to run.
    "backend/deploy/requirements.txt": "modal\n",
    "backend/deploy/app.py": "",
    # A script folder whose requirements name pytest has a suite.
    "scripts/avatar/requirements.txt": "pytest>=8\n",
    "scripts/avatar/run.py": "",
    "scripts/avatar/test_run.py": "",
}


def _monorepo_stacks(units: BuildUnitIndex | None = None) -> dict[str, str]:
    paths = sorted(_MONOREPO)
    files = {p: _MONOREPO[p] for p in files_to_read(paths, units)}
    return {s.root: s.test for s in detect_stacks(paths, files, units) if s.language == "python"}


def test_a_pip_application_in_a_subdirectory_runs_its_tests():
    stacks_by_root = _monorepo_stacks()
    assert set(stacks_by_root) == {"backend", "scripts/avatar"}
    (backend,) = [s for s in detect_stacks(sorted(_MONOREPO), _MONOREPO) if s.root == "backend"]
    assert backend.requires[-1] == "python -m pip install -r requirements-dev.txt -r requirements.txt pytest pytest-cov"
    assert backend.test.startswith("pytest --cov=")


def _backend(**changed: str):
    files = {**_MONOREPO, **{f"backend/{k.replace('__', '/').replace('_dot_', '.')}": v for k, v in changed.items()}}
    (backend,) = [s for s in detect_stacks(sorted(files), files) if s.root == "backend"]
    return backend


def test_a_tests_directory_is_named_when_other_test_files_would_be_collected():
    # A script folder's test_*.py beside backend/tests/ would join a bare pytest run.
    backend = _backend(scripts__test_seed_dot_py="")
    assert backend.test.startswith("pytest tests --cov=") and backend.test_paths == ("tests",)


@pytest.mark.parametrize(
    "other",
    [
        "services__tests__test_billing_dot_py",  # a package's own suite keeps running
        "vendor__lib__test_lib_dot_py",  # a vendored file is not ours
        "test_smoke_dot_py",  # a root test file keeps running
    ],
)
def test_a_bare_run_stays_when_the_other_test_files_are_suites_or_not_ours(other):
    backend = _backend(**{other: ""})
    assert backend.test_paths == ()


def test_xdist_named_only_outside_the_install_adds_no_flag():
    backend = _backend(**{"tox_dot_ini": "[testenv]\ndeps = pytest-xdist\n"})
    assert "-n auto" not in backend.test


@pytest.mark.parametrize(
    ("pyproject", "flag"),
    [
        ('[project]\nname = "a"\n[project.optional-dependencies]\ntest = ["pytest-xdist"]\n', True),
        ('[project]\nname = "a"\n[project.optional-dependencies]\ntest = ["pytest"]\nperf = ["pytest-xdist"]\n', False),
        ('[dependency-groups]\ndev = ["pytest-xdist"]\n', True),
        ('[dependency-groups]\nlint = ["pytest-xdist"]\n', False),
        ('[tool.poetry.group.dev.dependencies]\npytest-xdist = "*"\n', True),
    ],
)
def test_xdist_counts_only_where_the_install_brings_it(pyproject, flag):
    backend = _backend(**{"requirements-dev_dot_txt": "pytest\n", "pyproject_dot_toml": pyproject})
    assert ("-n auto" in backend.test) is flag


def test_pytests_own_testpaths_keep_deciding():
    backend = _backend(scripts__test_seed_dot_py="", pytest_dot_ini="[pytest]\ntestpaths = tests\n")
    assert backend.test.startswith("pytest --cov=") and backend.test_paths == ()


def test_conflicting_pins_install_one_requirements_file_at_a_time():
    backend = _backend(**{"requirements_dot_txt": "fastapi\npytest==9.1.1\n",
                          "requirements-dev_dot_txt": "pytest==8.3.4\npytest-cov\n"})
    assert backend.requires == (
        "python -m pip install -r requirements.txt",
        "python -m pip install -r requirements-dev.txt pytest pytest-cov",
    )
    assert [line for line in backend.setup if "pip install" in line] == [
        "- run: pip install -r requirements.txt   # adjust to your install",
        "- run: pip install -r requirements-dev.txt pytest pytest-cov   # adjust to your install",
    ]
    assert any("pin pytest to different versions" in note for note in backend.notes)


@pytest.mark.parametrize(("dev", "flag"), [("pytest-xdist==3.6\n", True), ("pytest_xdist\n", True), ("pytest\n", False)])
def test_a_declared_xdist_spreads_the_coverage_run(dev, flag):
    backend = _backend(**{"requirements-dev_dot_txt": dev})
    assert (" -n auto " in backend.test) is flag
    # The suite profile runs in one process: its paths never carry the flag.
    assert backend.test_paths == ()


def test_a_configured_worker_count_is_left_alone():
    backend = _backend(**{"requirements-dev_dot_txt": "pytest-xdist\n", "pytest_dot_ini": "[pytest]\naddopts = -n 4\n"})
    assert "-n auto" not in backend.test


def test_the_build_unit_index_names_the_same_pip_applications():
    roots = ("backend", "backend/deploy", "scripts/avatar")
    index = BuildUnitIndex.from_units(
        [
            BuildUnit(f"manifest:{r}", "manifest", r.rsplit("/", 1)[-1], r, f"{r}/requirements.txt", "manifest-root")
            for r in roots
        ],
        sorted(_MONOREPO),
    )
    assert set(_monorepo_stacks(index)) == {"backend", "scripts/avatar"}


def test_a_root_requirements_file_keeps_its_pytest_guess():
    """The repository root's requirements file is the project's; only units below it need a suite."""
    paths = ["requirements.txt", "main.py", "test_main.py"]
    (stack,) = detect_stacks(paths, {"requirements.txt": "flask\n"})
    assert (stack.root, stack.language, stack.runner) == ("", "python", "pytest")


@pytest.mark.parametrize(
    "files",
    [
        {"api/requirements.txt": "pytest_mock\n"},  # packaging spells _ and - alike
        {"api/requirements.txt": "flask\n", "api/pytest.ini": "[pytest\naddopts = -q\n"},  # does not parse
        {"api/Requirements (1).TXT": "pytest\n"},  # any name the registry glob takes
    ],
)
def test_a_subdirectory_suite_is_seen_from_any_pytest_signal(files):
    paths = sorted([*files, "api/app.py"])
    stacks_found = detect_stacks(paths, {p: files[p] for p in files_to_read(paths) if p in files})
    assert [(s.root, s.language) for s in stacks_found] == [("api", "python")]


def test_a_root_note_named_like_requirements_does_not_make_a_python_unit():
    paths = ["package.json", "package-lock.json", "Requirements for launch.txt", "src/index.test.js"]
    files = {"package.json": json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}})}
    assert [s.language for s in detect_stacks(paths, files)] == ["node"]


def test_jest_writes_junit_through_jest_junit_with_repo_relative_files():
    files = {"web/package.json": _package({"test": "jest --ci"}, devDependencies={"jest": "29"})}
    (stack,) = detect_stacks(["web/package.json", "web/package-lock.json", "web/a.test.ts"], files)
    assert (stack.junit_report, stack.junit_flag) == ("junit.xml", "--reporters=default --reporters=jest-junit")
    # The classname names the test file; the prefix makes it repo-relative from the unit.
    assert stack.junit_vars() == "JEST_JUNIT_CLASSNAME={filepath} JEST_JUNIT_FILE_PATH_PREFIX=web/"
    # The reporter is installed for the run, and said why.
    assert stack.requires == ("npm ci", "npm install --no-save jest-junit@16")
    assert stack.prepare == ("- run: npm install --no-save jest-junit@16",)
    assert "jest-junit" in stack.notes[-1]


def test_jest_junit_in_the_package_needs_no_install_and_the_root_no_prefix():
    files = {"package.json": _package({"test": "jest"}, devDependencies={"jest": "29", "jest-junit": "16"})}
    (stack,) = detect_stacks(["package.json", "yarn.lock", "a.test.ts"], files)
    assert stack.junit_vars() == "JEST_JUNIT_CLASSNAME={filepath}"
    assert (stack.requires, stack.prepare, stack.notes) == (("yarn install --frozen-lockfile",), (), ())


def test_other_runners_need_no_junit_environment():
    files = {"package.json": _package({"test": "vitest run"}, devDependencies={"vitest": "1", "@vitest/coverage-v8": "1"})}
    (stack,) = detect_stacks(["package.json", "package-lock.json"], files)
    assert stack.junit_vars() == "" and stack.junit_flag.startswith("--reporter=default")


@pytest.mark.parametrize(
    ("lock", "script", "command"),
    [
        ("package-lock.json", "jest", "npx jest --coverage --coverageReporters=lcov"),
        ("package-lock.json", "jest --ci --runInBand", "npx jest --coverage --coverageReporters=lcov --ci --runInBand"),
        ("yarn.lock", "jest", "yarn jest --coverage --coverageReporters=lcov"),
        ("pnpm-lock.yaml", "vitest run", f"pnpm exec vitest run {_VITEST}"),
        # Not the runner alone: the script stays, hooks and all.
        ("package-lock.json", "cross-env CI=1 jest", "npm test -- --coverage --coverageReporters=lcov"),
        ("package-lock.json", "jest 'src/**'", "npm test -- --coverage --coverageReporters=lcov"),
        # A shell comment would swallow the flags appended after it, so the runner runs directly.
        ("package-lock.json", "jest # unit tests", "npx jest --coverage --coverageReporters=lcov"),
        # Not the runner alone and commented: no command, since every flag would land in the comment.
        ("package-lock.json", "cross-env CI=1 jest # unit tests", None),
        # A bare -- would turn the flags appended after it into test patterns.
        ("package-lock.json", "jest --", "npm test -- --coverage --coverageReporters=lcov"),
    ],
)
def test_a_test_script_with_hooks_runs_the_runner_directly(lock, script, command):
    scripts = {"pretest": "node scripts/check-tokens.js", "test": script}
    deps = {"jest": "29", "jest-junit": "16", "vitest": "1", "@vitest/coverage-v8": "1"}
    (stack,) = detect_stacks(["package.json", lock], {"package.json": _package(scripts, devDependencies=deps)})
    assert stack.test == (command or "")
    direct = command is not None and not command.startswith("npm test")
    assert any("would also run the `pretest` script" in n for n in stack.notes) is direct


@pytest.mark.parametrize(
    ("script", "command"),
    [
        ("jest # unit tests", "npx jest --coverage --coverageReporters=lcov"),
        ("vitest run  # CI", f"npx vitest run {_VITEST}"),
        # The comment is not read as the script's own flags.
        ("jest # add --coverage later", "npx jest --coverage --coverageReporters=lcov"),
        ("cross-env CI=1 jest # unit tests", ""),
        # A # inside quotes is no comment: the script stays.
        ('jest -t "a #b"', "npm test -- --coverage --coverageReporters=lcov"),
    ],
)
def test_a_commented_test_script_runs_the_runner_without_the_comment(script, command):
    deps = {"jest": "29", "jest-junit": "16", "vitest": "1", "@vitest/coverage-v8": "1"}
    (stack,) = detect_stacks(["package.json", "package-lock.json"],
                             {"package.json": _package({"test": script}, devDependencies=deps)})
    assert stack.test == command
    # No hooks were skipped, so no hooks note.
    assert not any("would also run" in n for n in stack.notes)


@pytest.mark.parametrize(
    ("lock", "script", "command"),
    [
        ("package-lock.json", "jest --coverage", "npm test --"),
        ("pnpm-lock.yaml", "jest --coverage", "pnpm run test"),
        ("yarn.lock", "jest --coverage --ci", "yarn test"),
    ],
)
def test_a_jest_script_that_measures_coverage_still_writes_test_timings(lock, script, command):
    files = {"package.json": _package({"test": script}, devDependencies={"jest": "29"})}
    (stack,) = detect_stacks(["package.json", lock], files)
    # The command takes the JUnit flag appended to it: npm needs its separator, pnpm its run form.
    assert (stack.runner, stack.test) == ("jest", command)
    assert (stack.junit_report, stack.junit_flag) == ("junit.xml", "--reporters=default --reporters=jest-junit")
    assert stack.requires[-1].endswith("jest-junit@16")


def test_a_script_that_measures_behind_c8_keeps_no_junit_flag():
    files = {"package.json": _package({"test": "c8 --reporter=lcov mocha"}, devDependencies={"mocha": "10"})}
    (stack,) = detect_stacks(["package.json", "package-lock.json"], files)
    assert (stack.runner, stack.junit_flag) == ("covered", "")


def test_a_test_script_without_hooks_stays_the_script():
    files = {"package.json": _package({"test": "jest"}, devDependencies={"jest": "29", "jest-junit": "16"})}
    (stack,) = detect_stacks(["package.json", "package-lock.json"], files)
    assert stack.test == "npm test -- --coverage --coverageReporters=lcov" and stack.notes == ()


def test_the_hooks_note_names_both_hooks_and_runs_the_first():
    scripts = {"pretest": "tsc", "test": "jest", "posttest": "node report.js"}
    files = {"package.json": _package(scripts, devDependencies={"jest": "29", "jest-junit": "16"})}
    (stack,) = detect_stacks(["package.json", "package-lock.json"], files)
    assert stack.notes == (
        "This runs the test runner directly: `npm test` would also run the `pretest` and `posttest` "
        "scripts. Run `npm run pretest` first when the tests need it.",
    )


def test_a_manifest_in_a_data_folder_is_no_unit(tmp_path):
    """A data folder is known from its contents, so only a checkout can tell."""
    files = {"go.mod": "module example.com/app\n", "main.go": "", "main_test.go": ""}
    for i in range(4):
        item = f"backend/data/problems/p{i}"
        files |= {f"{item}/case{n}.json": "{}" for n in range(12)}
        files |= {f"{item}/{name}": "" for name in ("python.py", "Solution.java", "go.go")}
    files["backend/data/problems/p0/package.json"] = _package({"test": "vitest run"})
    _write(tmp_path, files)
    paths = list(files)
    assert "backend/data/problems/p0" in {s.root for s in detect_stacks(paths, files)}
    assert [(s.root, s.language) for s in detect_repo_stacks(tmp_path)] == [("", "go")]
