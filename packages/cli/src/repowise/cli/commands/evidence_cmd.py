"""``repowise evidence``: ingest what a real run showed and join it to the code.

``evidence add`` reads one ``repowise-evidence-v1`` file (a profile,
benchmark results, test timings or query counts), or a profiler's or
benchmark runner's own output in a format of
:data:`~repowise.core.evidence.profile_formats.profile_formats` (converted
to v1 first), joins its entries to the indexed symbols and stores
the run with where it came from: the commit, the checkout, the workload and
its class, and what did not map. The steps live in
:mod:`repowise.core.evidence.add`; this module words them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

import click
from rich.markup import escape

from repowise.cli._setup import configure_cli_logging
from repowise.cli.commands._report_ingest import (
    open_ingest_target,
    refuse_partial,
    report_stored_partial,
)
from repowise.cli.helpers import (
    ensure_repowise_dir,
    get_db_url_for_repo,
    reconcile_schema_best_effort,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.core.evidence import EVIDENCE_FORMAT
from repowise.core.evidence.add import (
    EvidenceRefusedError,
    PreparedEvidence,
    check_file_size,
    check_options,
    evidence_caps,
    prepare_evidence,
)
from repowise.core.evidence.format_v1 import VALUE_KINDS
from repowise.core.evidence.ingest import FLAG_WORKLOAD_CLASSES
from repowise.core.evidence.profile_formats import profile_formats


def _resolve_repo(path: str | None, notices: Any) -> Path:
    """The repository to ingest into (workspace-aware, like ``coverage``)."""
    target = resolve_command_target(path=path)
    return target.narrow(notices, command="evidence", repo_option=False)


@click.group("evidence")
def evidence_group() -> None:
    """Ingest runtime evidence (profiles, benchmarks, test timings, query counts)."""


@evidence_group.command("add")
@click.argument("file", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--workload", "workload", required=True, help="What ran, e.g. 'cli build'.")
@click.option(
    "--class",
    "workload_class",
    required=True,
    help="Workload class: " + ", ".join(FLAG_WORKLOAD_CLASSES) + ".",
)
@click.option(
    "--commit",
    default=None,
    help="The full SHA measured. Defaults to the file's commit_sha; required for a "
    "converted file.",
)
@click.option(
    "--checkout-root",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="The checkout the run used. Defaults to the repository root.",
)
@click.option(
    "--dirty/--clean",
    default=None,
    help="Whether the tree had uncommitted changes when measured. Defaults to the "
    "file's dirty field, else the working tree when the commit is HEAD.",
)
@click.option(
    "--environment",
    default="unknown",
    help="Where it ran, one word (local, ci, ...).",
)
@click.option(
    "--input-format",
    default=None,
    help="Read FILE as this format instead of detecting it from the content: "
    + ", ".join([EVIDENCE_FORMAT, *profile_formats.names])
    + ", or one a plugin registers.",
)
@click.option(
    "--value-kind",
    type=click.Choice(sorted(VALUE_KINDS)),
    default=None,
    help="The clock a converted profile sampled. Needed when the profiler's output "
    "does not say (collapsed stacks, py-spy).",
)
@click.option(
    "--allow-partial",
    is_flag=True,
    default=False,
    help="Store the run even when more than half its entries did not map (flagged partial).",
)
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary).")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show debug logs.")
@format_option()
def evidence_add(
    file: Path,
    workload: str,
    workload_class: str,
    commit: str | None,
    checkout_root: Path | None,
    dirty: bool | None,
    environment: str,
    input_format: str | None,
    value_kind: str | None,
    allow_partial: bool,
    repo: str | None,
    verbose: bool,
    fmt: str,
) -> None:
    """Ingest one evidence FILE for a named workload.

    FILE is a repowise-evidence-v1 document, or a profile or benchmark
    results as the tool wrote them (Speedscope JSON, Go pprof, V8 .cpuprofile,
    collapsed stacks, pytest-benchmark JSON), detected from its content.
    A profile's frames join to the indexed functions only under the checkout
    root, each checked by a second signal; the rest are counted by reason.
    v1 entries name their function (path, qualified name, line) and match
    exactly. Evidence from another commit than HEAD keeps its commit and
    reads as stale; it is never re-mapped. The production class is set only
    by a verified source, never by this flag.

    Examples:

        repowise evidence add prof.json --workload "cli build" --class entry_point
        repowise evidence add prof.speedscope.json --workload "cli build" --class entry_point --commit $(git rev-parse HEAD)
        repowise evidence add stacks.txt --workload "api" --class load_test --value-kind cpu --commit $(git rev-parse HEAD)
        repowise evidence add bench.json --workload "pytest bench" --class benchmark --commit $(git rev-parse HEAD)
        repowise evidence add bench.json --workload "go bench" --class benchmark --format json

    Exits 1 when nothing was stored, 2 on a usage error.
    """
    configure_cli_logging(verbose=verbose)
    notices = notice_console(fmt)
    commit = commit.lower() if commit is not None else None
    try:
        check_options(workload, workload_class, environment, commit)
    except EvidenceRefusedError as exc:
        _refuse(exc, fmt)

    repo_path = _resolve_repo(repo, notices)
    ensure_repowise_dir(repo_path)
    root = (checkout_root or repo_path).resolve()
    if not root.is_dir():
        raise click.BadParameter(f"{root} is not a directory.", param_hint="--checkout-root")

    from repowise.core.repo_config import load_repo_config

    max_file_mb, caps, problems = evidence_caps(load_repo_config(repo_path))
    for problem in problems:
        notices.print(f"[yellow]Ignoring {escape(problem)}.[/yellow]")
    try:
        size = file.stat().st_size
    except OSError as exc:
        raise click.BadParameter(f"cannot read {file}: {exc.strerror}", param_hint="FILE") from None
    try:
        # Checked before reading, so a file too large is never loaded.
        check_file_size(size, max_file_mb, str(file))
        prepared = prepare_evidence(
            file.read_bytes(),
            name=str(file),
            checkout_root=root,
            commit=commit,
            dirty=dirty,
            input_format_name=input_format,
            value_kind=value_kind,
        )
    except EvidenceRefusedError as exc:
        _refuse(exc, fmt)
    payload = run_async(
        _ingest(
            repo_path=repo_path,
            prepared=prepared,
            workload=workload.strip(),
            workload_class=workload_class,
            environment=environment,
            allow_partial=allow_partial,
            caps=caps,
            notices=notices,
        )
    )
    if fmt == "json":
        emit_json(payload)
    if not payload["stored"]:
        raise click.exceptions.Exit(1)


def _refuse(exc: EvidenceRefusedError, fmt: str) -> NoReturn:
    """A usage error for an option at fault, else the refusal for the file."""
    if exc.option is not None:
        raise click.BadParameter(str(exc), param_hint=exc.option) from None
    emit_refusal(exc.code, str(exc), fmt, remedy=exc.hint)
    raise exc  # unreachable: emit_refusal exits


async def _ingest(*, repo_path: Path, **ingest: Any) -> dict[str, Any]:
    """Open the index at *repo_path* and store the run (:func:`_store`)."""
    from repowise.core.persistence import create_engine, create_session_factory, get_session

    url = get_db_url_for_repo(repo_path)
    await reconcile_schema_best_effort(url)
    engine = create_engine(url)
    try:
        async with get_session(create_session_factory(engine)) as session:
            return await _store(session, repo_path=repo_path, **ingest)
    finally:
        await engine.dispose()


async def _store(
    session: Any,
    *,
    repo_path: Path,
    prepared: PreparedEvidence,
    workload: str,
    workload_class: str,
    environment: str,
    allow_partial: bool,
    caps: Any,
    notices: Any,
) -> dict[str, Any]:
    """Store the run (:func:`~repowise.core.evidence.add.store_evidence`) and say what happened."""
    from repowise.core.evidence.add import store_evidence

    target = await open_ingest_target(session, repo_path, what="evidence", out=notices)
    if target is None:
        return {"stored": False, "reason": "no_index"}
    stored = await store_evidence(
        session,
        target.repo,
        prepared,
        repo_path=repo_path,
        workload=workload,
        workload_class=workload_class,
        environment=environment,
        allow_partial=allow_partial,
        caps=caps,
    )
    payload = stored.payload
    conversion = payload.get("conversion")
    if conversion is not None:
        _report_conversion(notices, prepared.format, conversion)
        _warn_short_run(
            notices,
            workload_class,
            prepared.value_kind or prepared.run.value_kind,
            conversion,
        )
    if payload["stored"]:
        _report_stored(
            notices,
            payload,
            payload["restamped"],
            stored.rows_kept,
            payload["truncated"],
            payload["index_commit"],
        )
    elif payload["reason"] == "mostly_unmapped":
        refuse_partial(
            "evidence file",
            "write each entry's path relative to the repository root and its "
            "function as the qualified name repowise indexed",
            out=notices,
        )
    else:
        notices.print("[red]No entry matched an indexed function, so nothing was stored.[/red]")
    return payload


def _report_conversion(notices: Any, name: str, summary: dict[str, Any]) -> None:
    """Where the profile's time went and how its frames joined, never silent."""
    from repowise.core.evidence.status import split_words

    rate = summary["skip_rate"]
    split = split_words(summary) or "repo code itself unknown"
    skipped = "no repo frames" if rate is None else f"{rate:.0%} of repo frame time skipped"
    reasons = ", ".join(
        f"{reason} {part:.0%}" for reason, part in summary["skipped_share_by_reason"].items()
    )
    notices.print(
        f"Converted {escape(name)}: {split} of "
        f"{_total(summary['total'])} {escape(summary['unit'])}; {skipped}"
        + (f" ({escape(reasons)})." if reasons else ".")
    )


