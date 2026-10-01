"""``repowise security``: security signal scanning and its CI gate.

Subcommands:
  scan --history [--since <rev>] [--to <rev>] [--format json]
      Walk the entire git history of the repo (not just the working tree) with
      the same pattern registry the indexer uses, and persist any secrets or
      risky patterns into the shared ``security_findings`` table — tagged with
      the commit that introduced them. Re-runs are idempotent.
  check [REVSPEC | --staged] [--fail-on high|med|low] [--baseline FILE] [--format ...]
      Gate a change on the findings it adds: its changed lines at the head,
      plus secrets committed and removed inside it. Git only, no index and no
      database; exit codes are the shared CI ones (:mod:`repowise.cli.ci`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.markup import escape

from repowise.cli.ci import (
    CI_FORMATS,
    EXIT_GATE_FAILED,
    CannotEvaluateError,
    append_step_summary,
    cannot_evaluate,
    change_lines,
    ci_notices,
    repo_root,
)
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output,
)
from repowise.cli.output import emit_json, format_option, notice_console

if TYPE_CHECKING:
    from repowise.core.analysis.security_gate import ChangeScan, CustomPattern, GateResult

_CHECK_FORMATS = (*CI_FORMATS, "sarif", "gitlab")


@click.group("security")
def security_command() -> None:
    """Security signal scanning (working tree + full git history) and its CI gate."""


@security_command.command("scan")
@click.option(
    "--history",
    is_flag=True,
    default=False,
    help="Scan the full git history, not just the current working tree.",
)
@click.option(
    "--since",
    default=None,
    help="Lower git revision bound (exclusive). Defaults to all history.",
)
@click.option(
    "--to",
    default=None,
    help="Upper git revision bound (inclusive). Defaults to all history/HEAD.",
)
@click.option(
    "--path",
    "repo",
    default=None,
    help="Repo path (defaults to cwd / workspace primary).",
)
@click.option(
    "--all-patterns",
    is_flag=True,
    default=False,
    help="History mode: also report code-smell patterns (eval, os.system, "
    "weak hashes, ...). By default history mode reports only leaked-secret "
    "patterns (hardcoded credentials and known vendor key/token/PEM shapes) "
    "to avoid noise.",
)
@format_option(help="Output format. ``json`` is the machine-readable summary.")
@click.option(
    "--output",
    "legacy_output",
    default=None,
    type=click.Choice(["table", "json"]),
    hidden=True,
    help="Deprecated alias for --format.",
)
def security_scan(
    history: bool,
    since: str | None,
    to: str | None,
    all_patterns: bool,
    repo: str | None,
    fmt: str,
    legacy_output: str | None,
) -> None:
    """Scan for security signals and persist findings to the local store.

    Without ``--history`` this is a no-op stub (working-tree scanning already
    happens during ``repowise init`` / ``repowise update``). With ``--history``
    it walks every tracked revision and surfaces leaked secrets / risky
    patterns that were later removed — something the working-tree scan cannot
    see.
    """
    # ``--output`` shipped before ``--format`` was the convention. An explicit
    # ``--output`` still wins so existing scripts keep their behaviour; with it
    # unset (the default) ``--format`` decides.
    output_format = legacy_output or fmt

    if not history:
        # Notice on stderr and a payload on stdout, rather than a rich notice
        # and nothing: json mode has to leave stdout parseable even on the
        # path where the command declines to do any work.
        notice_console(output_format).print(
            "[yellow]Working-tree scanning runs automatically during "
            "`repowise init`/`repowise update`.[/yellow]\n"
            "Pass [cyan]--history[/cyan] to scan the full git history for secrets "
            "and risky patterns (including ones deleted in later commits)."
        )
        if output_format == "json":
            emit_json({"scanned": False, "reason": "history-mode-not-requested"})
        return

    if output_format == "json":
        silence_logs_for_machine_output()

    from pathlib import Path

    from repowise.core.analysis.history_scan import HistorySecurityScanner
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        upsert_repository,
    )

    target = resolve_command_target(path=repo)
    target.notice(notice_console(output_format), command="security scan --history")

    if target.is_workspace:
        primary = target.primary_path()
        if primary is None:
            raise click.ClickException("Workspace has no primary repo configured.")
        repo_path = primary
    else:
        assert target.repo_path is not None
        repo_path = target.repo_path

    ensure_repowise_dir(repo_path)

    async def _do() -> dict:
        engine = create_engine(get_db_url_for_repo(repo_path))
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            row = await get_repository_by_path(session, str(repo_path))
            if row is None:
                row = await upsert_repository(
                    session,
                    name=repo_path.name,
                    local_path=str(repo_path),
                )
            scanner = HistorySecurityScanner(session, row.id)
            summary = await scanner.scan_history(
                Path(repo_path),
                since=since,
                to=to,
                secrets_only=not all_patterns,
                progress=lambda msg: (
                    console.print(f"[dim]{msg}[/dim]") if output_format != "json" else None
                ),
            )
            await session.commit()
            return {
                "commits_scanned": summary.commits_scanned,
                "blobs_scanned": summary.blobs_scanned,
                "files_scanned": summary.files_scanned,
                "findings_inserted": summary.findings_inserted,
                "by_severity": summary.by_severity,
                "by_kind": summary.by_kind,
            }

    result = run_async(_do())

    if output_format == "json":
        emit_json(result)
        return

    console.print(f"[bold]repowise security scan --history[/bold] — {repo_path}")
    console.print(f"  Commits scanned: {result['commits_scanned']}")
    console.print(f"  Blobs scanned:   {result['blobs_scanned']}")
    console.print(f"  Files scanned:   {result['files_scanned']}")
    console.print(f"  Findings stored: {result['findings_inserted']}")
    if result["by_severity"]:
        sev = ", ".join(f"{k}={v}" for k, v in sorted(result["by_severity"].items()))
        console.print(f"  By severity:     {sev}")
    if result["by_kind"]:
        kinds = ", ".join(f"{k}={v}" for k, v in sorted(result["by_kind"].items()))
        console.print(f"  By kind:         {kinds}")
    console.print(
        "\nFindings are written to the security_findings table and show up in "
        "`repowise server`'s security API and UI. Re-running is idempotent."
    )


@security_command.command("check")
@click.argument("revspec", required=False, default=None)
@click.option(
    "--fail-on",
    type=click.Choice(["high", "med", "low"]),
    default="high",
    show_default=True,
    help="Exit 1 on a finding of this severity or above.",
)
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Accept the findings recorded in this file; only new ones fail.",
)
@click.option(
    "--write-baseline",
    "write_baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Add this change's findings to this file, keeping its entries and those of "
    "--baseline, and exit 0.",
)
@click.option(
    "--path",
    "repo",
    default=None,
    help="A path inside the repository (defaults to cwd).",
)
@click.option(
    "--staged",
    is_flag=True,
    default=False,
    help="Check the staged changes (what the next commit records), read from the index; "
    "for a pre-commit hook. Takes no REVSPEC.",
)
@format_option(
    choices=_CHECK_FORMATS,
    help="Output format. ``github`` writes annotations to stdout and the "
    "markdown summary to $GITHUB_STEP_SUMMARY when set; ``sarif`` is for "
    "code-scanning upload; ``gitlab`` is a GitLab Code Quality report.",
)
def security_check(
    revspec: str | None,
    fail_on: str,
    baseline_path: Path | None,
    write_baseline_path: Path | None,
    repo: str | None,
    staged: bool,
    fmt: str,
) -> None:
    """Gate a change on the security findings it adds (no index needed, built for CI).

    Scans the files the change touched, as its head has them, and keeps the
    findings on changed lines. Secret kinds are also checked in every commit
    of the change, so a key committed and then deleted inside it still fails:
    it stays in the history. Snippets are masked in every format.

    REVSPEC is the change: ``origin/main...HEAD`` (the pull-request view),
    ``base..head``, or one commit. Without it, the base comes from the CI's
    pull-request variables, else the remote's default branch. ``--staged``
    checks what the next commit would record instead.

    A ``repowise-security-ignore`` comment on a finding's line silences it
    (``repowise-security-ignore: kind, kind`` silences only those kinds); it is
    still counted and listed. ``security.patterns`` in .repowise/config.yaml
    adds secret shapes of your own.

    Examples:

        repowise security check origin/main...HEAD
        repowise security check --staged
        repowise security check --format github --baseline .security-baseline.json
        repowise security check --format sarif > security.sarif
        repowise security check --format gitlab > gl-code-quality-security.json
    """
    if staged and revspec:
        raise click.UsageError("--staged checks the staged changes; drop REVSPEC.")
    if fmt != "table":
        silence_logs_for_machine_output()
    try:
        _check(
            _CheckOptions(revspec, fail_on, baseline_path, write_baseline_path, repo, staged, fmt)
        )
    except (click.exceptions.Exit, click.ClickException, click.Abort):
        raise
    except Exception as exc:
        # A crash must never read as a failed gate: a pre-commit hook blocks on 1 only.
        cannot_evaluate(fmt, "internal_error", f"security check failed unexpectedly: {exc!r}")


@dataclass(frozen=True)
class _CheckOptions:
    """The ``security check`` flags, as the command received them."""

    revspec: str | None
    fail_on: str
    baseline_path: Path | None
    write_baseline_path: Path | None
    repo: str | None
    staged: bool
    fmt: str


@dataclass(frozen=True)
class _Verdict:
    """A finished check, ready to render: *accepted* is the whole baseline."""

    scan: ChangeScan
    gate: GateResult
    label: str
    accepted: frozenset[str]
    patterns: tuple[CustomPattern, ...]


def _check(opts: _CheckOptions) -> None:
    from repowise.core.analysis.security_gate import evaluate

    fmt = opts.fmt
    try:
        root = repo_root(opts.repo)
        patterns = _custom_patterns(root)
        accepted = _baseline_entries(opts.baseline_path) if opts.baseline_path else None
        changed, label = change_lines(str(root), opts.revspec, staged=opts.staged)
        scan = _scan_change(root, changed, label, patterns=patterns, staged=opts.staged)
        if opts.write_baseline_path is not None:
            _record_baseline(opts.write_baseline_path, scan, fmt, also_keep=accepted or [])
            return
    except CannotEvaluateError as exc:
        cannot_evaluate(fmt, exc.code, str(exc))

    baseline = None
    if accepted is not None:
        baseline = frozenset(e["fingerprint"] for e in accepted)
    gate = evaluate(
        scan.findings, fail_on=opts.fail_on, baseline=baseline, suppressed=scan.suppressed
    )
    _EMITTERS.get(fmt, _print_check)(_Verdict(scan, gate, label, baseline or frozenset(), patterns))
    if not gate.passed:
        raise click.exceptions.Exit(EXIT_GATE_FAILED)


def _custom_patterns(root: Path) -> tuple[CustomPattern, ...]:
    """``security.patterns`` from the repo config; any invalid entry stops the check."""
    from repowise.core.analysis.security_gate import custom_patterns
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        config = load_repo_config(root)
    except (RepoConfigError, OSError) as exc:
        raise CannotEvaluateError("config_invalid", str(exc)) from exc
    patterns, errors = custom_patterns(config)
    if errors:
        raise CannotEvaluateError("config_invalid", " ".join(errors))
    return patterns


def _scan_change(
    root: Path,
    changed: dict[str, set[int]],
    label: str,
    *,
    patterns: tuple[CustomPattern, ...],
    staged: bool,
) -> ChangeScan:
    """Scan the change, turning a git failure into a cannot-evaluate."""
    import subprocess

    from repowise.core.analysis.security_gate import (
        MissingObjectError,
        ShallowHistoryError,
        scan_change,
    )

    try:
        return scan_change(str(root), changed, label, patterns=patterns, staged=staged)
    except ShallowHistoryError as exc:
        raise CannotEvaluateError("history_shallow", str(exc)) from exc
    except MissingObjectError as exc:
        raise CannotEvaluateError("object_missing", str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = stderr.strip().splitlines()
        raise CannotEvaluateError(
            "git_failed", f"git failed: {detail[-1] if detail else f'exit {exc.returncode}'}"
        ) from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise CannotEvaluateError("git_failed", f"Could not run git: {exc}") from exc


def _baseline_entries(path: Path) -> list[dict]:
    from repowise.core.ci.baseline import BaselineError, read_entries

    try:
        return read_entries(path)
    except BaselineError as exc:
        raise CannotEvaluateError("baseline_unreadable", str(exc)) from exc


def _record_baseline(path: Path, scan: ChangeScan, fmt: str, *, also_keep: list[dict]) -> None:
    """Add the change's findings to *path*, keeping its entries and *also_keep* (``--baseline``)."""
    from repowise.core.analysis.security_gate import write_baseline

    keep = [*(_baseline_entries(path) if path.exists() else []), *also_keep]
    try:
        written = write_baseline(path, scan.findings, keep=keep)
    except OSError as exc:
        raise CannotEvaluateError(
            "baseline_unwritable", f"cannot write baseline {path}: {exc}"
        ) from exc
    if fmt == "json":
        emit_json({"baseline": str(path), "recorded": len(scan.findings), "entries": written})
        return
    ci_notices(fmt).print(
        f"Recorded {len(scan.findings)} finding(s); {escape(str(path))} now holds "
        f"{written} entr{'y' if written == 1 else 'ies'}."
    )


