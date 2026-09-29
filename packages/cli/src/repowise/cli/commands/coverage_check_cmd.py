"""``repowise coverage check``: gate a change on its patch coverage, in CI.

Needs git and a coverage report, nothing else: no index, no API key. Report
paths are resolved against ``git ls-files``, so a file added by the change
resolves too (an index cached from the base branch would not know it). With no
report on disk it falls back to the coverage an index stores, but gates on it
only when that coverage was measured at the change's head and carries
executable-line data; otherwise it cannot evaluate.

Exit codes and output channels are the shared CI ones (:mod:`repowise.cli.ci`):
0 when the gate passes or there is nothing to judge, 1 when patch coverage is
below ``--fail-under``, a path-scoped gate (``coverage.gates``) that is not
informational fails, or the risky files' is below ``--fail-under-risky``, 2
when the check could not run (no report, a ``--report`` matching no file,
unreadable report, unknown revision, missing history, bad config, risk
unreadable or a shallow clone under ``--fail-under-risky``). A change under
``--min-coverable-lines`` is reported against the threshold but exits 0.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
from repowise.cli.helpers import console, repo_index_session, run_async
from repowise.cli.output import emit_json, format_option
from repowise.core.analysis.health.coverage import PARSERS as COVERAGE_PARSERS
from repowise.core.ci.markdown import plural
from repowise.core.persistence.database import has_db_store


@click.command("check")
@click.argument("revspec", required=False, default=None)
@click.option(
    "--report",
    "reports",
    multiple=True,
    help="Coverage report to read: a path or a glob such as artifacts/**/lcov.info, "
    "relative to cwd (repeatable; merged hit-wins). PATH=PREFIX prepends PREFIX to "
    "that report's paths. Defaults to coverage.paths in .repowise/config.yaml, "
    "else discovery.",
)
@click.option(
    "--report-format",
    type=click.Choice(list(COVERAGE_PARSERS)),
    default=None,
    help="Force a report parser instead of detecting it from the content.",
)
@click.option(
    "--fail-under",
    type=click.FloatRange(0, 100),
    default=None,
    help="Exit 1 when patch coverage is below this percentage. "
    "Defaults to coverage.fail_under in .repowise/config.yaml.",
)
@click.option(
    "--min-coverable-lines",
    type=click.IntRange(min=0),
    default=None,
    help="Small-change tolerance: a change with fewer changed executable lines "
    "than this is reported but never fails the gate. "
    "Defaults to coverage.min_coverable_lines in .repowise/config.yaml.",
)
@click.option(
    "--fail-under-risky",
    type=click.FloatRange(0, 100),
    default=None,
    help="Exit 1 when patch coverage of the risky files is below this percentage. "
    "Risky: hotspots or bug magnets from the index; on git alone, the top quartile "
    "of files with bug-fix history. No risky file changed: not applied. "
    "Defaults to coverage.fail_under_risky in .repowise/config.yaml.",
)
@click.option(
    "--path",
    "repo",
    default=None,
    help="A path inside the repository (defaults to cwd). Config is read at the repo root.",
)
@format_option(
    choices=CI_FORMATS,
    help="Output format. ``github`` writes annotations to stdout and the "
    "markdown summary to $GITHUB_STEP_SUMMARY when set.",
)
def coverage_check(
    revspec: str | None,
    reports: tuple[str, ...],
    report_format: str | None,
    fail_under: float | None,
    min_coverable_lines: int | None,
    fail_under_risky: float | None,
    repo: str | None,
    fmt: str,
) -> None:
    """Gate a change on its patch coverage (no index needed, built for CI).

    Patch coverage is the share of the change's executable lines the tests
    ran. REVSPEC is the change: ``origin/main...HEAD`` (what the branch did
    since it forked, the pull-request view), ``base..head``, or one commit.
    Without it, the base comes from the CI's pull-request variables (GitHub,
    GitLab, Jenkins, Bitbucket), else the remote's default branch.

    Path-scoped gates in coverage.gates (.repowise/config.yaml) are judged
    too; one that fails and is not informational fails the check. See
    ``repowise coverage suggest-gates``.

    Examples:

        repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
        repowise coverage check --report coverage.out --format github
        repowise coverage check --report 'artifacts/**/lcov.info' --min-coverable-lines 5
        repowise coverage check --report web/coverage/lcov.info=web
        repowise coverage check HEAD --format json

    Each changed file carries its risk (git bug-fix history, plus hotspot,
    bug-magnet and dependent counts when an index exists), and the rows read
    riskiest first. ``--fail-under-risky`` gates the risky files alone. With an
    index, each uncovered range also names the test file to extend.
    """
    notices = ci_notices(fmt)
    try:
        pc = _evaluate(
            revspec, reports, report_format, fail_under, min_coverable_lines, repo, notices
        )
        pc = _with_risk(repo, pc, fail_under_risky)
        pc = _with_hints(repo, pc)
    except CannotEvaluateError as exc:
        cannot_evaluate(fmt, exc.code, str(exc))
    _emit(pc, fmt)
    if pc.gate == "fail":
        raise click.exceptions.Exit(EXIT_GATE_FAILED)


def _evaluate(revspec, reports, report_format, fail_under, min_coverable_lines, repo, notices):
    from repowise.core.analysis.patch_coverage import patch_coverage_from_resolved

    root = repo_root(repo)
    cfg = _coverage_config(
        root,
        validate_threshold=fail_under is None,
        validate_min_lines=min_coverable_lines is None,
    )
    # A flag overrides its config key.
    threshold = _first_set(fail_under, cfg.fail_under)
    min_coverable_lines = _first_set(min_coverable_lines, cfg.min_coverable_lines)
    report_prefixes = _cli_reports(reports) if reports else cfg.reports(root)
    report_paths = list(report_prefixes)
    if not report_paths and not has_db_store(root):
        raise CannotEvaluateError("no_report", _NO_REPORT)
    changed, label = change_lines(str(root), revspec)
    if not report_paths:
        # No report on disk: the index's stored coverage answers, when it can.
        if notices is console:
            notices.print("[dim]Reading the coverage stored in the index[/dim]")
        return _gateable(
            run_async(_stored(root, changed, label, threshold, min_coverable_lines, cfg))
        )
    if notices is console:
        # Machine formats carry the list in ``scope.reports`` instead.
        notices.print(f"[dim]Reading {', '.join(escape(str(p)) for p in report_paths)}[/dim]")

    return patch_coverage_from_resolved(
        changed,
        _resolve_reports(root, cfg, report_prefixes, report_format, notices),
        threshold=threshold,
        label=label,
        reports=[str(p) for p in report_paths],
        min_coverable_lines=min_coverable_lines,
        ignore=cfg.ignore,
        gates=cfg.gates,
    )


def _first_set(flag, configured):
    """The flag's value, else the config's: ``0`` is a value, not unset."""
    return configured if flag is None else flag


def _cli_reports(args: tuple[str, ...]) -> dict[Path, str | None]:
    """``--report`` values: each a path or glob relative to cwd, optionally ``=PREFIX``.

    The whole argument is expanded first, so a path or glob holding ``=``
    (``artifacts/shard=1/*.info``) stays one; only when it matches nothing is
    it split on the last ``=``, and the prefix applies to every file the left
    side matches. One that matches no file cannot be evaluated: a report
    missing from a CI job is a broken setup, not a pass.
    """
    from repowise.core.analysis.health.coverage import expand_report_patterns

    cwd = Path()  # relative, so reports read as the caller named them
    out: dict[Path, str | None] = {}
    for arg in args:
        pattern, prefix = arg, None
        matches = expand_report_patterns([arg], cwd)
        if not matches and "=" in arg:
            pattern, _, prefix = arg.rpartition("=")
            matches = expand_report_patterns([pattern], cwd)
        if not matches:
            raise CannotEvaluateError(
                "report_not_found", f"--report {arg}: no coverage report matches {pattern}."
            )
        for path in matches:
            out.setdefault(path, prefix or None)
    return out


_NO_REPORT = (
    "No coverage report found. Pass one with --report (lcov.info, coverage.xml, "
    "coverage.out, jacoco.xml, ...), set coverage.paths in .repowise/config.yaml, "
    "or ingest one with `repowise coverage add`."
)


def _gateable(pc):
    """Stored coverage the gate can trust, or why it cannot evaluate.

    Coverage from another commit describes other code, and coverage stored
    before executable lines were kept cannot tell an uncovered line from a
    comment: either would pass a gate that measured nothing.
    """
    if pc is None:
        raise CannotEvaluateError("no_report", _NO_REPORT)
    if pc.scope.freshness != "current":
        at = f" at {pc.scope.measured_commit[:7]}" if pc.scope.measured_commit else ""
        raise CannotEvaluateError(
            "coverage_stale",
            f"The stored coverage was measured{at}, not at this change's head. "
            "Pass a fresh report with --report, or re-run `repowise coverage add`.",
        )
    measurable = pc.with_status("measured") + pc.with_status("no_coverable_changes")
    if pc.with_status("no_line_data") and not measurable:
        raise CannotEvaluateError(
            "no_line_data",
            "The stored coverage predates executable-line data. Re-run "
            "`repowise coverage add` with the report, or pass it with --report.",
        )
    return pc


async def _stored(
    root: Path, changed, label: str, threshold: float | None, min_lines: int | None, cfg
):
    """Patch coverage from the index's stored coverage, or ``None`` without one."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core import git_refs
    from repowise.core.analysis.change_risk.features import revspec_head
    from repowise.core.analysis.patch_coverage import stored_patch_coverage

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        try:
            return await stored_patch_coverage(
                session,
                repo_id,
                changed,
                label=label,
                head_commit=git_refs.resolve(str(root), revspec_head(label)),
                threshold=threshold,
                # The flag overrides its config key, as for a report.
                config=replace(cfg, min_coverable_lines=min_lines),
            )
        except SQLAlchemyError as exc:
            raise CannotEvaluateError(
                "index_unreadable", f"Could not read the index's coverage: {exc}"
            ) from exc


def _with_risk(repo, pc, fail_under_risky):
    """*pc* with each row's risk and the risky-file gate.

    Git answers alone; an index, when one opens, adds hotspot, bug-magnet and
    dependent counts. A missing or unreadable index never fails the check. A
    risky-file gate that cannot trust the risk (a shallow clone's truncated
    history, or a measured row with no risk at all) cannot evaluate, unless
    the flat or a path-scoped gate already failed: that verdict stands.
    """
    from repowise.core.analysis.patch_coverage import assess_risks, attach_risk

    root = repo_root(repo)
    threshold = _risky_threshold(root, fail_under_risky)
    paths = [f.file_path for f in pc.files]
    git, index = _read_risk(root, pc.scope.label or None, paths)
    pc = attach_risk(pc, assess_risks(paths, git, index), risky_threshold=threshold)
    # A failure the flat or a path-scoped gate already found stands and is reported.
    if threshold is not None and pc.flat_gate != "fail" and not pc.failing_path_gates:
        _require_trusted_risk(root, pc)
    return pc


def _risky_threshold(root: Path, flag: float | None) -> float | None:
    """``--fail-under-risky``, else ``coverage.fail_under_risky`` (validated)."""
    if flag is not None:
        return flag
    cfg = _coverage_config(
        root, validate_threshold=False, validate_min_lines=False, validate_risky=True
    )
    return cfg.fail_under_risky


def _read_risk(root: Path, revspec: str | None, paths: list[str]):
    """``(git fix history or None, index facts)``; git first, never under an open store."""
    from repowise.core.analysis.patch_coverage import read_git_fix_history

    git = read_git_fix_history(str(root), revspec)
    index = run_async(_index_facts(root, paths)) if paths and has_db_store(root) else {}
    return git, index


def _require_trusted_risk(root: Path, pc) -> None:
    """Raise when the risky-file gate cannot trust the risk it would read."""
    from repowise.core import git_refs
    from repowise.core.analysis.patch_coverage import risk_unreadable

    if git_refs.is_shallow(str(root)):
        # Fix pressure from a truncated history undercounts every file, and the
        # top-quartile rule would call risky files calm.
        raise CannotEvaluateError(
            "history_shallow",
            "The risky-file gate reads bug-fix history, and this clone is shallow. "
            "Fetch full history (fetch-depth: 0, or git fetch --unshallow).",
        )
    if unread := risk_unreadable(pc):
        raise CannotEvaluateError(
            "risk_unavailable",
            f"Could not read the risk of {plural(len(unread), 'changed file')}, so the "
            "risky-file gate cannot run. It needs git history: fetch it (fetch-depth: 0), "
            "or index the repository with `repowise init`.",
        )


async def _index_facts(root: Path, paths: list[str]) -> dict:
    """Hotspot, bug-magnet and dependent counts from the index; ``{}`` when it cannot say."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.analysis.patch_coverage import read_index_facts

    async with repo_index_session(root) as opened:
        if opened is None:
            return {}
        session, repo_id = opened
        try:
            return await read_index_facts(session, repo_id, paths)
        except (SQLAlchemyError, OSError, LookupError):
            # An index written by an older version can fail the query itself.
            return {}


def _with_hints(repo, pc):
    """*pc* with the test to extend per uncovered range, when an index opens.

    Hints are advice, never part of the verdict, so a missing or unreadable
    index leaves the rows without them (``hints`` null) and the gate as it was.
    """
    from repowise.core import git_refs
    from repowise.core.analysis.change_risk.features import revspec_head
    from repowise.core.analysis.patch_coverage import attach_hints

    root = repo_root(repo)
    if not has_db_store(root):
        return pc
    head = git_refs.resolve(str(root), revspec_head(pc.scope.label or None)) or None
    try:
        hints = run_async(_read_hints(root, pc, head))
    except Exception:
        # Advice only: whatever an old or damaged index does to the read, the
        # gate's verdict must not change because of it.
        hints = None
    return pc if hints is None else attach_hints(pc, hints)


async def _read_hints(root: Path, pc, head: str | None):
    """``{path: hints}`` from the index; ``None`` when it cannot say."""
    from repowise.core.analysis.patch_coverage import read_test_hints

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        return await read_test_hints(session, repo_id, pc, repo_path=str(root), head_commit=head)


def _resolve_reports(root, cfg, report_prefixes, report_format, notices):
    """Parse the reports against the files git tracks, failing when nothing usable remains."""
    from repowise.core import git_refs
    from repowise.core.analysis.health.coverage import build_coverage_map

    resolved, errors = build_coverage_map(
        root,
        list(report_prefixes),
        set(git_refs.tracked_paths(str(root))),
        coverage_format=report_format or cfg.format,
        strip_prefix=cfg.strip_prefix,
        path_prefix=cfg.path_prefix,
        report_prefixes=report_prefixes,
        ignore=cfg.ignore,
    )
    for path, err in errors:
        notices.print(f"[yellow]{escape(path.name)}: {escape(err)}[/yellow]")
    if len(errors) == len(report_prefixes):
        raise CannotEvaluateError(
            "report_unreadable", "No coverage report could be read; see the messages above."
        )
    if not resolved.files and resolved.ignored and not resolved.total:
        raise CannotEvaluateError(
            "report_all_ignored",
            f"All {resolved.ignored} report entries match coverage.ignore, so nothing "
            "is left to measure. Narrow coverage.ignore in .repowise/config.yaml.",
        )
    if not resolved.files:
        raise CannotEvaluateError(
            "report_unmatched",
            "No report path matched a file in this repository. If the report paths "
            "carry a build prefix, set coverage.strip_prefix in .repowise/config.yaml."
        )
    if resolved.mapping_partial:
        notices.print(
            "[yellow]More than half the report paths did not match a file in this "
            "repository; check coverage.strip_prefix / coverage.path_prefix.[/yellow]"
        )
    return resolved


def _coverage_config(
    root: Path, *, validate_threshold: bool, validate_min_lines: bool, validate_risky: bool = False
):
    from repowise.core.analysis.health.coverage import CoverageConfig
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        raw = load_repo_config(root)
    except RepoConfigError as exc:
        raise CannotEvaluateError("config_invalid", str(exc)) from exc
    cfg = CoverageConfig.from_repo_config(raw)
    block = raw.get("coverage")
    block = block if isinstance(block, dict) else {}
    # A gate that silently stops gating (or starts failing tiny changes) is
    # worse than no gate, so a value the config cannot use stops the check.
    for key, parsed, validate in (
        ("fail_under", cfg.fail_under, validate_threshold),
        ("fail_under_risky", cfg.fail_under_risky, validate_risky),
    ):
        if validate and block.get(key) is not None and parsed is None:
            raise CannotEvaluateError(
                "config_invalid",
                f"coverage.{key} must be a number from 0 to 100, got {block[key]!r}."
            )
    raw_min = block.get("min_coverable_lines")
    if validate_min_lines and raw_min is not None and cfg.min_coverable_lines is None:
        raise CannotEvaluateError(
            "config_invalid",
            f"coverage.min_coverable_lines must be a whole number of 0 or more, got {raw_min!r}."
        )
    if cfg.gate_errors:
        raise CannotEvaluateError("config_invalid", " ".join(cfg.gate_errors))
    return cfg


def _emit(pc, fmt: str) -> None:
    from repowise.core.analysis.patch_coverage import github_annotations, render_markdown

    if fmt == "json":
        emit_json(pc.to_dict())
    elif fmt == "markdown":
        click.echo(render_markdown(pc), nl=False)
    elif fmt == "github":
        for line in github_annotations(pc):
            click.echo(line)
        append_step_summary(render_markdown(pc))
        _print_summary(pc)
    else:
        _print_summary(pc)
        _print_path_gates(pc)
        _print_table(pc)


def _print_summary(pc) -> None:
    from repowise.core.analysis.patch_coverage import (
        headline,
        risk_basis_line,
        risky_line,
        scope_line,
    )

    console.print(escape(headline(pc, markdown=False)))
    if risky := risky_line(pc, markdown=False):
        console.print(escape(risky))
    console.print(f"[dim]{escape(scope_line(pc, markdown=False))}[/dim]")
    if basis := risk_basis_line(pc):
        console.print(f"[dim]{escape(basis)}.[/dim]")


def _print_path_gates(pc) -> None:
    from rich.table import Table

    from repowise.core.analysis.patch_coverage import path_gate_row

    if not pc.path_gates:
        return
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("Path-scoped gate")
    table.add_column("Verdict")
    table.add_column("Covered changed lines", justify="right")
    table.add_column("Threshold", justify="right")
    for g in pc.path_gates:
        table.add_row(*(escape(v) for v in path_gate_row(g)))
    console.print(table)


def _print_table(pc) -> None:
    from rich.table import Table

    from repowise.core.analysis.patch_coverage import (
        RANGE_LIMIT,
        STATUS_TEXT,
        attention_rows,
        first_hint,
        format_ranges,
        hint_phrase,
        risk_words,
    )

    rows = attention_rows(pc)
    if not rows:
        return
    with_hint = any(f.hints for f in rows)
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("File")
    table.add_column("Risk")
    table.add_column("Covered", justify="right")
    table.add_column("Uncovered changed lines")
    if with_hint:
        table.add_column("Extend")
    for f in rows:
        if f.status == "measured":
            covered = f"{f.covered_line_count} of {f.coverable_line_count}"
            gaps = format_ranges(f.uncovered_ranges, RANGE_LIMIT)
        else:
            covered, gaps = "", STATUS_TEXT[f.status]
        cells = [escape(f.file_path), escape(risk_words(f.risk)), covered, gaps]
        if with_hint:
            hint = first_hint(f)
            cells.append(escape(hint_phrase(hint)) if hint else "")
        table.add_row(*cells)
    console.print(table)