#: Seconds in one unit of a timed profile; a ``samples`` unit says nothing of time.
_SECONDS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}
#: An entry point measured for less than this most likely did no real work.
_SHORT_RUN_S = 3.0


def _warn_short_run(
    notices: Any, workload_class: str, value_kind: str | None, summary: dict[str, Any]
) -> None:
    """Warn (never refuse) when an entry point's wall-clock profile covers only a few seconds.

    A program that deferred to another process or printed its usage exits 0
    having measured nothing representative, and its run would still rank.
    """
    per = _SECONDS.get(summary["unit"])
    if workload_class != "entry_point" or value_kind != "wall" or per is None:
        return
    seconds = summary["total"] * per
    if seconds >= _SHORT_RUN_S:
        return
    notices.print(
        f"[yellow]This entry point's profile covers {seconds:.1f} s: a run "
        "that deferred its work or printed its usage measures little. Check its output, and "
        "if it did no real work, run it again with arguments that do.[/yellow]"
    )


def _report_stored(
    notices: Any,
    summary: dict[str, Any],
    restamped: bool,
    stored: int,
    truncated: int,
    index_commit: str | None,
) -> None:
    """Print what was stored, what was left out and whether it reads as current."""
    verb = "Restamped" if restamped else "Stored"
    notices.print(
        f"[green]{verb} {summary['kind']} evidence for {escape(summary['workload'])}[/green]: "
        f"{stored} of {summary['entries']} entries stored at {summary['commit_sha'][:12]}."
    )
    labels = (("Skipped", summary["skipped"]), ("Kept without a function", summary["unlinked"]))
    for label, counts in labels:
        if counts:
            reasons = ", ".join(f"{reason} {count}" for reason, count in counts.items())
            notices.print(f"[yellow]{label}, by reason: {escape(reasons)}.[/yellow]")
    if truncated:
        notices.print(f"[yellow]{truncated} entries left out at the per-run cap.[/yellow]")
    if summary["tests_failed"]:
        notices.print(
            f"[yellow]{summary['tests_failed']:,} tests failed in the profiled run; the suite "
            "still ran, so the profile is kept and the count is recorded on the run.[/yellow]"
        )
    if summary["mapping_partial"]:
        report_stored_partial("evidence", out=notices)
    _report_staleness(notices, summary, index_commit)