def _emit_json(v: _Verdict) -> None:
    from repowise.core.analysis.security_gate import DETECTION_BASIS

    emit_json(
        {
            "revspec": v.label,
            "files_scanned": v.scan.files_scanned,
            "commits_scanned": v.scan.commits_scanned,
            "long_lines_skipped": v.scan.long_lines_skipped,
            "findings_basis": DETECTION_BASIS,
            "findings": v.scan.findings,
            "gate": v.gate.to_dict(),
        }
    )


def _emit_sarif(v: _Verdict) -> None:
    from repowise.cli import __version__
    from repowise.core.analysis.security_gate import SarifExtras, render_sarif

    extras = SarifExtras(v.gate.suppressed, v.patterns, v.scan.long_lines_skipped)
    emit_json(
        render_sarif(
            v.scan.findings,
            tool_version=__version__,
            fail_on=v.gate.fail_on,
            accepted=v.accepted,
            extras=extras,
        )
    )


def _emit_gitlab(v: _Verdict) -> None:
    from repowise.core.analysis.security_gate import render_gitlab

    emit_json(render_gitlab(v.scan.findings, fail_on=v.gate.fail_on, accepted=v.accepted))
    # Code Quality has no suppression field or run properties: counts go to the job log.
    if v.gate.suppressed:
        ci_notices("gitlab").print(
            f"{len(v.gate.suppressed)} finding(s) suppressed inline "
            "(repowise-security-ignore), left out of the report."
        )
    if v.scan.long_lines_skipped:
        ci_notices("gitlab").print(_long_lines_note(v.scan.long_lines_skipped))


