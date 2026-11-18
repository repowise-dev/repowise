"""Runtime targets for ``evidence setup``: owners' entry points, benchmarks, recipes and ingest lines."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.core.analysis.published_packages import BuildFacts, go_main_packages
from repowise.core.code_origin import is_benchmark_function, is_benchmark_path
from repowise.core.evidence.runtime_setup import RuntimeInputs, load_runtime_inputs, runtime_targets
from repowise.core.evidence.setup import evidence_setup
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
    assert "'filter_threshold':0" in entry["command"]
    assert entry["command"].startswith('python -c "') and entry["command"].count('"') == 2
    assert entry["prerequisites"] == ["python -m pip install pyinstrument"]
    assert entry["ingest"] == (
        f'repowise evidence add .repowise/app.speedscope.json --workload "app" '
        f"--class entry_point --commit {SHA}"
    )
    assert tests["class"] == "unit_test"
    assert "pytest.main" in tests["command"]
    assert tests["prerequisites"][-1] == "python -m pip install pytest"


def test_a_python_main_module_the_index_flags_is_run_as_a_module(tmp_path: Path) -> None:
    _python(tmp_path, scripts="")
    (tmp_path / "app" / "__main__.py").write_text("print(1)\n")

    entry = _targets(tmp_path, RuntimeInputs(entry_files=("app/__main__.py",)))[0]

    assert entry["workload"] == "python -m app"
    assert "runpy.run_module('app',run_name='__main__',alter_sys=True)" in entry["command"]


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

    main, bench, tests = _targets_go(tmp_path, inputs, mains)

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


def test_go_profiles_wait_for_a_pprof_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.evidence.profile_formats import profile_formats

    monkeypatch.delitem(profile_formats._impls, "pprof", raising=False)
    _go(tmp_path)
    inputs = RuntimeInputs(
        benchmarks=(("work/work_test.go", "BenchmarkCrunch"),), test_files=("work/work_test.go",)
    )

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=inputs)

    profiled = [t for t in plan["units"][0]["runtime"] if t["command"]]
    assert profiled and all(t["ingest"] is None for t in profiled)
    assert all(t["reason"] == "This build of repowise does not read pprof files yet." for t in profiled)
    # Nothing it could read leads the loop: the coverage run does.
    assert plan["next_call"]["purpose"].startswith("Run the go tests")


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
    assert by_root["tools"] == ["tools: go bench .", "tools: go test ."]


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
    poetry = runtime_targets(replace(stack, test="poetry run pytest"), tmp_path, RuntimeInputs(), SHA)[0]
    assert poetry["command"].startswith('poetry run python -c "')
    assert poetry["prerequisites"] == ["poetry run python -m pip install pyinstrument"]


def test_the_python_recipe_writes_its_profile_where_the_ingest_line_reads_it(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    _python(tmp_path)
    (tmp_path / "app" / "cli.py").write_text("import os\n\ndef main():\n    os.chdir('..')\n    return 0\n")
    entry = _targets(tmp_path)[0]
    # The recipe with a stand-in profiler, so the test needs no pyinstrument.
    fake = tmp_path / "fake"
    (fake / "pyinstrument" / "renderers").mkdir(parents=True)
    (fake / "pyinstrument" / "__init__.py").write_text(
        "class Profiler:\n    def start(self): pass\n    def stop(self): pass\n"
        "    def output(self, renderer): return '{}'\n"
    )
    (fake / "pyinstrument" / "renderers" / "__init__.py").write_text(
        "class SpeedscopeRenderer:\n    def __init__(self, **kw): pass\n"
    )
    code = entry["command"].split('-c "', 1)[1][:-1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(fake), str(tmp_path)])}

    subprocess.run([sys.executable, "-c", code], cwd=entry["cwd"], env=env, check=True)

    assert (tmp_path / entry["profile"]).read_text() == "{}"


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

    assert js["command"] == (
        "node --cpu-prof --cpu-prof-dir=.repowise --cpu-prof-name=node-bin-fx-js.cpuprofile bin/fx.js"
    )
    assert js["ingest"] is None and "cpuprofile" in js["reason"]
    assert ts["command"] is None and "TypeScript" in ts["reason"]


def test_next_call_profiles_an_entry_point_before_running_tests_with_coverage(tmp_path: Path) -> None:
    _python(tmp_path)

    plan = evidence_setup(tmp_path, indexed=True, head=SHA, runtime=RuntimeInputs())

    runs = [s["run"] for s in plan["next_call"]["steps"]]
    assert "from app.cli import main" in runs[-3]
    assert runs[-2].startswith("repowise evidence add .repowise/app.speedscope.json")
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
                ):
                    session.add(GraphNode(repository_id="r1", node_id=node, node_type="file",
                                          is_entry_point=entry, is_test=test))
                session.add(HealthFileMetric(repository_id="r1", file_path="scripts/gen.py",
                                             code_origin="tooling"))
                for path, name in (("work/work_test.go", "BenchmarkCrunch"),
                                   ("work/work_test.go", "TestCrunch")):
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