def _report_staleness(notices: Any, summary: dict[str, Any], index_commit: str | None) -> None:
    if summary["stale"]:
        notices.print(
            "[yellow]Measured at another commit than HEAD: it is kept with its own "
            "commit and reads as stale.[/yellow]"
        )
    elif index_commit and index_commit != summary["commit_sha"]:
        notices.print(
            "[yellow]The index is at another commit than the one measured; run "
            "[cyan]repowise update[/cyan] and add the file again to join it to the "
            "current code.[/yellow]"
        )


@evidence_group.command("setup")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary).")
@format_option()
def evidence_setup_command(repo: str | None, fmt: str) -> None:
    """Print how to collect evidence here; run nothing, write nothing.

    For each build unit: the stack, the command that writes a coverage report
    repowise reads (run from the unit's root), what it needs first, where the
    report lands, and the exact ingest line (run from the repository root).
    Then its runtime targets, entry points and benchmarks before the unit-test
    suite, each with a profiler command that needs no privileges and the exact
    ``evidence add`` line. ``next_call`` lists the steps to run next, each
    with its directory: a coverage report on disk first, then the best
    profile, then the tests with coverage. ``stack: unknown`` when repowise is
    not sure how a unit runs. Works before the repository is indexed.

    Examples:

        repowise evidence setup
        repowise evidence setup --format json
    """
    _print_setup(repo, fmt)


