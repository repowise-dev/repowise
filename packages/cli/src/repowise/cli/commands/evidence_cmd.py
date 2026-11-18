"""``repowise evidence``: ingest what a real run showed and join it to the code.

``evidence add`` reads one ``repowise-evidence-v1`` file (a profile,
benchmark results, test timings or query counts), or a profiler's or
benchmark runner's own output in a format of
:data:`~repowise.core.evidence.profile_formats.profile_formats` (converted
to v1 first), joins its entries to the indexed symbols and stores
the run with where it came from: the commit, the checkout, the workload and
its class, and what did not map.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from repowise.core.evidence.format_v1 import VALUE_KINDS
from repowise.core.evidence.ingest import FLAG_WORKLOAD_CLASSES
from repowise.core.evidence.profile_formats import profile_formats
from repowise.core.git_sha import is_full_sha


@dataclass(frozen=True)
class _Input:
    """The file as read: a v1 document (written or converted), or a profile run still to convert."""

    format: str
    text: str | None = None
    doc: Any = None
    run: Any = None


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
    results as the tool wrote them (Speedscope JSON, Go pprof, collapsed stacks,
    pytest-benchmark JSON), detected from its content.
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
    _check_options(workload, workload_class, environment, commit)

    repo_path = _resolve_repo(repo, notices)
    ensure_repowise_dir(repo_path)
    root = (checkout_root or repo_path).resolve()
    if not root.is_dir():
        raise click.BadParameter(f"{root} is not a directory.", param_hint="--checkout-root")

    from repowise.core.repo_config import load_repo_config

    caps = _caps(load_repo_config(repo_path), notices)
    read = _read_evidence(file, caps[0], fmt, input_format, root)
    _check_value_kind(read, value_kind)
    payload = run_async(
        _ingest(
            repo_path=repo_path,
            root=root,
            read=read,
            value_kind=value_kind,
            workload=workload.strip(),
            workload_class=workload_class,
            commit=_measured_commit(commit, read),
            dirty=dirty if dirty is not None or read.doc is None else read.doc.dirty,
            environment=environment,
            allow_partial=allow_partial,
            caps=caps[1],
            notices=notices,
        )
    )
    if fmt == "json":
        emit_json(payload)
    if not payload["stored"]:
        raise click.exceptions.Exit(1)


def _check_options(
    workload: str, workload_class: str, environment: str, commit: str | None
) -> None:
    """Refuse a usage error in the flags before anything is opened."""
    if workload_class == "production":
        raise click.BadParameter(
            "production is set only by a verified source (a request-log or "
            "statement ingest, a connector), never by a flag. Use entry_point for a "
            "profiled command or request replay, load_test for a load test.",
            param_hint="--class",
        )
    if workload_class not in FLAG_WORKLOAD_CLASSES:
        raise click.BadParameter(
            f"{workload_class!r} is not one of {', '.join(FLAG_WORKLOAD_CLASSES)}.",
            param_hint="--class",
        )
    if not workload.strip():
        raise click.BadParameter("name the workload that ran.", param_hint="--workload")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", environment):
        raise click.BadParameter(
            "one word of up to 32 letters, digits, '.', '_' or '-'.", param_hint="--environment"
        )
    if commit is not None and not is_full_sha(commit):
        raise click.BadParameter(
            "the full 40- or 64-character SHA, e.g. $(git rev-parse HEAD).", param_hint="--commit"
        )


def _read_evidence(
    file: Path, max_file_mb: float, fmt: str, input_format: str | None, root: Path
) -> _Input:
    """*file* read as v1 or as a registered format; refuses one too large or unreadable."""
    from repowise.core.evidence import EvidenceFormatError, parse_evidence
    from repowise.core.evidence.profile_convert import refuse_typescript

    try:
        size = file.stat().st_size
    except OSError as exc:
        raise click.BadParameter(f"cannot read {file}: {exc.strerror}", param_hint="FILE") from None
    if size > max_file_mb * 1024 * 1024:
        emit_refusal(
            "file_too_large",
            f"{file} is {size / 1048576:.1f} MB, over evidence.max_file_mb ({max_file_mb:g}).",
            fmt,
            remedy="Raise evidence.max_file_mb in .repowise/config.yaml, or ingest a smaller run.",
        )
    data = file.read_bytes()
    name = _input_format(input_format, data)
    try:
        if not data.strip():
            # Every reader would name a format the file lacks; the run that writes it never finished.
            raise EvidenceFormatError(
                f"{file} is empty: the profiled run probably crashed or stopped before writing it",
                "run the command again, check its output for a crash, then ingest the file it writes",
            )
        if name == EVIDENCE_FORMAT:
            text = data.decode("utf-8", errors="replace")
            return _Input(name, text=text, doc=parse_evidence(text))
        input_format = profile_formats.get(name)
        if input_format.to_v1 is not None:
            text = json.dumps(input_format.to_v1(data), sort_keys=True)
            return _Input(name, text=text, doc=parse_evidence(text))
        run = input_format.read(data)
        refuse_typescript(run, str(root))
        return _Input(name, run=run)
    except EvidenceFormatError as exc:
        emit_refusal("invalid_evidence", str(exc), fmt, remedy=exc.hint)
        raise  # unreachable: emit_refusal exits


def _input_format(given: str | None, data: bytes) -> str:
    """``--input-format`` checked against the registry, else the format sniffed.

    A file no profile format claims is read as v1, whose parser says what is
    wrong with it.
    """
    from repowise.core.evidence.profile_formats import SNIFF_BYTES, detect_profile_format

    names = [EVIDENCE_FORMAT, *(name for name, _ in profile_formats.all())]
    if given is not None:
        if given not in names:
            raise click.BadParameter(
                f"{given!r} is not one of {', '.join(names)}.", param_hint="--input-format"
            )
        return given
    head = data[:SNIFF_BYTES]
    if EVIDENCE_FORMAT.encode() in head:
        return EVIDENCE_FORMAT
    return detect_profile_format(head) or EVIDENCE_FORMAT


def _check_value_kind(read: _Input, value_kind: str | None) -> None:
    """A converted profile needs its clock; a v1 file's own must not be contradicted."""
    if read.doc is not None:
        if value_kind is not None and read.doc.value_kind is None:
            raise click.BadParameter(
                f"a {read.doc.kind} file records no clock; --value-kind names the clock "
                "of a profile only, so leave it out.",
                param_hint="--value-kind",
            )
        if value_kind is not None and read.doc.value_kind != value_kind:
            raise click.BadParameter(
                f"the file's value_kind is {read.doc.value_kind}; --value-kind only names "
                "the clock of a converted profile, so pass the same or none.",
                param_hint="--value-kind",
            )
        return
    if value_kind is None and read.run.value_kind is None:
        raise click.BadParameter(
            f"a {read.format} profile does not say which clock it sampled: pass "
            "--value-kind cpu for on-CPU sampling, wall for wall-clock time.",
            param_hint="--value-kind",
        )


