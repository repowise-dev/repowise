"""``repowise perf measure``: time a command before and after a change.

Runs only the command typed after ``--``, the way ``distill`` does. The rule
that turns run times into a verdict lives in :mod:`repowise.core.perf_measure`.
"""

from __future__ import annotations

import platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import click
from rich.markup import escape

from repowise.cli.ci import cannot_evaluate
from repowise.cli.helpers import ensure_repowise_dir, find_repowise_repo_root, get_repowise_dir
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.core.command_runner import (
    CommandRun,
    UnrenderableCommandError,
    render_command,
    run_command,
)
from repowise.core.git_head import find_checkout, read_head_commit
from repowise.core.perf_measure import (
    DEFAULT_RUNS,
    MIN_RUNS,
    Baseline,
    BaselineError,
    CommandFailedError,
    Comparison,
    Side,
    baseline_path,
    collect,
    collect_interleaved,
    compare,
    load_baseline,
    save_baseline,
    split_noise,
)

_DEFAULT_TIMEOUT = 600.0
#: A regressed verdict. Informational: an agent branches on it; nothing gates on it.
_EXIT_REGRESSED = 1


@click.group("perf")
def perf_group() -> None:
    """Measure a command's wall time and compare it before and after a change."""


@perf_group.command(
    "measure",
    # Options are parsed only before the command, so a command that takes
    # --runs or --save of its own is never touched.
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
    short_help="Time a command and say whether a change made it faster or slower.",
)
@click.option("--save", "save_name", metavar="NAME", help="Store these runs as baseline NAME.")
@click.option(
    "--against", "against_name", metavar="NAME", help="Compare these runs with baseline NAME."
)
@click.option(
    "--base-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Alternate runs between this checkout (base) and the current directory (head).",
)
@click.option(
    "--runs",
    type=click.IntRange(MIN_RUNS, 1000),
    default=DEFAULT_RUNS,
    show_default=True,
    help="Timed runs per side.",
)
@click.option(
    "--warmup",
    type=click.IntRange(0, 100),
    default=1,
    show_default=True,
    help="Untimed runs per side before the timed ones.",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=_DEFAULT_TIMEOUT,
    show_default=True,
    help="Seconds one run may take before it is killed.",
)
@format_option()
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
def perf_measure_command(
    save_name: str | None,
    against_name: str | None,
    base_dir: Path | None,
    runs: int,
    warmup: int,
    timeout: float,
    fmt: str,
    command: tuple[str, ...],
) -> None:
    """Run COMMAND several times and compare its wall time with a base.

    \b
        repowise perf measure --save base -- pytest tests/test_parse.py
        <edit the code>
        repowise perf measure --against base -- pytest tests/test_parse.py
        repowise perf measure --base-dir ../main-checkout -- python -m mypkg build

    Pick one mode. --save stores the runs in .repowise/perf/NAME.json with the
    commit and whether the tree was dirty. --against runs the same command
    again and compares. --base-dir alternates runs between that checkout and
    this one in a single session, which cancels most machine drift.

    The verdict compares median run times (head / base) with a 95% bootstrap
    interval. It says improved or regressed only when the whole interval clears
    1 by more than the threshold: twice the base-versus-base noise (each
    side's earlier half of runs against its later half), at least 5% before
    and after, or 2% interleaved. Anything else is inconclusive.

    Exit codes: 0 saved, improved or inconclusive; 1 regressed; 2 nothing
    could be measured (a run failed or timed out, a bad baseline). Every run
    must exit 0: a failing command is an error, never a timing. Wall time is
    noisy, so the exit code is informational: do not gate CI on it.
    """
    modes = [m for m in (save_name, against_name, base_dir) if m is not None]
    if len(modes) != 1:
        cannot_evaluate(fmt, "usage", "pass exactly one of --save, --against or --base-dir")
    try:
        command_str = render_command(command)
    except UnrenderableCommandError as exc:
        cannot_evaluate(fmt, "unrenderable_command", str(exc))

    here = Path.cwd().resolve()
    job = _Job(
        command=command_str,
        # As typed, for the progress line; the rendered line carries cmd.exe escapes.
        shown=" ".join(command),
        runner=partial(_run_once, command_str, timeout),
        here=here,
        runs=runs,
        warmup=warmup,
        fmt=fmt,
    )
    try:
        if save_name is not None:
            _save(job, save_name)
        elif against_name is not None:
            _against(job, against_name)
        else:
            assert base_dir is not None
            _interleaved(job, base_dir.resolve())
    except CommandFailedError as exc:
        cannot_evaluate(fmt, "command_failed", _failure_message(exc))
    except BaselineError as exc:
        cannot_evaluate(fmt, "baseline", str(exc))
    except OSError as exc:
        cannot_evaluate(fmt, "io_error", f"could not run or store the measurement: {exc}")
    except KeyboardInterrupt:
        cannot_evaluate(fmt, "interrupted", "interrupted; nothing was measured or saved")


@dataclass(frozen=True)
class _Job:
    """One invocation's command and settings, shared by the three modes."""

    command: str
    shown: str
    runner: Callable[[Path], CommandRun]
    here: Path
    runs: int
    warmup: int
    fmt: str

    def announce(self, *, sides: int) -> None:
        notice_console(self.fmt).print(_plan_line(self.shown, self.runs, self.warmup, sides=sides))

    def collect(self, *, side: str = "head") -> list[float]:
        return collect(self.runner, self.here, runs=self.runs, warmup=self.warmup, side=side)