def _markdown(v: _Verdict) -> str:
    from repowise.core.analysis.security_gate import render_markdown

    return render_markdown(
        v.gate,
        label=v.label,
        files_scanned=v.scan.files_scanned,
        commits_scanned=v.scan.commits_scanned,
        long_lines_skipped=v.scan.long_lines_skipped,
    )


def _emit_markdown(v: _Verdict) -> None:
    click.echo(_markdown(v), nl=False)


def _emit_github(v: _Verdict) -> None:
    from repowise.core.analysis.security_gate import github_annotations

    for line in github_annotations(v.scan.findings, v.gate):
        click.echo(line)
    append_step_summary(_markdown(v))


_SEVERITY_COLOUR = {"high": "red", "med": "yellow", "low": "dim"}


def _print_check(v: _Verdict) -> None:
    from repowise.core.analysis.security_gate import DETECTION_BASIS

    console.print(
        f"[bold]{escape(v.label)}[/bold]: {v.scan.files_scanned} changed file(s) and "
        f"{v.scan.commits_scanned} commit(s) scanned"
    )
    _print_findings(v)
    _print_suppressed(v.gate)
    if v.scan.long_lines_skipped:
        console.print(f"[yellow]{_long_lines_note(v.scan.long_lines_skipped)}[/yellow]")
    _print_gate(v.gate)
    console.print(f"[dim]{escape(DETECTION_BASIS)}[/dim]")