def _measured_commit(commit: str | None, read: _Input) -> str:
    """The commit the run measured: ``--commit`` or the file's, which must agree.

    A converted file's commit is what its tool recorded, never taken on its
    own: ``--commit`` is required and the recorded one only cross-checks it.
    """
    in_file = read.doc.commit_sha if read.doc is not None else None
    converted = read.format != EVIDENCE_FORMAT
    if commit is not None and in_file is not None and commit != in_file:
        raise click.BadParameter(
            f"the {read.format} file records commit {in_file}, so it did not measure "
            f"{commit}; pass the commit the run measured."
            if converted
            else f"the file says it measured {in_file}; pass the same commit or none.",
            param_hint="--commit",
        )
    measured = commit or (None if converted else in_file)
    if measured is None:
        raise click.BadParameter(
            "say which commit was measured: --commit $(git rev-parse HEAD) at the time "
            "of the run" + ("." if converted else ", or commit_sha in the file."),
            param_hint="--commit",
        )
    return measured


def _caps(config: Any, notices: Any) -> tuple[float, Any]:
    """``evidence.max_file_mb`` and the store caps, from *config*."""
    from repowise.core.evidence.ingest import EvidenceCaps
    from repowise.core.settings import resolve

    def value(key: str) -> Any:
        resolved = resolve(f"evidence.{key}", config)
        if resolved.problem:
            notices.print(f"[yellow]Ignoring {escape(resolved.problem)}.[/yellow]")
        return resolved.value

    return value("max_file_mb"), EvidenceCaps(
        max_symbols_per_run=value("max_symbols_per_run"),
        max_rows_per_run=value("max_rows_per_run"),
        history_runs=value("history_runs"),
        max_runs=value("max_runs"),
    )


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
    root: Path,
    read: _Input,
    value_kind: str | None,
    workload: str,
    workload_class: str,
    commit: str,
    dirty: bool | None,
    environment: str,
    allow_partial: bool,
    caps: Any,
    notices: Any,
) -> dict[str, Any]:
    from repowise.core.analysis.health.coverage.discovery import mostly_unmapped
    from repowise.core.evidence import parse_evidence
    from repowise.core.evidence.hotness import refresh_symbol_hotness
    from repowise.core.evidence.ingest import (
        RunProvenance,
        cap_rows,
        entry_paths,
        join_entries,
        load_origins,
        load_symbols,
        run_fields,
        run_key,
        stamp_bodies,
        unlisted_count,
    )
    from repowise.core.persistence.crud import save_evidence_run

    target = await open_ingest_target(session, repo_path, what="evidence", out=notices)
    if target is None:
        return {"stored": False, "reason": "no_index"}
    index_commit = getattr(target.repo, "head_commit", None)

    changed, dirty = _tree_state(root, commit, dirty)

    text, doc, conversion = read.text, read.doc, None
    if read.run is not None:
        conversion = await _convert(session, target.repo.id, read.run, root, changed, value_kind)
        text = json.dumps(conversion.document, sort_keys=True)
        doc = parse_evidence(text)
        _report_conversion(notices, read.format, conversion)

    paths = entry_paths(doc)
    origins = await load_origins(session, target.repo.id, paths)
    joined = join_entries(
        doc,
        await load_symbols(session, target.repo.id, paths),
        origins,
        changed or frozenset(),
    )
    # A benchmark or test keeps its row without its function, so only the
    # kinds that need the join can be mostly unmapped.
    partial = doc.kind in ("profile", "query_count") and mostly_unmapped(
        joined.matched, len(doc.entries)
    )
    provenance = RunProvenance(
        workload_name=workload,
        workload_class=workload_class,
        commit_sha=commit,
        checkout_root=str(root),
        dirty=dirty,
        environment=environment,
    )
    summary = _summary(doc, provenance, joined, partial, index_commit, target.head_sha)
    summary["input_format"] = read.format
    if conversion is not None:
        summary["conversion"] = conversion.summary()
    refused = _refusal(summary, partial and not allow_partial, bool(joined.rows), notices)
    if refused is not None:
        return refused

    rows, truncated = cap_rows(doc.kind, joined.rows, caps)
    if doc.kind == "profile":
        await stamp_bodies(
            session,
            target.repo.id,
            rows,
            index_at_measured_commit=index_commit is not None and index_commit == commit,
        )
    write = await save_evidence_run(
        session,
        target.repo.id,
        run_fields(
            doc,
            provenance,
            joined,
            key=run_key(text, provenance, doc.kind, index_commit),
            truncated=truncated,
            mapping_partial=partial,
            unlisted=unlisted_count(joined.rows, origins) if doc.kind == "profile" else None,
        ),
        rows,
        history_runs=caps.history_runs,
        max_runs=caps.max_runs,
    )
    if doc.kind == "profile":
        # Profiles are what the read model is built from, and the save may
        # have pruned an older one. Best effort: the run stays stored.
        await refresh_symbol_hotness(
            session, target.repo.id, head_commit=index_commit, repo_path=repo_path
        )

    _report_stored(notices, summary, write.restamped, len(rows), truncated, index_commit)
    return {
        **summary,
        "stored": True,
        "run_id": write.run_id,
        "restamped": write.restamped,
        "rows_written": write.rows_written,
        "truncated": truncated,
        "runs_pruned": write.runs_pruned,
    }


