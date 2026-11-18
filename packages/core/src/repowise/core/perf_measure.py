"""Time a command before and after a change and say whether it moved.

The owner of ``repowise perf measure``'s rule. A command is run N times on
each side; the verdict compares the median of head runs to the median of base
runs through :func:`repowise.core.stats.bootstrap_median_ratio` and calls the
change only when that interval clears 1 by more than the machine's own noise.

Noise is measured, not assumed: each side's runs are split into an earlier and
a later half, and how far those two halves' medians sit apart is what the same
code on the same machine does to itself. The threshold is twice the larger of
those, with a floor:

- before and after (a saved baseline, then head minutes later): floor 5%, as
  the machine's state drifts between the two sessions;
- interleaved (base and head runs alternated in one session): floor 2%, as
  drift lands on both sides alike.

Wall time is noisy evidence. It informs an agent's next step and never gates
anything.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from repowise.core.command_runner import CommandRun
from repowise.core.stats import RatioInterval, bootstrap_median_ratio

DEFAULT_RUNS = 10
#: Two runs per half is the least a noise probe can split.
MIN_RUNS = 4
BEFORE_AFTER_FLOOR = 0.05
INTERLEAVED_FLOOR = 0.02
NOISE_MULTIPLIER = 2.0
CONFIDENCE = 0.95

#: ``.repowise/perf/<name>.json``; the format version is bumped on a breaking
#: change so an old baseline is refused rather than misread.
BASELINE_DIR = "perf"
BASELINE_VERSION = 1
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
#: What the verdict compares, in the words a plan's ``verify.measure`` uses.
METRIC = "median wall time"

Verdict = Literal["improved", "regressed", "inconclusive"]
Mode = Literal["before_after", "interleaved"]

#: Runs the command once in a directory. ``run_command`` bound to a command
#: string and a timeout in production; a fake in tests.
Runner = Callable[[Path], CommandRun]


class CommandFailedError(Exception):
    """A run exited non-zero or timed out: there is no timing to report."""

    def __init__(self, side: str, index: int, run: CommandRun) -> None:
        how = "timed out" if run.timed_out else f"exited {run.exit_code}"
        which = f"warmup run {-index}" if index < 0 else f"run {index}"
        super().__init__(f"{side} {which} {how}")
        self.side = side
        self.index = index
        self.run = run


class BaselineError(Exception):
    """A baseline name is invalid, or its file is missing or unreadable."""


def _checked(run: CommandRun, side: str, index: int) -> float:
    if not run.ok:
        raise CommandFailedError(side, index, run)
    return run.seconds


def collect(
    runner: Runner, cwd: Path, *, runs: int, warmup: int, side: str = "head"
) -> list[float]:
    """*warmup* discarded runs, then *runs* timed ones, in *cwd*."""
    for i in range(warmup):
        _checked(runner(cwd), side, -(i + 1))
    return [_checked(runner(cwd), side, i + 1) for i in range(runs)]


def interleave_order(runs: int) -> list[Literal["base", "head"]]:
    """The run order for interleaved mode: pairs that alternate which side leads.

    ``base head, head base, base head, ...`` (ABBA), so neither side always
    runs right after the other and a warming cache or a cooling CPU lands on
    both evenly.
    """
    order: list[Literal["base", "head"]] = []
    for i in range(runs):
        order.extend(("base", "head") if i % 2 == 0 else ("head", "base"))
    return order


def collect_interleaved(
    runner: Runner, base_cwd: Path, head_cwd: Path, *, runs: int, warmup: int
) -> tuple[list[float], list[float]]:
    """*runs* timed runs per side in :func:`interleave_order`, after warmups on both."""
    for i in range(warmup):
        _checked(runner(base_cwd), "base", -(i + 1))
        _checked(runner(head_cwd), "head", -(i + 1))
    samples: dict[str, list[float]] = {"base": [], "head": []}
    for side in interleave_order(runs):
        cwd = base_cwd if side == "base" else head_cwd
        samples[side].append(_checked(runner(cwd), side, len(samples[side]) + 1))
    return samples["base"], samples["head"]


def split_noise(samples: Sequence[float]) -> float:
    """How far the later half's median sits from the earlier half's, as a fraction.

    A base-versus-base comparison from runs already taken: the same command
    on the same code, so any difference is the machine. With an odd count the
    middle run is left out of both halves.
    """
    half = len(samples) // 2
    if half == 0:
        return 0.0
    early = statistics.median(samples[:half])
    late = statistics.median(samples[len(samples) - half :])
    return abs(late / early - 1.0)


def threshold(mode: Mode, noises: Sequence[float]) -> float:
    floor = INTERLEAVED_FLOOR if mode == "interleaved" else BEFORE_AFTER_FLOOR
    return max(floor, NOISE_MULTIPLIER * max(noises, default=0.0))


def verdict(interval: RatioInterval, limit: float) -> Verdict:
    """``improved`` or ``regressed`` only when the whole interval clears ``1 -/+ limit``."""
    if interval.high < 1.0 - limit:
        return "improved"
    if interval.low > 1.0 + limit:
        return "regressed"
    return "inconclusive"


@dataclass(frozen=True)
class Side:
    """One side's timed runs and where they ran."""

    samples: list[float]
    commit: str | None = None
    dirty: bool | None = None
    cwd: str | None = None

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    def to_json(self) -> dict[str, Any]:
        return {
            "runs": len(self.samples),
            "median_s": self.median,
            "samples_s": list(self.samples),
            "noise": split_noise(self.samples),
            "commit": self.commit,
            "dirty": self.dirty,
            "cwd": self.cwd,
        }