def _save(job: _Job, name: str) -> None:
    root = _repo_root(job.here)
    baseline_path(get_repowise_dir(root), name)  # refuse a bad name before any run
    repowise_dir = ensure_repowise_dir(root)
    job.announce(sides=1)
    samples = job.collect(side="base")
    baseline = Baseline(
        name=name,
        command=job.command,
        warmup=job.warmup,
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        side=_side(samples, job.here),
        environment=_environment(),
    )
    path = save_baseline(repowise_dir, baseline)
    _emit_saved(baseline, path, job.fmt)


def _against(job: _Job, name: str) -> None:
    baseline = load_baseline(get_repowise_dir(_repo_root(job.here)), name)
    if baseline.command != job.command:
        cannot_evaluate(
            job.fmt,
            "command_mismatch",
            f"baseline {name!r} timed `{baseline.command}`, not "
            f"`{job.command}`; compare the same command or save a new baseline",
        )
    notices = notice_console(job.fmt)
    if baseline.environment.get("host") not in (None, _environment()["host"]):
        notices.print(
            "[yellow]Warning:[/yellow] the baseline was measured on another "
            "machine, so the comparison mixes in machine differences"
        )
    job.announce(sides=1)
    head = _side(job.collect(), job.here)
    if _same_code(baseline.side, head):
        notices.print(
            "[yellow]Warning:[/yellow] base and head are the same commit with "
            "clean trees, so this compares the code with itself"
        )
    _emit_comparison(compare(baseline.side, head, "before_after"), job, baseline=name)


def _interleaved(job: _Job, base_root: Path) -> None:
    if base_root == job.here:
        cannot_evaluate(
            job.fmt, "usage", "--base-dir is the current directory; name another checkout"
        )
    job.announce(sides=2)
    base_samples, head_samples = collect_interleaved(
        job.runner, base_root, job.here, runs=job.runs, warmup=job.warmup
    )
    result = compare(_side(base_samples, base_root), _side(head_samples, job.here), "interleaved")
    _emit_comparison(result, job, baseline=None)


def _same_code(base: Side, head: Side) -> bool:
    return bool(head.commit) and head.commit == base.commit and not (head.dirty or base.dirty)


def _failure_message(exc: CommandFailedError) -> str:
    message = f"{exc}; nothing was measured. Fix the command first."
    tail = exc.run.output_tail.strip()
    return f"{message}\nLast output:\n{tail}" if tail else message


def _run_once(command: str, timeout: float, cwd: Path) -> CommandRun:
    return run_command(command, cwd=cwd, timeout=timeout)


def _repo_root(here: Path) -> Path:
    """Where ``.repowise/`` is: the nearest one up, else the checkout root, else here."""
    found = find_repowise_repo_root(here)
    if found is not None:
        return found
    checkout = find_checkout(here)
    return checkout.root if checkout is not None else here


def _side(samples: list[float], cwd: Path) -> Side:
    checkout = find_checkout(cwd)
    if checkout is None:
        return Side(samples, None, None, str(cwd))
    return Side(samples, read_head_commit(checkout.root), _dirty(checkout.root), str(cwd))


def _dirty(root: Path) -> bool | None:
    from repowise.core.analysis.change_risk.features import working_tree_is_dirty

    try:
        return working_tree_is_dirty(str(root))
    except Exception:
        # The dirty flag is context for the reader; a git that cannot answer
        # leaves it unknown rather than failing a measurement that worked.
        return None


def _environment() -> dict[str, str]:
    return {"os": platform.system(), "host": platform.node()}


def _plan_line(command: str, runs: int, warmup: int, *, sides: int) -> str:
    each = " per side" if sides == 2 else ""
    return f"[dim]Timing `{escape(command)}`: {warmup} warmup + {runs} runs{each}[/dim]"


def _fmt_seconds(value: float) -> str:
    return f"{value * 1000:.1f} ms" if value < 1 else f"{value:.3f} s"


def _side_line(label: str, side: Side) -> str:
    where = []
    if side.commit:
        where.append(f"commit {side.commit[:12]}")
    if side.dirty:
        where.append("dirty tree")
    suffix = f", {', '.join(where)}" if where else ""
    return (
        f"  {label:<5} median {_fmt_seconds(side.median)} "
        f"({len(side.samples)} runs, noise {split_noise(side.samples):.1%}{suffix})"
    )


def _emit_saved(baseline: Baseline, path: Path, fmt: str) -> None:
    if fmt == "json":
        emit_json(
            {
                "mode": "save",
                "verdict": "saved",
                "baseline": baseline.name,
                "path": str(path),
                "command": baseline.command,
                "base": baseline.side.to_json(),
            }
        )
        return
    click.echo(f"Saved baseline {baseline.name!r} to {path}")
    click.echo(_side_line("base", baseline.side))


def _emit_comparison(result: Comparison, job: _Job, *, baseline: str | None) -> None:
    if job.fmt == "json":
        emit_json({**result.to_json(), "command": job.command, "baseline": baseline})
    else:
        mode = "interleaved" if result.mode == "interleaved" else "before and after"
        iv = result.interval
        click.echo(f"{result.verdict.capitalize()} ({mode})")
        click.echo(_side_line("base", result.base))
        click.echo(_side_line("head", result.head))
        click.echo(
            f"  ratio head/base {iv.estimate:.3f}, {iv.confidence:.0%} interval "
            f"{iv.low:.3f} to {iv.high:.3f}"
        )
        click.echo(
            f"  threshold {result.threshold:.1%}: a verdict needs the interval below "
            f"{1 - result.threshold:.3f} or above {1 + result.threshold:.3f}"
        )
    # Informational: a regressed verdict exits 1 so an agent can branch on it.
    if result.verdict == "regressed":
        raise click.exceptions.Exit(_EXIT_REGRESSED)


__all__ = ["perf_group", "perf_measure_command"]
