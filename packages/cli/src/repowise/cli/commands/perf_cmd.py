"""``repowise perf``: time a command before and after a change, and gate on exact evidence.

``perf measure`` runs only the command typed after ``--``, the way
``distill`` does; the rule that turns run times into a verdict lives in
:mod:`repowise.core.perf_measure`. ``perf check`` runs nothing: it compares
the exact values of stored benchmark runs with a saved baseline, by the rule
in :mod:`repowise.core.perf_check`.
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

from repowise.cli.ci import EXIT_GATE_FAILED, cannot_evaluate
from repowise.cli.helpers import (
    ensure_repowise_dir,
    find_repowise_repo_root,
    get_db_url_for_repo,
    get_repowise_dir,
    reconcile_schema_best_effort,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.core import perf_check
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
    """Compare a command's wall time, or a benchmark's allocations, before and after a change."""


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


@perf_group.command(
    "check", short_help="Fail when a change makes a benchmark allocate more per operation."
)
@click.option(
    "--save",
    "save_name",
    metavar="NAME",
    help="Store the newest benchmark run of each workload as exact baseline NAME.",
)
@click.option(
    "--against",
    "against_name",
    metavar="NAME",
    help="Compare the newest benchmark runs with exact baseline NAME.",
)
@click.option(
    "--workload",
    default=None,
    help="Only this workload's runs. With --save, the baseline's other workloads are kept.",
)
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary).")
@format_option()
def perf_check_command(
    save_name: str | None,
    against_name: str | None,
    workload: str | None,
    repo: str | None,
    fmt: str,
) -> None:
    """Gate a change on exact runtime evidence: allocations per operation.

    \b
        repowise perf check --save base
        <edit the code, run the benchmarks again, repowise evidence add the results>
        repowise perf check --against base

    Reads the newest stored benchmark run of each workload (go test -bench
    -benchmem results ingested with repowise evidence add). --save stores
    their allocations per operation in .repowise/perf/exact/NAME.json, beside
    perf measure's baselines and never in place of one. --against compares
    the newest runs now with that baseline: a benchmark that allocates more
    per operation than before regresses. A workload whose newest run is still
    the saved one, not ingested since, was not run again and is not judged;
    a benchmark the run now lacks is listed as dropped.

    Timing never gates: time per operation is stored, but only exact values
    are compared. perf measure compares wall time.

    Exit codes: 0 saved, or no exact value grew; 1 a benchmark allocates more
    per operation; 2 nothing could be judged (no index, no exact value on
    both sides, a bad baseline) or a usage error.
    """
    if (save_name is None) == (against_name is None):
        cannot_evaluate(fmt, "usage", "pass exactly one of --save or --against")
    notices = notice_console(fmt)
    repo_path = resolve_command_target(path=repo).narrow(notices, command="perf", repo_option=False)
    try:
        if save_name is not None:
            perf_check.snapshot_path(get_repowise_dir(repo_path), save_name)  # refuse a bad name first
            current = _current_snapshot(repo_path, workload, fmt)
            if not current["workloads"]:
                cannot_evaluate(fmt, "no_exact_evidence", _no_exact(repo_path))
            if workload is not None:
                current = _keep_other_workloads(repo_path, save_name, current)
            path = perf_check.save_snapshot(ensure_repowise_dir(repo_path), save_name, current)
            _emit_snapshot_saved(save_name, path, current, fmt)
        else:
            assert against_name is not None
            base = perf_check.load_snapshot(get_repowise_dir(repo_path), against_name)
            if workload is not None:
                if workload not in base["workloads"]:
                    cannot_evaluate(
                        fmt, "baseline", f"baseline {against_name!r} has no workload {workload!r}"
                    )
                base = {**base, "workloads": {workload: base["workloads"][workload]}}
            result = perf_check.compare(base, _current_snapshot(repo_path, workload, fmt))
            _emit_check(result, against_name, fmt)
    except BaselineError as exc:
        cannot_evaluate(fmt, "baseline", str(exc))
    except OSError as exc:
        cannot_evaluate(fmt, "io_error", f"could not read or store the baseline: {exc}")


def _keep_other_workloads(repo_path: Path, name: str, current: dict) -> dict:
    """*current* (one workload) over baseline *name*'s other workloads, when it exists."""
    if not perf_check.snapshot_path(get_repowise_dir(repo_path), name).is_file():
        return current
    kept = perf_check.load_snapshot(get_repowise_dir(repo_path), name)["workloads"]
    return {**current, "workloads": dict(sorted({**kept, **current["workloads"]}.items()))}