@dataclass(frozen=True)
class Comparison:
    mode: Mode
    base: Side
    head: Side
    interval: RatioInterval
    threshold: float
    verdict: Verdict

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "verdict": self.verdict,
            "base": self.base.to_json(),
            "head": self.head.to_json(),
            "ratio": {
                "estimate": self.interval.estimate,
                "low": self.interval.low,
                "high": self.interval.high,
                "confidence": self.interval.confidence,
                "method": self.interval.method,
            },
            "threshold": self.threshold,
        }


def compare(base: Side, head: Side, mode: Mode) -> Comparison:
    interval = bootstrap_median_ratio(head.samples, base.samples, confidence=CONFIDENCE)
    limit = threshold(mode, (split_noise(base.samples), split_noise(head.samples)))
    return Comparison(mode, base, head, interval, limit, verdict(interval, limit))


@dataclass(frozen=True)
class Baseline:
    """A saved base side: the command, its runs, and the commit they ran at."""

    name: str
    command: str
    warmup: int
    measured_at: str
    side: Side
    environment: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": BASELINE_VERSION,
            "name": self.name,
            "command": self.command,
            "warmup": self.warmup,
            "measured_at": self.measured_at,
            "environment": dict(self.environment),
            **asdict(self.side),
        }


def baseline_path(repowise_dir: Path, name: str) -> Path:
    """Where baseline *name* lives. The name is a file name, never a path."""
    if not _NAME_RE.fullmatch(name):
        raise BaselineError(
            f"baseline name {name!r} must be 1-64 letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return repowise_dir / BASELINE_DIR / f"{name}.json"


def baseline_name(label: str | None) -> str:
    """A valid baseline name for a change to *label* (a function's name): ``before-<label>``."""
    slug = _NAME_UNSAFE.sub("-", label or "").strip("-.")
    return f"before-{slug}"[:64].rstrip("-.") if slug else "before"


def verify_measure(target: Mapping[str, Any], label: str | None) -> dict[str, Any]:
    """A fix plan's ``verify.measure``: how to time *target* (a
    :func:`~repowise.core.evidence.runtime_setup.timed_target`) before and after
    a change to *label*.

    ``tolerance`` is the before-and-after floor: the verdict calls a change
    only beyond it, or beyond twice the noise the runs show when that is wider.
    """
    name = baseline_name(label)
    command = target["command"]
    return {
        "command": command,
        "before": f"repowise perf measure --save {name} -- {command}",
        "after": f"repowise perf measure --against {name} -- {command}",
        "metric": METRIC,
        "baseline": name,
        "tolerance": BEFORE_AFTER_FLOOR,
        "workload": target["workload"],
        "workload_class": target["class"],
        **({"cwd": target["cwd"]} if target.get("cwd") else {}),
    }


def save_baseline(repowise_dir: Path, baseline: Baseline) -> Path:
    path = baseline_path(repowise_dir, baseline.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(baseline.to_json(), indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_baseline(repowise_dir: Path, name: str) -> Baseline:
    path = baseline_path(repowise_dir, name)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BaselineError(
            f"no baseline named {name!r}; save one first with "
            f"`repowise perf measure --save {name} -- <command>`"
        ) from None
    except (OSError, ValueError) as exc:
        raise BaselineError(f"baseline {name!r} could not be read: {exc}") from None
    try:
        if raw.get("version") != BASELINE_VERSION:
            raise BaselineError(
                f"baseline {name!r} has format version {raw.get('version')!r}; save it again"
            )
        samples = [float(v) for v in raw["samples"]]
        if len(samples) < MIN_RUNS or not all(math.isfinite(v) and v > 0 for v in samples):
            raise BaselineError(f"baseline {name!r} holds no usable run times; save it again")
        return Baseline(
            name=name,
            command=str(raw["command"]),
            warmup=int(raw.get("warmup", 0)),
            measured_at=str(raw.get("measured_at", "")),
            side=Side(samples, raw.get("commit"), raw.get("dirty"), raw.get("cwd")),
            environment={str(k): str(v) for k, v in (raw.get("environment") or {}).items()},
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise BaselineError(f"baseline {name!r} is malformed ({exc}); save it again") from None


__all__ = [
    "BEFORE_AFTER_FLOOR",
    "DEFAULT_RUNS",
    "INTERLEAVED_FLOOR",
    "METRIC",
    "MIN_RUNS",
    "Baseline",
    "BaselineError",
    "CommandFailedError",
    "Comparison",
    "Side",
    "baseline_name",
    "baseline_path",
    "collect",
    "collect_interleaved",
    "compare",
    "interleave_order",
    "load_baseline",
    "save_baseline",
    "split_noise",
    "threshold",
    "verdict",
    "verify_measure",
]