async def _convert(
    session: Any,
    repo_id: str,
    run: Any,
    root: Path,
    changed: frozenset[str] | None,
    value_kind: str | None,
) -> Any:
    """*run*'s frames joined to the index at *root*, as a v1 document (or a refusal)."""
    from repowise.core.evidence.profile_convert import convert_profile
    from repowise.core.frame_resolver import checkout_reader, frame_paths, load_frame_symbols

    symbols, origins = await load_frame_symbols(
        session,
        repo_id,
        frame_paths(run.frames, root=str(root)),
        fold_case=sys.platform == "win32",
    )
    return convert_profile(
        run,
        root=str(root),
        symbols_by_file=symbols,
        dirty_paths=changed or (),
        origins=origins,
        read_lines=checkout_reader(root),
        value_kind=value_kind,
    )


def _report_conversion(notices: Any, name: str, conversion: Any) -> None:
    """Where the profile's time went and how its frames joined, never silent."""
    from repowise.core.evidence.status import split_words

    summary = conversion.summary()
    rate = summary["skip_rate"]
    split = split_words(summary) or "repo code itself unknown"
    skipped = "no repo frames" if rate is None else f"{rate:.0%} of repo frame time skipped"
    reasons = ", ".join(
        f"{reason} {part:.0%}" for reason, part in summary["skipped_share_by_reason"].items()
    )
    notices.print(
        f"Converted {escape(name)}: {split} of "
        f"{_total(conversion.total)} {escape(conversion.unit)}; {skipped}"
        + (f" ({escape(reasons)})." if reasons else ".")
    )


