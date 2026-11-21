"""``repowise perf measure`` and ``perf check``: modes, exit codes and the JSON document."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands import perf_cmd
from repowise.core.command_runner import CommandRun
from tests.unit.cli import test_evidence_cmd
from tests.unit.cli.test_evidence_cmd import _GO_BENCH, _go_bench_repo
from tests.unit.cli.test_evidence_cmd import _add as _evidence_add

_JITTER = [1.00, 1.01, 0.99, 1.02, 0.98]


def _fake_runner(monkeypatch, seconds_for: Callable[[Path, int], float], *, fail_at: int = 0):
    """Patch the runner: run *i* in *cwd* takes ``seconds_for(cwd, i)``."""
    calls: list[tuple[str, Path]] = []

    def fake(command: str, *, cwd: Path, timeout: float) -> CommandRun:
        calls.append((command, cwd))
        if fail_at and len(calls) == fail_at:
            return CommandRun(1, 0.1, False, "collected 3 items\nE   AssertionError: off by one")
        return CommandRun(0, seconds_for(cwd, len(calls)), False, "")

    monkeypatch.setattr(perf_cmd, "run_command", fake)
    return calls


def _steady(level: float) -> Callable[[Path, int], float]:
    return lambda _cwd, i: level * _JITTER[i % len(_JITTER)]


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args: str):
    return CliRunner(mix_stderr=False).invoke(perf_cmd.perf_group, ["measure", *args])


def test_save_then_against_round_trip(repo: Path, monkeypatch) -> None:
    _fake_runner(monkeypatch, _steady(1.0))
    saved = _invoke("--save", "base", "--format", "json", "--", "pytest", "-x")
    assert saved.exit_code == 0, saved.output
    doc = json.loads(saved.stdout)
    assert doc["verdict"] == "saved" and doc["base"]["runs"] == 10
    assert (repo / ".repowise" / "perf" / "base.json").is_file()

    _fake_runner(monkeypatch, _steady(0.5))
    result = _invoke("--against", "base", "--format", "json", "--", "pytest", "-x")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["mode"] == "before_after" and doc["verdict"] == "improved"
    assert doc["baseline"] == "base" and doc["command"] == "pytest -x"
    assert set(doc["ratio"]) == {"estimate", "low", "high", "confidence", "method"}
    assert doc["ratio"]["high"] < 1 - doc["threshold"]
    assert set(doc["head"]) == {
        "runs", "median_s", "samples_s", "noise", "commit", "dirty", "cwd"
    }  # fmt: skip
    assert len(doc["head"]["samples_s"]) == 10


def test_a_regression_exits_one_and_prints_the_interval(repo: Path, monkeypatch) -> None:
    _fake_runner(monkeypatch, _steady(1.0))
    assert _invoke("--save", "base", "--", "pytest").exit_code == 0
    _fake_runner(monkeypatch, _steady(1.5))
    result = _invoke("--against", "base", "--", "pytest")
    assert result.exit_code == 1
    assert "Regressed (before and after)" in result.stdout.splitlines()
    assert "95% interval" in result.output


def test_an_unclear_change_is_inconclusive_and_exits_zero(repo: Path, monkeypatch) -> None:
    _fake_runner(monkeypatch, _steady(1.0))
    _invoke("--save", "base", "--", "pytest")
    _fake_runner(monkeypatch, _steady(1.02))
    result = _invoke("--against", "base", "--format", "json", "--", "pytest")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["verdict"] == "inconclusive"


def test_save_records_the_commit_and_dirty_state(repo: Path, monkeypatch) -> None:
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    (repo / "a.txt").write_text("one", encoding="utf-8")
    subprocess.run([*git, "add", "a.txt"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "one"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "a.txt").write_text("two", encoding="utf-8")

    _fake_runner(monkeypatch, _steady(1.0))
    result = _invoke("--save", "base", "--format", "json", "--", "pytest")
    doc = json.loads(result.stdout)
    assert doc["base"]["commit"] == head and doc["base"]["dirty"] is True
    stored = json.loads((repo / ".repowise" / "perf" / "base.json").read_text(encoding="utf-8"))
    assert stored["commit"] == head and stored["dirty"] is True


def test_base_dir_interleaves_and_uses_the_tight_threshold(
    repo: Path, tmp_path_factory, monkeypatch
) -> None:
    base_dir = tmp_path_factory.mktemp("base")
    # Noiseless runs, so the threshold is exactly the interleaved floor, and a
    # 4% speedup clears it where the 5% before-and-after floor would not.
    calls = _fake_runner(monkeypatch, lambda cwd, i: 1.0 if cwd == base_dir.resolve() else 0.96)
    result = _invoke("--base-dir", str(base_dir), "--runs", "4", "--format", "json", "--", "make")
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["mode"] == "interleaved" and doc["threshold"] == pytest.approx(0.02)
    assert doc["verdict"] == "improved"
    sides = ["base" if cwd == base_dir.resolve() else "head" for _cmd, cwd in calls]
    assert sides == ["base", "head"] + ["base", "head", "head", "base"] * 2


def test_a_failing_run_exits_two_with_its_last_output(repo: Path, monkeypatch) -> None:
    _fake_runner(monkeypatch, _steady(1.0), fail_at=3)
    result = _invoke("--save", "base", "--format", "json", "--", "pytest")
    assert result.exit_code == 2
    doc = json.loads(result.stdout)
    assert doc["error"] == "command_failed"
    assert "base run 2 exited 1" in doc["message"]
    assert "AssertionError: off by one" in doc["message"]
    assert not (repo / ".repowise" / "perf" / "base.json").exists()


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["--", "pytest"], "usage"),
        (["--save", "a", "--against", "b", "--", "pytest"], "usage"),
        (["--against", "missing", "--", "pytest"], "baseline"),
        (["--save", "../x", "--", "pytest"], "baseline"),
        (["--save", "base\n", "--", "pytest"], "baseline"),
    ],
)
def test_refusals_exit_two_with_a_json_error(repo: Path, monkeypatch, args, code) -> None:
    calls = _fake_runner(monkeypatch, _steady(1.0))
    result = _invoke("--format", "json", *args)
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == code
    assert calls == []


def test_against_refuses_a_different_command(repo: Path, monkeypatch) -> None:
    _fake_runner(monkeypatch, _steady(1.0))
    _invoke("--save", "base", "--", "pytest", "-x")
    calls = _fake_runner(monkeypatch, _steady(1.0))
    result = _invoke("--against", "base", "--format", "json", "--", "pytest", "-k", "slow")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "command_mismatch"
    assert calls == []


def test_options_after_the_separator_belong_to_the_command(repo: Path, monkeypatch) -> None:
    calls = _fake_runner(monkeypatch, _steady(1.0))
    result = _invoke("--save", "base", "--runs", "4", "--warmup", "0", "--", "tool", "--runs", "9")
    assert result.exit_code == 0, result.output
    assert len(calls) == 4
    assert calls[0][0].endswith("tool --runs 9")


def test_base_dir_must_be_another_checkout(repo: Path, monkeypatch) -> None:
    calls = _fake_runner(monkeypatch, _steady(1.0))
    result = _invoke("--base-dir", str(repo), "--format", "json", "--", "make")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "usage"
    assert calls == []


def test_a_command_that_cannot_start_exits_two_with_a_document(repo: Path, monkeypatch) -> None:
    def broken(command: str, *, cwd: Path, timeout: float) -> CommandRun:
        raise FileNotFoundError("no shell")

    monkeypatch.setattr(perf_cmd, "run_command", broken)
    result = _invoke("--save", "base", "--format", "json", "--", "make")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"] == "io_error"


# --- perf check -------------------------------------------------------------

#: The evidence tests' small indexed git repo, shared.
indexed = test_evidence_cmd.indexed


def _check(root: Path, *args: str):
    return CliRunner(mix_stderr=False).invoke(
        perf_cmd.perf_group, ["check", "--path", str(root), *args]
    )


def _ingest(root: Path, head: str, text: str, name: str) -> None:
    path = root / name
    path.write_text(text)
    result = _evidence_add(
        str(path), "--workload", "go bench results ./strs", "--class", "benchmark",
        "--commit", head, "--format", "json",
    )
    assert result.exit_code == 0, result.output


def test_check_gates_on_allocations_per_op_never_on_time(indexed) -> None:
    root, _ = indexed
    head = _go_bench_repo(root)
    text = (_GO_BENCH / "bench.txt").read_text()
    _ingest(root, head, text, "before.txt")

    saved = _check(root, "--save", "base", "--format", "json")
    assert saved.exit_code == 0, saved.output
    assert json.loads(saved.stdout)["benchmarks"] == {"go bench results ./strs": 3}
    assert (root / ".repowise" / "perf" / "exact" / "base.json").is_file()

    # Nothing ran since the baseline: nothing to judge.
    same = _check(root, "--against", "base", "--format", "json")
    assert same.exit_code == 2
    assert json.loads(same.stdout)["error"] == "cannot_judge"

    # Ten times slower, allocating the same: passes.
    _ingest(root, head, text.replace("112.0 ns/op", "1120 ns/op"), "slower.txt")
    slower = _check(root, "--against", "base")
    assert slower.exit_code == 0, slower.output
    assert slower.stdout.startswith("Passed: 0 of 3 benchmarks")

    _ingest(root, head, text.replace("40 B/op\t       4 allocs/op", "48 B/op\t       6 allocs/op"),
            "more.txt")
    more = _check(root, "--against", "base", "--format", "json")
    assert more.exit_code == 1, more.output
    payload = json.loads(more.stdout)
    assert (payload["verdict"], payload["baseline"], payload["metrics"]) == (
        "regressed", "base", ["allocs_per_op"],
    )
    assert [(c["benchmark"], c["base"], c["head"]) for c in payload["regressed"]] == [
        ("example.com/gobench/strs.BenchmarkConcat/n=5", 4, 6),
    ]


def test_check_needs_one_mode_and_stored_exact_values(indexed) -> None:
    root, _ = indexed
    both = _check(root, "--save", "a", "--against", "b", "--format", "json")
    assert both.exit_code == 2 and json.loads(both.stdout)["error"] == "usage"
    empty = _check(root, "--save", "base", "--format", "json")
    assert empty.exit_code == 2 and json.loads(empty.stdout)["error"] == "no_exact_evidence"
    # No Go benchmark here: nothing to gate, and no setup target that setup never prints.
    message = json.loads(empty.stdout)["message"]
    assert "nothing to gate" in message and "target" not in message
    assert "repowise perf measure" in message
    missing = _check(root, "--against", "nope", "--format", "json")
    assert missing.exit_code == 2 and json.loads(missing.stdout)["error"] == "baseline"


def test_check_names_the_go_benchmark_target_setup_prints(indexed) -> None:
    root, _ = indexed
    _go_bench_repo(root)
    empty = _check(root, "--save", "base", "--format", "json")
    assert empty.exit_code == 2
    message = json.loads(empty.stdout)["message"]
    assert "Go benchmark results target `repowise evidence setup` prints" in message


def test_check_names_a_workload_the_baseline_lacks(indexed) -> None:
    root, _ = indexed
    head = _go_bench_repo(root)
    _ingest(root, head, (_GO_BENCH / "bench.txt").read_text(), "before.txt")
    assert _check(root, "--save", "base").exit_code == 0
    result = _check(root, "--against", "base", "--workload", "other", "--format", "json")
    assert result.exit_code == 2
    assert "has no workload 'other'" in json.loads(result.stdout)["message"]


def test_a_workload_filtered_save_keeps_the_baselines_other_workloads(indexed) -> None:
    root, _ = indexed
    head = _go_bench_repo(root)
    text = (_GO_BENCH / "bench.txt").read_text()
    _ingest(root, head, text, "before.txt")
    path = root / "other.txt"
    path.write_text(text.replace("BenchmarkJoin", "BenchmarkSplit"))
    result = _evidence_add(
        str(path), "--workload", "other", "--class", "benchmark", "--commit", head,
    )
    assert result.exit_code == 0, result.output
    assert _check(root, "--save", "base").exit_code == 0

    saved = _check(root, "--save", "base", "--workload", "other", "--format", "json")
    assert saved.exit_code == 0, saved.output
    assert json.loads(saved.stdout)["benchmarks"] == {"go bench results ./strs": 3, "other": 3}
