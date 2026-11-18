"""The before/after timing rule: noise, threshold, verdict, run order, baselines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.command_runner import CommandRun
from repowise.core.perf_measure import (
    BEFORE_AFTER_FLOOR,
    INTERLEAVED_FLOOR,
    Baseline,
    BaselineError,
    CommandFailedError,
    Side,
    baseline_path,
    collect,
    collect_interleaved,
    compare,
    interleave_order,
    load_baseline,
    save_baseline,
    split_noise,
    threshold,
)

# Ten runs with about 1% spread around 1.0 s, no drift between halves.
_STEADY = [1.00, 1.01, 0.99, 1.02, 0.98, 1.00, 1.01, 0.99, 1.02, 0.98]


def _scaled(factor: float) -> list[float]:
    return [v * factor for v in _STEADY]


def _ok(seconds: float) -> CommandRun:
    return CommandRun(0, seconds, False, "")


def test_split_noise_compares_the_later_half_with_the_earlier_one() -> None:
    assert split_noise([1.0, 1.0, 1.1, 1.1]) == pytest.approx(0.1)
    # The middle run of an odd count sits in neither half.
    assert split_noise([1.0, 1.0, 9.0, 1.0, 1.0]) == 0.0
    assert split_noise([1.0]) == 0.0


def test_threshold_is_twice_the_noise_with_a_floor_per_mode() -> None:
    assert threshold("before_after", [0.0, 0.01]) == BEFORE_AFTER_FLOOR
    assert threshold("interleaved", [0.0, 0.005]) == INTERLEAVED_FLOOR
    assert threshold("before_after", [0.04, 0.01]) == pytest.approx(0.08)
    assert threshold("interleaved", [0.04, 0.01]) == pytest.approx(0.08)


@pytest.mark.parametrize(
    ("factor", "expected"),
    [(0.7, "improved"), (1.4, "regressed"), (1.01, "inconclusive"), (0.97, "inconclusive")],
)
def test_verdict_needs_the_whole_interval_past_the_threshold(factor: float, expected: str) -> None:
    result = compare(Side(_STEADY), Side(_scaled(factor)), "before_after")
    assert result.verdict == expected
    assert result.interval.low <= result.interval.estimate <= result.interval.high
    assert result.interval.estimate == pytest.approx(factor)


def test_interleaved_mode_calls_a_change_the_before_after_floor_would_not() -> None:
    base, head = Side(_STEADY), Side(_scaled(0.96))
    assert compare(base, head, "before_after").verdict == "inconclusive"
    assert compare(base, head, "interleaved").verdict == "improved"


def test_a_noisy_machine_widens_the_threshold_into_inconclusive() -> None:
    # The later half of base runs is 20% slower than the earlier half: the
    # machine moved by itself, so a 30% change is not trusted.
    drifting = [1.0] * 5 + [1.2] * 5
    result = compare(Side(drifting), Side([v * 0.75 for v in drifting]), "before_after")
    assert result.threshold == pytest.approx(0.4)
    assert result.verdict == "inconclusive"


def test_interleave_order_alternates_which_side_leads() -> None:
    assert interleave_order(3) == ["base", "head", "head", "base", "base", "head"]


def test_collect_interleaved_warms_both_sides_then_follows_the_order(tmp_path: Path) -> None:
    base_dir, head_dir = tmp_path / "base", tmp_path / "head"
    calls: list[str] = []

    def runner(cwd: Path) -> CommandRun:
        calls.append(cwd.name)
        return _ok(2.0 if cwd == base_dir else 1.0)

    base, head = collect_interleaved(runner, base_dir, head_dir, runs=2, warmup=1)
    assert calls == ["base", "head", "base", "head", "head", "base"]
    assert base == [2.0, 2.0] and head == [1.0, 1.0]


def test_collect_discards_warmup_runs(tmp_path: Path) -> None:
    times = iter([9.0, 1.0, 1.1, 1.2, 1.3])
    runs = collect(lambda _cwd: _ok(next(times)), tmp_path, runs=4, warmup=1)
    assert runs == [1.0, 1.1, 1.2, 1.3]


def test_a_failing_run_is_an_error_not_a_timing(tmp_path: Path) -> None:
    results = iter([_ok(1.0), CommandRun(4, 0.2, False, "Traceback\nValueError: bad")])
    with pytest.raises(CommandFailedError) as caught:
        collect(lambda _cwd: next(results), tmp_path, runs=4, warmup=0)
    assert str(caught.value) == "head run 2 exited 4"
    assert caught.value.run.output_tail.endswith("ValueError: bad")


def test_a_failing_warmup_or_timeout_names_itself(tmp_path: Path) -> None:
    with pytest.raises(CommandFailedError, match="base warmup run 1 timed out"):
        collect(
            lambda _cwd: CommandRun(None, 5.0, True, ""), tmp_path, runs=4, warmup=1, side="base"
        )


def _baseline(name: str = "base") -> Baseline:
    return Baseline(
        name=name,
        command="pytest -x",
        warmup=1,
        measured_at="2026-11-18T10:00:00+00:00",
        side=Side(_STEADY, "a" * 40, True, "/repo"),
        environment={"os": "Linux", "host": "box"},
    )


def test_baseline_round_trips(tmp_path: Path) -> None:
    path = save_baseline(tmp_path, _baseline())
    assert path == tmp_path / "perf" / "base.json"
    loaded = load_baseline(tmp_path, "base")
    assert loaded == _baseline()


@pytest.mark.parametrize("name", ["../escape", "a/b", "", ".hidden", "x" * 65, "base\n"])
def test_a_baseline_name_is_a_file_name_never_a_path(tmp_path: Path, name: str) -> None:
    with pytest.raises(BaselineError):
        baseline_path(tmp_path, name)


def test_a_missing_baseline_says_how_to_save_one(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="--save nope"):
        load_baseline(tmp_path, "nope")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.update(version=99),
        lambda raw: raw.update(samples=[1.0, 0.0, 1.0, 1.0]),
        lambda raw: raw.update(samples=[1.0]),
        lambda raw: raw.pop("command"),
        lambda raw: raw.update(samples="fast"),
    ],
)
def test_a_damaged_baseline_is_refused(tmp_path: Path, mutate) -> None:
    path = save_baseline(tmp_path, _baseline())
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(BaselineError):
        load_baseline(tmp_path, "base")


@pytest.mark.parametrize(
    ("label", "expected"),
    [("load_rows", "before-load_rows"), ("Report.render", "before-Report.render"),
     ("<lambda>", "before-lambda"), ("", "before"), (None, "before"), ("x" * 100, "before-" + "x" * 57)],
)
def test_a_baseline_name_for_any_function_is_one_perf_measure_accepts(
    tmp_path: Path, label: str | None, expected: str
) -> None:
    from repowise.core.perf_measure import baseline_name, baseline_path

    name = baseline_name(label)
    assert name == expected
    assert baseline_path(tmp_path, name).name == f"{name}.json"