_NO_EXACT = (
    "no stored benchmark run reports allocations per operation; run the Go benchmark "
    "results target `repowise evidence setup` prints and its ingest line, then save"
)
_NOTHING_TO_GATE = (
    "no stored benchmark run reports allocations per operation, and this repository has no "
    "Go benchmark (`go test -bench`) that `repowise evidence setup` can run, so there is "
    "nothing to gate; `repowise perf measure -- <command>` compares a command's wall time "
    "before and after a change"
)


def _no_exact(repo_path: Path) -> str:
    """Why there is nothing to save: name setup's Go benchmark results target only when it prints one."""
    head = read_head_commit(repo_path)
    # Without a commit setup offers nothing to ingest, which says nothing about the benchmarks.
    offer = run_async(_runtime_offer(repo_path, head)) if head else None
    if offer is None or any(t.get("format") in perf_check.EXACT_FORMATS for t in offer):
        return _NO_EXACT
    return _NOTHING_TO_GATE


async def _runtime_offer(repo_path: Path, head: str) -> list[dict] | None:
    from repowise.cli.helpers import repo_index_session
    from repowise.core.evidence.setup import load_runtime_offer

    async with repo_index_session(repo_path) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        return await load_runtime_offer(session, repo_id, repo_path, head)


def _current_snapshot(repo_path: Path, workload: str | None, fmt: str) -> dict:
    """The newest benchmark run of each workload with its exact values; exits 2 without an index."""
    from sqlalchemy.exc import SQLAlchemyError

    try:
        current = run_async(_read_current(repo_path, workload))
    except SQLAlchemyError as exc:
        cannot_evaluate(fmt, "store_error", f"could not read the stored evidence: {exc}")
    if current is None:
        cannot_evaluate(fmt, "no_index", "No index for this repository. Run `repowise init`.")
    return current


async def _read_current(repo_path: Path, workload: str | None) -> dict | None:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import (
        benchmark_results,
        get_repository_by_path,
        list_evidence_runs,
    )

    url = get_db_url_for_repo(repo_path)
    await reconcile_schema_best_effort(url)
    engine = create_engine(url)
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo_row = await get_repository_by_path(session, str(repo_path))
            if repo_row is None:
                return None
            runs = await list_evidence_runs(session, repo_row.id)
            newest = perf_check.newest_runs(runs, workload)
            rows = await benchmark_results(session, [run.id for run in newest.values()])
            return perf_check.snapshot(newest, rows)
    finally:
        await engine.dispose()


def _emit_snapshot_saved(name: str, path: Path, snap: dict, fmt: str) -> None:
    counts = {w: len(v["benchmarks"]) for w, v in snap["workloads"].items()}
    if fmt == "json":
        emit_json(
            {
                "mode": "save",
                "verdict": "saved",
                "baseline": name,
                "path": str(path),
                "metrics": list(perf_check.EXACT_METRICS),
                "benchmarks": counts,
            }
        )
        return
    click.echo(f"Saved exact baseline {name!r} to {path}")
    for workload, count in counts.items():
        click.echo(f"  {workload}: {count} benchmarks")


def _emit_check(result: dict, name: str, fmt: str) -> None:
    if result["verdict"] == "cannot_judge":
        cannot_evaluate(fmt, "cannot_judge", f"nothing to judge against {name!r}: {result['reason']}")
    if fmt == "json":
        emit_json({**result, "baseline": name})
    else:
        verdict = "Regressed" if result["verdict"] == "regressed" else "Passed"
        click.echo(
            f"{verdict}: {len(result['regressed'])} of {result['compared']} benchmarks "
            f"allocate more per operation than baseline {name!r}"
        )
        for change in (*result["regressed"], *result["improved"]):
            click.echo(
                f"  {change['change']:<9} {change['workload']}: {change['benchmark']} "
                f"allocs/op {change['base']:g} -> {change['head']:g}"
            )
        notices = notice_console(fmt)
        dropped = [f"{d['workload']}: {d['benchmark']}" for d in result["dropped"]]
        for names, words in (
            (result["not_rerun"], "not run again since the baseline"),
            (result["missing"], "no stored run now"),
            (dropped, "benchmark gone from the run now"),
        ):
            if names:
                notices.print(f"[yellow]Not judged ({words}):[/yellow] {escape(', '.join(names))}")
        notices.print("[dim]Timing is not judged; perf measure compares wall time.[/dim]")
    if result["verdict"] == "regressed":
        raise click.exceptions.Exit(EXIT_GATE_FAILED)


__all__ = ["perf_check_command", "perf_group", "perf_measure_command"]