def _tree_state(root: Path, commit: str, dirty: bool | None) -> tuple[Any, bool | None]:
    """Files that differ from *commit*, and *dirty* filled in from them.

    Files that differ from the commit are skipped: their lines are not the
    ones measured. Known only when the commit is the checkout's HEAD and git
    can say; otherwise the tree's state stays unknown.
    """
    from repowise.core.analysis.health.coverage import live_head_commit
    from repowise.core.evidence.ingest import uncommitted_paths

    at_head = live_head_commit(root, None) == commit
    changed = uncommitted_paths(root) if at_head else None
    if dirty is None and changed is not None:
        dirty = bool(changed)
    return changed, dirty


def _summary(
    doc: Any,
    provenance: Any,
    joined: Any,
    partial: bool,
    index_commit: str | None,
    head_sha: str | None,
) -> dict[str, Any]:
    """What the run held and how it mapped, stored or not."""
    from collections import Counter

    commit = provenance.commit_sha
    return {
        "kind": doc.kind,
        "workload": provenance.workload_name,
        "class": provenance.workload_class,
        "commit_sha": commit,
        "index_commit": index_commit,
        "stale": head_sha is not None and commit != head_sha,
        "dirty": provenance.dirty,
        "entries": len(doc.entries),
        "matched": joined.matched,
        "skipped": dict(sorted((Counter(doc.skipped) + joined.skipped).items())),
        "unlinked": dict(sorted(joined.unlinked.items())),
        "mapping_partial": partial,
        "unmatched_sample": joined.unmatched_sample,
    }


def _refusal(
    summary: dict[str, Any], refuse_partial_run: bool, any_rows: bool, notices: Any
) -> dict[str, Any] | None:
    """The payload for a run that is not stored, or None to store it."""
    if refuse_partial_run:
        refuse_partial(
            "evidence file",
            "write each entry's path relative to the repository root and its "
            "function as the qualified name repowise indexed",
            out=notices,
        )
        return {**summary, "stored": False, "reason": "mostly_unmapped"}
    if not any_rows:
        notices.print("[red]No entry matched an indexed function, so nothing was stored.[/red]")
        return {**summary, "stored": False, "reason": "nothing_matched"}
    return None


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
        f"{(run['commit_sha'] or 'unknown')[:12]}; {stored}{explained}."
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