def _print_setup(repo: str | None, fmt: str, *, coverage_only: bool = False) -> None:
    """Shared by ``evidence setup`` and ``coverage setup`` (*coverage_only*)."""
    from repowise.core.evidence.setup import evidence_setup
    from repowise.core.workspace.update.state import get_head_commit

    notices = notice_console(fmt)
    repo_path = _resolve_repo(repo, notices)
    indexed, units, coverage_commit, runtime, index_error, index_fix = run_async(
        _stored_inputs(repo_path, runtime=not coverage_only)
    )
    plan = evidence_setup(
        repo_path,
        indexed=indexed,
        units=units,
        head=get_head_commit(repo_path),
        coverage_commit=coverage_commit,
        runtime=runtime,
        coverage_only=coverage_only,
        index_error=index_error,
        index_fix=index_fix,
    )
    if fmt == "json":
        emit_json(plan)
        return
    for unit in plan["units"]:
        for line in _setup_lines(unit):
            notices.print(line)
    call = plan["next_call"]
    if plan["reason"]:
        notices.print(f"[yellow]{escape(plan['reason'])}[/yellow]")
    if call is None:
        return
    notices.print(f"[bold]Next[/bold]: {escape(call['purpose'])}")
    for step in call["steps"]:
        notices.print(f"  [dim]in {escape(step['cwd'])}[/dim]\n    [cyan]{escape(step['run'])}[/cyan]")
    if call.get("optional"):
        optional = call["optional"]
        notices.print(f"[bold]Optional[/bold]: {escape(', '.join(optional['workloads']))}. "
                      f"{escape(optional['purpose'])}")


def _setup_lines(unit: dict[str, Any]) -> list[str]:
    """One unit's coverage plan, in words."""
    cov = unit["coverage"]
    tools = ", ".join(t for t in (unit["runner"], unit["package_manager"]) if t)
    head = f"[bold]{unit['stack']}[/bold]" + (f" ({escape(tools)})" if tools else "")
    lines = [f"{head} at {escape(unit['root'] or 'the repository root')}"]
    if cov["command"]:
        lines += [f"  needs: [cyan]{escape(p)}[/cyan]" for p in cov["prerequisites"]]
        lines.append(f"  coverage: [cyan]{escape(cov['command'])}[/cyan]")
    elif cov["reason"]:
        lines.append(f"  [yellow]{escape(cov['reason'])}[/yellow]")
    if cov["found"]:
        lines.append(f"  on disk: {escape(', '.join(cov['found']))}")
    if cov["ingest"]:
        lines.append(f"  ingest (from the repository root): [cyan]{escape(cov['ingest'])}[/cyan]")
    for target in unit.get("runtime", ()):
        head = f"  {target['class']} [bold]{escape(target['workload'])}[/bold]"
        if not target["command"]:
            lines.append(f"{head}: [yellow]{escape(target['reason'])}[/yellow]")
            continue
        lines.append(f"{head}: [cyan]{escape(target['command'])}[/cyan]")
        if target["reason"]:
            lines.append(f"    [yellow]{escape(target['reason'])}[/yellow]")
        if target["ingest"]:
            lines.append(f"    then: [cyan]{escape(target['ingest'])}[/cyan]")
    return lines