def _print_findings(v: _Verdict) -> None:
    """The findings the baseline did not accept, as a table."""
    from rich.table import Table

    from repowise.core.analysis.security_gate import location

    accepted = {f["fingerprint"] for f in v.gate.baselined}
    rows = [f for f in v.scan.findings if f["fingerprint"] not in accepted]
    if not rows:
        return
    table = Table(show_edge=False, pad_edge=False)
    for column in ("Severity", "Kind", "Where", "Matched (secrets masked)"):
        table.add_column(column)
    for f in rows:
        colour = _SEVERITY_COLOUR[f["severity"]]
        table.add_row(
            f"[{colour}]{f['severity']}[/]",
            escape(f["kind"]),
            escape(location(f)),
            escape(f["snippet"]),
        )
    console.print(table)


def _print_suppressed(gate: GateResult) -> None:
    from repowise.core.analysis.security_gate import location

    if not gate.suppressed:
        return
    console.print(
        f"[dim]{len(gate.suppressed)} finding(s) suppressed inline "
        "(repowise-security-ignore):[/dim]"
    )
    for f in gate.suppressed:
        console.print(f"[dim]  {escape(location(f))} {escape(f['kind'])} ({f['severity']})[/dim]")


def _print_gate(gate: GateResult) -> None:
    if gate.passed:
        console.print(f"[green]Gate passed[/green] [dim](fails on {gate.fail_on} or above)[/dim]")
    else:
        note = f", {len(gate.baselined)} accepted by baseline" if gate.baselined else ""
        console.print(
            f"[red]Gate failed:[/red] {len(gate.failing)} finding(s) at {gate.fail_on} "
            f"or above{note}"
        )
    if any(f["commit"] for f in gate.failing):
        console.print(
            "[yellow]A row naming a commit is a secret committed inside this change; "
            "deleting it does not remove it from the history. Rotate it.[/yellow]"
        )


#: Writers per ``--format``; ``table`` (the default) is :func:`_print_check`.
_EMITTERS = {
    "json": _emit_json,
    "sarif": _emit_sarif,
    "gitlab": _emit_gitlab,
    "markdown": _emit_markdown,
    "github": _emit_github,
}


def _long_lines_note(n: int) -> str:
    from repowise.core.analysis.security_gate import MAX_CUSTOM_LINE_LENGTH

    return (
        f"{n} line(s) over {MAX_CUSTOM_LINE_LENGTH} characters not matched "
        "against custom patterns."
    )
