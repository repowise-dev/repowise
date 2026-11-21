"""Runtime targets for ``evidence setup``: owners' entry points, benchmarks, recipes and ingest lines."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from repowise.core.analysis.published_packages import BuildFacts, go_main_packages
from repowise.core.code_origin import is_benchmark_function, is_benchmark_path
from repowise.core.evidence.runtime_setup import (
    ARGS_PLACEHOLDER,
    RuntimeInputs,
    load_runtime_inputs,
    runtime_targets,
)
from repowise.core.evidence.setup import evidence_setup, offered_classes, runtime_offer
from repowise.core.generation.flow_entries import console_scripts, python_scripts
from repowise.core.test_runners.stacks import detect_repo_stacks

SHA = "a" * 40


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch):
    """Register stand-in profile formats for one test; the registry is restored after."""
    from repowise.core.evidence.profile_formats import ProfileFormat, profile_formats

    def register(*names: str) -> None:
        for name in names:
            monkeypatch.setitem(profile_formats._impls, name, ProfileFormat(lambda _b: False, read=lambda _b: None))

    return register


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("benches/parse.rs", True),
        ("pkg/benchmarks/test_io.py", True),
        ("bench/run.go", True),
        ("bench_parse.py", True),
        ("src/parse.bench.ts", True),
        ("absl/mutex_benchmark.cc", True),
        ("src/bench.py", False),
        ("src/benchmark_utils.md", False),
        ("tests/test_parse.py", False),
    ],
)
def test_benchmark_paths(path: str, expected: bool) -> None:
    assert is_benchmark_path(path) is expected


@pytest.mark.parametrize(
    ("path", "name", "expected"),
    [
        ("work/work_test.go", "BenchmarkCrunch", True),
        ("work/work_test.go", "Benchmark", True),
        ("work/work_test.go", "Benchmark_small", True),
        ("work/work_test.go", "Benchmarking", False),
        ("work/work.go", "BenchmarkCrunch", False),
        ("benchmarks/test_io.py", "test_read", True),
        ("bench_io.py", "bench_read", True),
        ("bench_io.py", "helper", False),
        ("tests/test_io.py", "test_read", False),
        ("benches/parse.rs", "parse", False),
    ],
)
def test_benchmark_functions(path: str, name: str, expected: bool) -> None:
    assert is_benchmark_function(path, name) is expected


def test_console_scripts_keep_plain_targets_only(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\n[project.scripts]\n'
        'app = "app.cli:main"\nmod = "app.tool"\nbad = "app.cli:main; rm -rf /"\n'
    )

    assert console_scripts(tmp_path) == {"app": ("app.cli", "main"), "mod": ("app.tool", None)}
    assert python_scripts(tmp_path) == {"app": "app", "mod": "app"}


def _python(root: Path, *, scripts: str = 'app = "app.cli:main"\n', extra: str = "") -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "app"\n{extra}[project.scripts]\n{scripts}'
    )
    (root / "app").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "cli.py").write_text("def main():\n    return 0\n")


def _targets(root: Path, inputs: RuntimeInputs | None = None, commit: str | None = SHA) -> list[dict]:
    (stack,) = detect_repo_stacks(root)
    return runtime_targets(stack, root, inputs or RuntimeInputs(), commit)


def test_a_console_script_is_profiled_in_process_before_the_test_suite(tmp_path: Path) -> None:
    _python(tmp_path)

    entry, tests = _targets(tmp_path)

    assert (entry["class"], entry["workload"]) == ("entry_point", "app")
    assert "from app.cli import main;sys.exit(main())" in entry["command"]
    assert "p=Profiler(interval=0.01)" in entry["command"]
    assert entry["command"].startswith('python -c "') and entry["command"].count('"') == 2
    # Nothing sh or PowerShell expands inside double quotes.
    assert not any(ch in entry["command"] + tests["command"] for ch in "$`!")
    # The unit's test install brings the project and pytest; the profiler comes on top.
    install = ["python -m pip install -e . pytest pytest-cov", "python -m pip install pyinstrument"]
    assert entry["prerequisites"] == install
    assert entry["ingest"] == (
        f'repowise evidence add .repowise/app.speedscope.json --workload "app <args>" '
        f"--class entry_point --commit {SHA}"
    )
    assert tests["class"] == "unit_test"
    assert "pytest.main" in tests["command"]
    assert tests["prerequisites"] == install


def test_a_src_layout_is_installed_before_it_is_profiled(tmp_path: Path) -> None:
    # A pyproject that only configures tools: the package imports only once installed.
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_lib.py").write_text("def test_x():\n    pass\n")

    (tests,) = _targets(tmp_path)

    assert tests["prerequisites"] == [
        "python -m pip install -e . pytest pytest-cov",
        "python -m pip install pyinstrument",
    ]


def test_an_entry_point_waits_for_its_arguments(tmp_path: Path) -> None:
    _python(tmp_path)

    entry, tests = _targets(tmp_path)

    # Run bare, a program measures its usage screen: the agent fills the placeholder.
    assert entry["command"].endswith(f'" {ARGS_PLACEHOLDER}')
    assert ARGS_PLACEHOLDER in entry["reason"] and entry["ingest"]
    assert tests["reason"] is None and not tests["command"].endswith(ARGS_PLACEHOLDER)
    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())
    assert plan["next_call"]["purpose"].startswith(
        "Profile python tests (unit_test), then ingest the profile. A subset of the tests is enough"
    )
    assert "outside Windows" in plan["next_call"]["purpose"]
    assert all(ARGS_PLACEHOLDER not in s["run"] for s in plan["next_call"]["steps"])


def test_a_python_main_module_the_index_flags_is_run_as_a_module(tmp_path: Path) -> None:
    _python(tmp_path, scripts="")
    (tmp_path / "app" / "__main__.py").write_text("print(1)\n")

    entry = _targets(tmp_path, RuntimeInputs(entry_files=("app/__main__.py",)))[0]

    assert entry["workload"] == "python -m app"
    # The run sits in a single-quoted literal the recipe ``exec``s.
    assert r"runpy.run_module(\'app\',run_name=\'__main__\',alter_sys=True)" in entry["command"]


def test_uv_runs_pyinstrument_without_an_install_step(tmp_path: Path) -> None:
    _python(tmp_path)
    (tmp_path / "uv.lock").write_text("")

    entry = _targets(tmp_path)[0]

    assert entry["command"].startswith("uv run --with pyinstrument python -c")
    assert entry["prerequisites"] == []


def test_pytest_benchmark_ranks_above_the_test_suite_and_runs_from_the_root(
    tmp_path: Path, registered
) -> None:
    _python(tmp_path, extra='dependencies = ["pytest-benchmark"]\n')
    registered("pytest-benchmark")

    classes = [t["class"] for t in _targets(tmp_path)]
    bench = _targets(tmp_path)[1]

    assert classes == ["entry_point", "benchmark", "unit_test"]
    assert bench["command"] == "pytest --benchmark-only --benchmark-json=.repowise/pytest-bench.json"
    assert bench["cwd"] == str(tmp_path)
    assert '--class benchmark' in bench["ingest"]


def test_pytest_benchmark_runs_in_the_units_environment(tmp_path: Path) -> None:
    # A uv unit below the root, its test tools in a dependency group (a monorepo library).
    unit = tmp_path / "libs" / "core"
    unit.mkdir(parents=True)
    _python(unit, scripts="", extra='dependencies = []\n[dependency-groups]\ntest = ["pytest", "pytest-benchmark"]\n')
    (unit / "uv.lock").write_text("")
    (stack,) = detect_repo_stacks(tmp_path)

    bench = next(t for t in runtime_targets(stack, tmp_path, RuntimeInputs(), SHA) if t["class"] == "benchmark")

    assert bench["command"] == (
        "uv run --project libs/core --group test pytest libs/core --benchmark-only "
        "--benchmark-json=.repowise/libs-core-pytest-bench.json"
    )
    assert bench["cwd"] == str(tmp_path)
    assert bench["prerequisites"] == ["git clean -fqx -- .repowise/libs-core-pytest-bench.json"]


def test_pytest_benchmark_at_the_root_installs_what_the_tests_need(tmp_path: Path) -> None:
    _python(tmp_path, scripts="", extra='dependencies = ["pytest-benchmark"]\n')

    bench = next(t for t in _targets(tmp_path) if t["class"] == "benchmark")

    assert bench["prerequisites"] == [
        "python -m pip install -e . pytest pytest-cov",
        "git clean -fqx -- .repowise/pytest-bench.json",
    ]


def test_a_stale_profile_is_removed_before_the_run(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".repowise/\n")
    _python(tmp_path, scripts="", extra='dependencies = ["pytest-benchmark"]\n')
    stale = tmp_path / ".repowise" / "pytest-bench.json"
    stale.parent.mkdir()
    stale.write_text("{}")
    (tmp_path / ".repowise" / "wiki.db").write_text("")
    bench = next(t for t in _targets(tmp_path) if t["class"] == "benchmark")

    # The same step in sh and PowerShell: git removes the one ignored file.
    subprocess.run(bench["prerequisites"][-1].split(), cwd=bench["cwd"], check=True)

    assert not stale.exists() and (tmp_path / ".repowise" / "wiki.db").exists()


def test_this_build_reads_pytest_benchmark_so_its_ingest_line_is_printed(tmp_path: Path) -> None:
    _python(tmp_path, scripts="", extra='dependencies = ["pytest-benchmark"]\n')

    bench = next(t for t in _targets(tmp_path) if t["class"] == "benchmark")
    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    assert bench["ingest"] == (
        'repowise evidence add .repowise/pytest-bench.json --workload "pytest bench" '
        f"--class benchmark --commit {SHA}"
    )
    assert bench["reason"] is None
    assert plan["next_call"]["purpose"] == "Profile pytest bench (benchmark), then ingest the profile."


def test_a_recipe_whose_output_this_build_cannot_read_prints_no_ingest_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.evidence.profile_formats import profile_formats

    monkeypatch.delitem(profile_formats._impls, "pytest-benchmark", raising=False)
    _python(tmp_path, scripts="", extra='dependencies = ["pytest-benchmark"]\n')

    bench = next(t for t in _targets(tmp_path) if t["class"] == "benchmark")
    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    assert bench["command"] and bench["ingest"] is None
    assert bench["reason"] == "This build of repowise does not read pytest-benchmark files yet."
    assert "bench" not in plan["next_call"]["purpose"]


def test_without_a_commit_nothing_can_be_ingested(tmp_path: Path) -> None:
    _python(tmp_path)

    entry = _targets(tmp_path, commit=None)[0]

    assert entry["ingest"] is None
    assert "commit" in entry["reason"]


def _go(root: Path) -> None:
    (root / "go.mod").write_text("module example.com/fx\n\ngo 1.22\n")
    (root / "work").mkdir()
    (root / "work" / "work.go").write_text("package work\n")
    (root / "work" / "work_test.go").write_text("package work\n")
    (root / "cmd" / "fx").mkdir(parents=True)
    (root / "cmd" / "fx" / "main.go").write_text("package main\n\nfunc main() {}\n")


def test_go_benchmarks_and_tests_profile_through_go_test(tmp_path: Path, registered) -> None:
    _go(tmp_path)
    registered("pprof")
    inputs = RuntimeInputs(
        benchmarks=(("work/work_test.go", "BenchmarkCrunch"),), test_files=("work/work_test.go",)
    )
    mains = go_main_packages(BuildFacts(tmp_path))

    main, bench, results, tests = _targets_go(tmp_path, inputs, mains)

    assert mains == ["cmd/fx"]
    assert (main["class"], main["command"], main["ingest"]) == ("entry_point", None, None)
    assert bench["command"] == (
        "go test '-run=^$' -bench . -benchmem -count=1 -o .repowise/go-bench-work.test "
        "-cpuprofile .repowise/go-bench-work.pprof ./work"
    )
    assert tests["command"] == (
        "go test -count=1 -o .repowise/go-test-work.test -cpuprofile .repowise/go-test-work.pprof ./work"
    )
    assert '--workload "go bench ./work" --class benchmark' in bench["ingest"]
    # The same benchmarks' printed results, allocations per op included, as a series.
    assert results["command"] == (
        "go test '-run=^$' -bench . -benchmem -count=1 ./work > .repowise/go-bench-results-work.txt"
    )
    assert results["ingest"] == (
        'repowise evidence add .repowise/go-bench-results-work.txt '
        f'--workload "go bench results ./work" --class benchmark --commit {SHA}'
    )
    # An older profile goes first, so a build that fails leaves nothing to ingest.
    assert bench["prerequisites"] == ["git clean -fqx -- .repowise/go-bench-work.pprof"]
    assert tests["prerequisites"] == ["git clean -fqx -- .repowise/go-test-work.pprof"]


def test_go_tests_profile_the_package_with_the_most_tests(tmp_path: Path, registered) -> None:
    _go(tmp_path)
    registered("pprof")
    # A shallow command package with one test, and a deeper package with the suite.
    inputs = RuntimeInputs(test_files=(
        "cmd/fx/main_test.go", "work/engine/a_test.go", "work/engine/b_test.go",
    ))

    tests = _targets_go(tmp_path, inputs, [])[-1]

    assert tests["workload"] == "go test ./work/engine"
    # go test -cpuprofile writes nothing when killed: no promise of a partial profile.
    assert "note" not in tests


def test_go_profiles_wait_for_a_pprof_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.evidence.profile_formats import profile_formats

    monkeypatch.delitem(profile_formats._impls, "pprof", raising=False)
    _go(tmp_path)
    inputs = RuntimeInputs(
        benchmarks=(("work/work_test.go", "BenchmarkCrunch"),), test_files=("work/work_test.go",)
    )

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=inputs)

    profiled = [t for t in plan["units"][0]["runtime"] if (t["profile"] or "").endswith(".pprof")]
    assert profiled and all(t["ingest"] is None for t in profiled)
    assert all(t["reason"] == "This build of repowise does not read pprof files yet." for t in profiled)
    # The benchmark results it can read lead the loop instead.
    assert "go-bench-results-work.txt" in json.dumps(plan["next_call"])


def test_a_nested_unit_keeps_its_own_targets(tmp_path: Path) -> None:
    _go(tmp_path)
    (tmp_path / "go.work").write_text("go 1.22\nuse .\nuse ./tools\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "go.mod").write_text("module example.com/tools\n\ngo 1.22\n")
    (tmp_path / "tools" / "t_test.go").write_text("package tools\n")
    inputs = RuntimeInputs(
        benchmarks=(("tools/t_test.go", "BenchmarkT"),), test_files=("tools/t_test.go",)
    )

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=inputs)

    by_root = {u["root"]: [t["workload"] for t in u["runtime"]] for u in plan["units"]}
    assert by_root[""] == ["go run ./cmd/fx"]
    assert by_root["tools"] == ["tools: go bench .", "tools: go bench results .", "tools: go test ."]


def test_units_never_share_a_workload_or_a_profile(tmp_path: Path) -> None:
    for root in ("a", "b"):
        (tmp_path / root).mkdir()
        _python(tmp_path / root)
    stacks = detect_repo_stacks(tmp_path)
    assert [s.root for s in stacks] == ["a", "b"]
    targets = [t for s in stacks for t in runtime_targets(s, tmp_path, RuntimeInputs(), SHA, (), {"", "a", "b"})]

    workloads = [t["workload"] for t in targets]
    profiles = [t["profile"] for t in targets]
    assert len(set(workloads)) == len(workloads) == len(set(profiles))
    assert "a: app" in workloads and "b: app" in workloads


def test_paths_a_shell_would_misread_never_reach_a_command(tmp_path: Path) -> None:
    _python(tmp_path, scripts="")
    inputs = RuntimeInputs(entry_files=("a$(id)/__main__.py", "app/__main__.py"))

    entry = _targets(tmp_path, inputs)[0]

    assert entry["workload"] == "python -m app"
    node = tmp_path / "node"
    node.mkdir()
    (node / "package.json").write_text(
        '{"name": "x", "bin": {"x": "bin/x.js; curl evil"}, "scripts": {"test": "jest"}}'
    )
    assert _targets(node) == []


def test_an_unknown_launcher_runs_plain_python(tmp_path: Path) -> None:
    from dataclasses import replace

    _python(tmp_path)
    (stack,) = detect_repo_stacks(tmp_path)

    launchers = {
        "uv run --extra dev pytest": "uv run --extra dev --with pyinstrument python -c",
        "uv run --with pytest-cov --group test -p 3.12 pytest": "uv run --group test -p 3.12 --with pyinstrument python -c",
        "uv run --frozen pytest": "uv run --frozen --with pyinstrument python -c",
        "poetry run --no-ansi python -m pytest": "poetry run --no-ansi python -c",
    }
    for test, launcher in launchers.items():
        entry = runtime_targets(replace(stack, test=test), tmp_path, RuntimeInputs(), SHA)[0]
        assert entry["command"].startswith(launcher), test
    for test in ("FOO=1 pytest", "cd x && pytest", "coverage run -m pytest"):
        entry = runtime_targets(replace(stack, test=test), tmp_path, RuntimeInputs(), SHA)[0]
        assert entry["command"].startswith('python -c "')
    poetry_stack = replace(stack, test="poetry run pytest", requires=())
    poetry = runtime_targets(poetry_stack, tmp_path, RuntimeInputs(), SHA)[0]
    assert poetry["command"].startswith('poetry run python -c "')
    assert poetry["prerequisites"] == ["poetry run python -m pip install pyinstrument"]


def _run_recipe(
    root: Path, main: str | None = None, cls: str = "entry_point", *args: str
) -> tuple[int, Path]:
    """Run a Python recipe with a stand-in profiler (no pyinstrument needed).

    The stand-in took one sample, of 10 ms, in ``main`` (pyinstrument's frame
    identifier: name, file and first line, then the line it was on).
    """
    import os
    import subprocess
    import sys

    if main is not None:
        (root / "app" / "cli.py").write_text(main)
    target = next(t for t in _targets(root) if t["class"] == cls)
    fake = root / "fake"
    (fake / "pyinstrument").mkdir(parents=True, exist_ok=True)
    (fake / "pyinstrument" / "__init__.py").write_text(
        "__version__ = '5.1.3'\n"
        "class _Session:\n"
        "    frame_records = [(['main' + chr(0) + 'app/cli.py' + chr(0) + '1' + chr(1) + 'l2'], 0.01)]\n"
        "class Profiler:\n    def __init__(self, interval=0.001): pass\n"
        "    def start(self): pass\n    def stop(self): return _Session()\n"
    )
    code = target["command"].split('-c "', 1)[1].rsplit('"', 1)[0]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(fake), str(root)])}
    done = subprocess.run(
        [sys.executable, "-c", code, *args], cwd=target["cwd"], env=env, capture_output=True,
        timeout=120,
    )
    return done.returncode, root / target["profile"]


#: What the recipe writes for the stand-in's one sample: sampled speedscope, so the count survives.
_SAMPLED = {
    "$schema": "https://www.speedscope.app/file-format-schema.json",
    "exporter": "pyinstrument@5.1.3",
    "profiles": [{
        "type": "sampled", "name": "python", "unit": "seconds", "startValue": 0,
        "endValue": 0.01, "samples": [[0]], "weights": [0.01],
    }],
    "shared": {"frames": [{"name": "main", "file": "app/cli.py", "line": 1}]},
}


def test_the_python_recipe_writes_its_profile_where_the_ingest_line_reads_it(tmp_path: Path) -> None:
    _python(tmp_path)

    code, profile = _run_recipe(tmp_path, "import os\n\ndef main():\n    os.chdir('..')\n    return 0\n")

    assert code == 0 and json.loads(profile.read_text()) == _SAMPLED
    # The reader counts every sample, so each share gets an interval.
    from repowise.core.evidence.profile_formats import profile_formats

    run = profile_formats.get("speedscope").read(profile.read_bytes())
    assert (run.unit, run.value_kind, [s.samples for s in run.stacks]) == ("s", "wall", [1])


def test_only_the_programs_own_exit_is_judged(tmp_path: Path) -> None:
    # A test runner catching a command's ``sys.exit(2)`` (click's CliRunner) is not the run failing.
    _python(tmp_path)
    main = "import sys\n\ndef main():\n    try:\n        sys.exit(2)\n    except SystemExit:\n        pass\n    return 0\n"

    code, profile = _run_recipe(tmp_path, main)

    assert code == 0 and profile.exists()


@pytest.mark.parametrize(
    "main",
    [
        "import missing_dependency\n\ndef main():\n    return 0\n",  # crashes at import
        "def main():\n    raise RuntimeError('boom')\n",
        "def main():\n    return 2\n",  # a usage error's exit code
        "def main():\n    return 1\n",  # an entry point's 1 is a failure
    ],
)
def test_a_failed_run_leaves_no_profile_to_ingest(tmp_path: Path, main: str) -> None:
    _python(tmp_path)
    stale = tmp_path / ".repowise" / "app.speedscope.json"
    stale.parent.mkdir()
    stale.write_text("{}")

    code, profile = _run_recipe(tmp_path, main)

    # An older run's profile is gone too, so ``evidence add`` stores nothing.
    assert code != 0 and profile == stale and not profile.exists()


def _suite(root: Path, tests: str) -> None:
    """A unit with unit tests beside integration tests that cannot even import."""
    _python(root)
    for name in ("unit", "integration"):
        (root / "tests" / name).mkdir(parents=True)
    (root / "tests" / "unit" / "test_a.py").write_text(tests)
    (root / "tests" / "integration" / "test_b.py").write_text("import missing_service_client\n")


def test_the_suite_profile_runs_the_tests_its_coverage_command_runs(tmp_path: Path) -> None:
    _suite(tmp_path, "def test_a():\n    pass\n")
    (stack,) = detect_repo_stacks(tmp_path)

    code, profile = _run_recipe(tmp_path, None, "unit_test", "-p", "no:cacheprovider")

    assert stack.test.startswith("pytest tests/unit ") and stack.test_paths == ("tests/unit",)
    assert code == 0
    assert json.loads(profile.read_text()) == {**_SAMPLED, "testsFailed": 0}


def test_a_path_in_the_arguments_picks_what_the_suite_profile_runs(tmp_path: Path) -> None:
    # Appended to tests/unit, the node id would run the failing test too.
    _suite(tmp_path, "def test_a():\n    pass\n\ndef test_b():\n    assert False\n")

    code, profile = _run_recipe(
        tmp_path, None, "unit_test", "-p", "no:cacheprovider", "tests/unit/test_a.py::test_a"
    )

    assert code == 0 and json.loads(profile.read_text())["testsFailed"] == 0


def test_an_options_value_does_not_replace_the_suites_paths(tmp_path: Path) -> None:
    # ``--ignore tests/unit/test_c.py`` narrows tests/unit; it is no path to run on its own.
    _suite(tmp_path, "def test_a():\n    pass\n")
    (tmp_path / "tests" / "unit" / "test_c.py").write_text("def test_c():\n    assert False\n")

    code, profile = _run_recipe(
        tmp_path, None, "unit_test", "-p", "no:cacheprovider", "--ignore", "tests/unit/test_c.py"
    )

    assert code == 0 and json.loads(profile.read_text())["testsFailed"] == 0


def test_a_file_options_value_does_not_replace_the_suites_paths(tmp_path: Path) -> None:
    # The integration tests cannot import: run with no paths, the profile would fail.
    _suite(tmp_path, "def test_a():\n    pass\n")
    (tmp_path / "pytest-extra.ini").write_text("[pytest]\n")

    code, profile = _run_recipe(
        tmp_path, None, "unit_test", "-p", "no:cacheprovider", "--log-file", "pytest-extra.ini"
    )

    assert code == 0 and json.loads(profile.read_text())["testsFailed"] == 0


def test_a_path_after_a_flag_still_picks_what_the_suite_profile_runs(tmp_path: Path) -> None:
    _suite(tmp_path, "def test_a():\n    pass\n\ndef test_b():\n    assert False\n")

    code, profile = _run_recipe(
        tmp_path, None, "unit_test", "-p", "no:cacheprovider", "-q", "tests/unit/test_a.py::test_a"
    )

    assert code == 0 and json.loads(profile.read_text())["testsFailed"] == 0


def test_of_several_apps_in_a_file_the_one_named_app_is_served(tmp_path: Path) -> None:
    _asgi(tmp_path, "from fastapi import FastAPI\nadmin = FastAPI()\napp = FastAPI()\n")

    serve = _targets(tmp_path, RuntimeInputs(entry_files=("app/main.py",)))[0]

    assert serve["workload"] == "serve app.main:app"


def _asgi(root: Path, source: str = "from fastapi import FastAPI\n\napp: FastAPI = FastAPI(title='x')\n") -> None:
    _python(root, scripts="")
    (root / "app" / "main.py").write_text(source)


def test_an_asgi_app_is_served_under_the_profiler_and_driven_by_requests(tmp_path: Path) -> None:
    _asgi(tmp_path)

    serve = _targets(tmp_path, RuntimeInputs(entry_files=("app/main.py",)))[0]

    assert (serve["class"], serve["workload"]) == ("entry_point", "serve app.main:app")
    assert r"import uvicorn;uvicorn.run(\'app.main:app\')" in serve["command"]
    # A server takes requests, not arguments.
    assert ARGS_PLACEHOLDER not in serve["command"] + serve["ingest"]
    assert "Ctrl-C" in serve["reason"] and "serves" not in serve
    assert serve["prerequisites"][-1] == "python -m pip install uvicorn"


def test_uv_brings_uvicorn_for_an_asgi_app(tmp_path: Path) -> None:
    _asgi(tmp_path, "import fastapi\napi = fastapi.FastAPI()\n")
    (tmp_path / "uv.lock").write_text("")

    serve = _targets(tmp_path, RuntimeInputs(entry_files=("app/main.py",)))[0]

    assert serve["command"].startswith("uv run --with pyinstrument --with uvicorn python -c")
    assert "app.main:api" in serve["command"] and serve["prerequisites"] == []


@pytest.mark.parametrize(
    "source",
    [
        "def create_app():\n    app = FastAPI()\n    return app\n",  # a factory: not module level
        "from flask import Flask\napp = Flask(__name__)\n",
    ],
)
def test_only_a_module_level_asgi_app_is_served(tmp_path: Path, source: str) -> None:
    _asgi(tmp_path, source)

    found = _targets(tmp_path, RuntimeInputs(entry_files=("app/main.py",)))

    assert not any(t["workload"].startswith("serve ") for t in found)


def test_a_red_suite_is_profiled_and_its_failures_counted(tmp_path: Path) -> None:
    _suite(tmp_path, "def test_a():\n    pass\n\ndef test_b():\n    assert False\n")

    code, profile = _run_recipe(tmp_path, None, "unit_test", "-p", "no:cacheprovider")

    assert code == 1 and json.loads(profile.read_text())["testsFailed"] == 1


def test_a_suite_that_could_not_run_leaves_no_profile(tmp_path: Path) -> None:
    _suite(tmp_path, "def test_a():\n    pass\n")

    code, profile = _run_recipe(tmp_path, None, "unit_test", "--no-such-option")

    assert code == 4 and not profile.exists()  # pytest's usage error


def test_a_suite_stopped_by_a_timeout_writes_its_profile_and_exits(tmp_path: Path) -> None:
    # SIGTERM (what ``timeout`` sends) writes the profile and ends the process,
    # even with a non-daemon thread that would keep it alive.
    _suite(
        tmp_path,
        "import signal, threading\n\ndef test_a():\n"
        "    threading.Thread(target=threading.Event().wait).start()\n"
        "    signal.raise_signal(signal.SIGTERM)\n\ndef test_b():\n    pass\n",
    )

    code, profile = _run_recipe(tmp_path, None, "unit_test", "-p", "no:cacheprovider")

    assert code == 143 and json.loads(profile.read_text())["profiles"] == _SAMPLED["profiles"]


def test_a_suite_stopped_with_ctrl_c_keeps_what_it_collected(tmp_path: Path) -> None:
    _suite(
        tmp_path,
        "import signal\n\ndef test_a():\n    signal.raise_signal(signal.SIGINT)\n\n"
        "def test_b():\n    pass\n",
    )

    code, profile = _run_recipe(tmp_path, None, "unit_test", "-p", "no:cacheprovider")

    # pytest reports an interrupted session (2); the recipe still wrote the profile.
    assert code == 2 and json.loads(profile.read_text())["profiles"] == _SAMPLED["profiles"]


def _targets_go(root: Path, inputs: RuntimeInputs, mains: list[str]) -> list[dict]:
    (stack,) = detect_repo_stacks(root)
    return runtime_targets(stack, root, inputs, SHA, mains)


def test_node_bins_get_a_cpu_profile_and_typescript_is_refused(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"name": "fx", "bin": {"fx": "bin/fx.js", "fxt": "bin/fxt.ts"}, '
        '"scripts": {"test": "jest"}, "devDependencies": {"jest": "1"}}'
    )
    (tmp_path / "bin").mkdir()

    js, ts = _targets(tmp_path)

    # One directory per target: every process and worker thread writes its own
    # profile there, cleaned first, and the ingest line takes the directory.
    assert js["command"] == "node --cpu-prof --cpu-prof-dir=.repowise/node-bin-fx-js bin/fx.js " + ARGS_PLACEHOLDER
    assert js["profile"] == ".repowise/node-bin-fx-js"
    assert js["prerequisites"] == ["git clean -fqx -- .repowise/node-bin-fx-js"]
    assert js["ingest"] == (
        "repowise evidence add .repowise/node-bin-fx-js "
        f'--workload "node bin/fx.js {ARGS_PLACEHOLDER}" --class entry_point --commit {SHA}'
    )
    assert "cpuprofile" not in js["reason"]
    assert ts["command"] is None and "TypeScript" in ts["reason"]


def test_next_call_profiles_before_running_tests_with_coverage(tmp_path: Path) -> None:
    _python(tmp_path)

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    runs = [s["run"] for s in plan["next_call"]["steps"]]
    assert "pytest.main" in runs[-3]
    assert runs[-2].startswith("repowise evidence add .repowise/python-tests.speedscope.json")
    assert runs[-1] == "repowise evidence status"


def test_a_coverage_report_on_disk_still_comes_first(tmp_path: Path) -> None:
    _python(tmp_path)
    (tmp_path / "coverage.xml").write_text("<coverage/>")

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    assert plan["next_call"]["steps"][0]["run"] == "repowise coverage add coverage.xml"


def test_coverage_only_leaves_runtime_out(tmp_path: Path) -> None:
    _python(tmp_path)

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs(), coverage_only=True)

    assert "runtime" not in plan["units"][0]
    assert plan["next_call"]["steps"][-1]["run"] == "repowise coverage status"


def test_runtime_inputs_come_from_the_store(tmp_path: Path) -> None:
    from repowise.core.persistence import create_session_factory, get_session
    from repowise.core.persistence.database import create_engine, init_db
    from repowise.core.persistence.models import GraphNode, HealthFileMetric, Repository, WikiSymbol

    async def run() -> RuntimeInputs:
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'wiki.db'}")
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                session.add(Repository(id="r1", name="t", local_path=str(tmp_path), url=""))
                await session.flush()
                for node, entry, test in (
                    ("app/__main__.py", True, False),
                    ("scripts/gen.py", True, False),
                    ("work/work_test.go", False, True),
                    # A copied library's tests and benchmarks (by path alone: vendored).
                    ("internal/thirdparty/xml/xml_test.go", False, True),
                    ("pkg/gen_test.go", False, True),
                ):
                    session.add(GraphNode(repository_id="r1", node_id=node, node_type="file",
                                          is_entry_point=entry, is_test=test))
                session.add(HealthFileMetric(repository_id="r1", file_path="scripts/gen.py",
                                             code_origin="tooling"))
                # The stored origin wins over the path.
                session.add(HealthFileMetric(repository_id="r1", file_path="pkg/gen_test.go",
                                             code_origin="generated"))
                for path, name in (("work/work_test.go", "BenchmarkCrunch"),
                                   ("work/work_test.go", "TestCrunch"),
                                   ("internal/thirdparty/xml/xml_test.go", "BenchmarkMarshal"),
                                   ("pkg/gen_test.go", "BenchmarkGen")):
                    session.add(WikiSymbol(repository_id="r1", file_path=path, symbol_id=f"{path}::{name}",
                                           name=name, qualified_name=name, kind="function"))
                await session.flush()
                return await load_runtime_inputs(session, "r1")
        finally:
            await engine.dispose()

    inputs = asyncio.run(run())

    assert inputs.entry_files == ("app/__main__.py",)
    assert inputs.benchmarks == (("work/work_test.go", "BenchmarkCrunch"),)
    assert inputs.test_files == ("work/work_test.go",)


def test_a_workload_measured_at_head_is_not_asked_for_again(tmp_path: Path) -> None:
    _python(tmp_path)
    measured = RuntimeInputs(measured=frozenset({("app", SHA), ("python tests", "b" * 40)}))

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=measured)

    assert plan["next_call"]["purpose"].startswith("Profile python tests (unit_test)")
    done = RuntimeInputs(measured=frozenset({("app", SHA), ("python tests", SHA)}))
    last = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=done, coverage_commit=SHA)
    assert last["next_call"] is None
    assert last["reason"].startswith("Evidence at this commit is ingested")


# ---------------------------------------------------------------------------
# What ``perf measure`` times for a change
# ---------------------------------------------------------------------------


def test_the_timed_workload_is_the_benchmark_without_its_report(tmp_path: Path) -> None:
    from repowise.core.evidence.runtime_setup import timed_target

    _python(tmp_path, extra='dependencies = ["pytest-benchmark"]\n')
    stacks = detect_repo_stacks(tmp_path)

    timed = timed_target(stacks, tmp_path, RuntimeInputs(), "app/cli.py")

    # The entry point waits for its arguments, so it is never timed.
    assert timed == {
        "workload": "pytest bench",
        "class": "benchmark",
        "command": "pytest --benchmark-only --benchmark-max-time=0 --benchmark-min-rounds=10",
        "cwd": "",
    }
    # Setup's own output stays as it was.
    assert all("timed" not in t for t in _targets(tmp_path))


def test_a_go_benchmark_is_timed_without_its_profile_flags(tmp_path: Path) -> None:
    from repowise.core.evidence.setup import timed_targets

    _go(tmp_path)
    inputs = RuntimeInputs(benchmarks=(("work/work_test.go", "BenchmarkCrunch"),))

    timed = timed_targets(tmp_path, ["work/work.go", "README.md"], units=None, runtime=inputs)

    # ``go run`` has no command, so the benchmark is what is timed.
    assert timed == {"work/work.go": {
        "workload": "go bench ./work", "class": "benchmark",
        "command": "go test '-run=^$' -bench . -benchtime=10x -benchmem -count=1 ./work", "cwd": "",
    }}


def test_only_a_benchmark_of_the_files_language_is_timed(tmp_path: Path) -> None:
    from repowise.core.evidence.setup import timed_targets

    _python(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text("def test_x():\n    pass\n")

    # An entry point waits for its arguments and the test suite runs far more
    # than the change: nothing is timed.
    assert timed_targets(tmp_path, ["app/cli.py"], units=None, runtime=RuntimeInputs()) == {}
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "app"\ndependencies = ["pytest-benchmark"]\n')
    assert timed_targets(tmp_path, ["app/cli.py"], units=None, runtime=RuntimeInputs())
    assert timed_targets(tmp_path, ["web/app.js"], units=None, runtime=RuntimeInputs()) == {}


def test_a_nested_unit_times_its_own_benchmark(tmp_path: Path) -> None:
    from repowise.core.evidence.setup import timed_targets

    for root in ("a", "b"):
        (tmp_path / root).mkdir()
        _python(tmp_path / root, extra='dependencies = ["pytest-benchmark"]\n')

    timed = timed_targets(tmp_path, ["b/app/cli.py"], units=None, runtime=RuntimeInputs())["b/app/cli.py"]

    # pytest-benchmark runs from the repository root, over the unit's directory.
    assert (timed["workload"], timed["cwd"]) == ("b: pytest bench", "")
    assert " b --benchmark-only" in timed["command"]


def test_a_pytest_benchmark_is_timed_over_fixed_rounds(tmp_path: Path) -> None:
    from repowise.core.evidence.setup import timed_targets

    _python(tmp_path, scripts="", extra='dependencies = ["pytest-benchmark"]\n')

    timed = timed_targets(tmp_path, ["app/cli.py"], units=None, runtime=RuntimeInputs())

    assert timed["app/cli.py"]["command"] == (
        "pytest --benchmark-only --benchmark-max-time=0 --benchmark-min-rounds=10"
    )


def test_entry_points_waiting_for_arguments_are_named_in_next_call(tmp_path: Path) -> None:
    _python(tmp_path, scripts='app = "app.cli:main"\ntool = "app.cli:main"\n')

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    assert plan["next_call"]["optional"]["workloads"] == ["app", "tool"]
    assert ARGS_PLACEHOLDER in plan["next_call"]["optional"]["purpose"]
    # Measured and covered, only the entry points are left: next_call stays, with no steps,
    # so an agent that stops at null never stops before them.
    measured = RuntimeInputs(measured=frozenset({("python tests", SHA)}))
    left = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=measured, coverage_commit=SHA)
    assert left["next_call"]["steps"] == [] and left["reason"] is None
    assert left["next_call"]["optional"] == plan["next_call"]["optional"]
    # Once a run of an entry point is stored at this commit under filled arguments, it is
    # not asked for again; a run stored under the unfilled name does not count.
    measured = RuntimeInputs(measured=frozenset({
        ("python tests", SHA), ("app build", SHA), ("tool <args>", SHA),
    }))
    left = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=measured, coverage_commit=SHA)
    assert left["next_call"]["optional"]["workloads"] == ["tool"]
    measured = RuntimeInputs(measured=frozenset({("python tests", SHA), ("app build", SHA), ("tool x", SHA)}))
    done = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=measured, coverage_commit=SHA)
    assert done["next_call"] is None
    assert done["reason"] == "Evidence at this commit is ingested; nothing left to run."


def test_a_server_waiting_for_requests_is_named_in_next_call_until_measured(tmp_path: Path) -> None:
    _asgi(tmp_path)
    inputs = RuntimeInputs(entry_files=("app/main.py",), measured=frozenset({("python tests", SHA)}))

    left = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=inputs, coverage_commit=SHA)

    assert left["next_call"]["steps"] == [] and left["next_call"]["optional"]["workloads"] == ["serve app.main:app"]
    inputs = RuntimeInputs(
        entry_files=("app/main.py",), measured=frozenset({("python tests", SHA), ("serve app.main:app", SHA)})
    )
    assert evidence_setup(tmp_path, indexed=True, head=SHA, runtime=inputs, coverage_commit=SHA)["next_call"] is None


def test_the_offer_is_what_the_plan_prints_an_ingest_line_for(tmp_path: Path) -> None:
    _python(tmp_path)

    offer = runtime_offer(tmp_path, units=None, runtime=RuntimeInputs(), head=SHA)
    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    printed = [t["workload"] for u in plan["units"] for t in u["runtime"] if t["ingest"]]
    assert [t["workload"] for t in offer] == printed == ["app", "python tests"]
    assert offered_classes(offer) == ("entry_point", "unit_test")
    assert {t["format"] for t in offer} == {"speedscope"}
    # The format is for the callers that read the offer, never printed.
    assert all("format" not in t for u in plan["units"] for t in u["runtime"])
    # Without a commit nothing can be ingested, so nothing is offered.
    assert runtime_offer(tmp_path, units=None, runtime=RuntimeInputs(), head=None) == []


def test_a_unit_with_nothing_to_profile_offers_nothing_and_says_so(tmp_path: Path) -> None:
    # A Jest unit: coverage, but no entry point, benchmark or suite recipe to profile.
    (tmp_path / "package.json").write_text(
        '{"name": "web", "scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}}'
    )

    assert runtime_offer(tmp_path, units=None, runtime=RuntimeInputs(), head=SHA) == []
    done = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs(), coverage_commit=SHA)
    assert done["next_call"] is None
    assert done["reason"].startswith("Coverage at this commit is ingested. No unit has an entry point")


def test_without_entry_points_next_call_has_no_optional_key(tmp_path: Path) -> None:
    _python(tmp_path, scripts="")

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    assert "optional" not in plan["next_call"]


def test_a_node_ingest_line_names_the_directory_of_per_process_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.evidence import runtime_setup

    monkeypatch.setattr(runtime_setup, "_readable", lambda _fmt: True)
    (tmp_path / "package.json").write_text(
        '{"name": "fx", "bin": {"fx": "bin/fx.js"}, '
        '"scripts": {"test": "jest"}, "devDependencies": {"jest": "1"}}'
    )
    (tmp_path / "bin").mkdir()

    (js,) = _targets(tmp_path)

    assert js["ingest"].startswith('repowise evidence add .repowise/node-bin-fx-js --workload "node bin/fx.js')