async def _stored_inputs(
    repo_path: Path, *, runtime: bool
) -> tuple[bool, Any, str | None, Any, str | None, str]:
    """Whether *repo_path* has an index, its stored build units, the commit its
    newest coverage measured, its runtime inputs, why an existing index could
    not be read (None when it could, or when there is none), and the command
    that fixes it.

    Opens the store only when one exists, so a repository never indexed gets
    its plan and no empty store is left behind.
    """
    from repowise.cli.helpers import StaleIndexError, repo_index_session
    from repowise.core.evidence.runtime_setup import load_runtime_inputs
    from repowise.core.evidence.setup import DOCTOR_COMMAND, UPDATE_COMMAND
    from repowise.core.persistence.crud.analysis.coverage import load_newest_ingest
    from repowise.core.persistence.crud.build_units import load_build_units
    from repowise.core.persistence.database import has_db_store

    if not has_db_store(repo_path):
        return False, None, None, None, None, DOCTOR_COMMAND
    try:
        async with repo_index_session(repo_path) as opened:
            if opened is not None:
                session, repo_id = opened
                ingest = await load_newest_ingest(session, repo_id)
                commit = ingest.ingested_commit_sha if ingest is not None else None
                units = await load_build_units(session, repo_id)
                inputs = await load_runtime_inputs(session, repo_id) if runtime else None
                return True, units, commit, inputs, None, DOCTOR_COMMAND
    except StaleIndexError as exc:
        # Written by an older repowise and not repairable in place; its message says to update.
        return False, None, None, None, exc.message, UPDATE_COMMAND
    # The session swallows read errors; ask once more to tell a store that
    # does not hold this repository from one that cannot be read.
    return False, None, None, None, await _index_read_error(repo_path), DOCTOR_COMMAND


async def _index_read_error(repo_path: Path) -> str | None:
    """Why the existing store cannot be read for *repo_path*, or None when it can."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path

    try:
        engine = create_engine(get_db_url_for_repo(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                await get_repository_by_path(session, str(repo_path))
        finally:
            await engine.dispose()
    except (SQLAlchemyError, OSError, LookupError) as exc:
        first = (str(exc).splitlines() or [""])[0]
        return f"{type(exc).__name__}: {first[:200]}"
    return None


@evidence_group.command("status")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd / workspace primary).")
@format_option()
def evidence_status_command(repo: str | None, fmt: str) -> None:
    """Show the runtime evidence stored: the newest run of each workload.

    Each run says how fresh it is against the indexed commit (current, stale
    or unknown), where its time went (production repo code itself, waits,
    libraries), and what was skipped.
    """
    notices = notice_console(fmt)
    repo_path = _resolve_repo(repo, notices)
    payload = run_async(_status(repo_path))
    if fmt == "json":
        emit_json(payload)
        return
    if not payload["indexed"]:
        notices.print("[yellow]No index yet. Run `repowise init`.[/yellow]")
        return
    if not payload["workloads"]:
        notices.print(
            "[yellow]No runtime evidence stored.[/yellow] Add a run with "
            "[cyan]repowise evidence add <file> --workload <name> --class <class>[/cyan]."
        )
        return
    for run in payload["workloads"]:
        notices.print(_status_line(run))


def _status_line(run: dict[str, Any]) -> str:
    """One workload's newest run, in words; an unknown value says so, never 0."""
    from repowise.core.evidence.status import split_words

    split = split_words(run)
    if split is None:
        # A profile stored before the split was recorded; other kinds have none.
        explained = "; time split unknown" if run["kind"] == "profile" else ""
    else:
        unit = f" {escape(run['unit'])}" if run["unit"] else ""
        explained = f"; {split} of {_total(run['total_value'])}{unit}"
    units = run["units"]
    stored = (
        f"{units['matched']} of {units['total']} entries stored"
        if units["matched"] is not None and units["total"] is not None
        else "entry counts unknown"
    )
    return (
        f"[bold]{escape(run['workload'])}[/bold] ({run['kind']}, {run['class']}, "
        f"{escape(run['environment'])}): {run['freshness']} at "
        f"{(run['commit_sha'] or 'unknown')[:12]}; {stored}{explained}"
        + (f"; {run['tests_failed']:,} tests failed" if run["tests_failed"] else "")
        + "."
        + (" [red](partial)[/red]" if run["mapping_partial"] else "")
    )


def _total(value: float) -> str:
    """A run total with grouping and never exponent form: 1,234,567 or 0.25."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


async def _status(repo_path: Path) -> dict[str, Any]:
    from repowise.core.evidence.status import evidence_status
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path, list_evidence_runs

    url = get_db_url_for_repo(repo_path)
    await reconcile_schema_best_effort(url)
    engine = create_engine(url)
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo_row = await get_repository_by_path(session, str(repo_path))
            if repo_row is None:
                # Same keys as an indexed repo, so a JSON reader never meets a missing one.
                return {"repo": str(repo_path), "indexed": False, **evidence_status([], None)}
            runs = await list_evidence_runs(session, repo_row.id)
            return {
                "repo": str(repo_path),
                "indexed": True,
                **evidence_status(runs, repo_row.head_commit),
            }
    finally:
        await engine.dispose()
