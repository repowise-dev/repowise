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
informational fails, the risky files' is below ``--fail-under-risky``, or
project coverage fell more than ``--max-drop`` points from the change's base,
2 when the check could not run (no report, a ``--report`` matching no file,
unreadable report, unknown revision, missing history, bad config, risk
unreadable or a shallow clone under ``--fail-under-risky``, no base
measurement under ``--max-drop``, or a base that measured something else). A
change under ``--min-coverable-lines`` is reported against the threshold but
exits 0.

Project coverage compares the change's base with its head: a ``--base-report``
measured at the base commit gives the totals and the files whose coverage
changed outside the change; without one, an index's ingest at the base commit
gives the totals alone.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

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
    "--base-report",
    "base_reports",
    multiple=True,
    help="Coverage report measured at the change's base commit (the merge-base for "
    "A...B), read like --report. Compares project coverage and lists files whose "
    "coverage changed outside the change.",
)
@click.option(
    "--max-drop",
    type=click.FloatRange(0, 100),
    default=None,
    help="Exit 1 when project coverage falls more than this many points from the "
    "change's base. Defaults to coverage.max_drop in .repowise/config.yaml.",
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
    base_reports: tuple[str, ...],
    max_drop: float | None,
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
        repowise coverage check --report lcov.info --base-report base/lcov.info --max-drop 0.5

    Each changed file carries its risk (git bug-fix history, plus hotspot,
    bug-magnet and dependent counts when an index exists), and the rows read
    riskiest first. ``--fail-under-risky`` gates the risky files alone. With an
    index, each uncovered range also names the test file to extend.

    ``--base-report`` (or, without one, the coverage an index stored at the
    base commit) adds project coverage at the base against the head, which
    ``--max-drop`` gates, and the files whose coverage changed on lines the
    change did not touch.
    """
    notices = ci_notices(fmt)
    try:
        pc, head, head_reports = _evaluate(
            revspec, reports, report_format, fail_under, min_coverable_lines, repo, notices
        )
        pc = _with_risk(repo, pc, fail_under_risky)
        pc = _with_hints(repo, pc)
        ask = _project_ask(pc, head, head_reports, base_reports, max_drop, notices)
        pc = _with_project(pc, ask) if ask is not None else pc
    except CannotEvaluateError as exc:
        cannot_evaluate(fmt, exc.code, str(exc))
    _emit(pc, fmt)
    if pc.gate == "fail":
        raise click.exceptions.Exit(EXIT_GATE_FAILED)


def _evaluate(revspec, reports, report_format, fail_under, min_coverable_lines, repo, notices):
    """``(patch coverage, the head's resolved reports, how they were read)``.

    The resolved reports are ``None`` when the coverage was read from the
    index, and the :class:`_Reports` then names no file.
    """
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
        stored = run_async(_stored(root, changed, label, threshold, min_coverable_lines, cfg))
        return _gateable(stored), None, _Reports(root, cfg, {}, report_format)
    if notices is console:
        # Machine formats carry the list in ``scope.reports`` instead.
        notices.print(f"[dim]Reading {', '.join(escape(str(p)) for p in report_paths)}[/dim]")

    head_reports = _Reports(root, cfg, report_prefixes, report_format)
    resolved = _resolve_reports(head_reports, notices)
    pc = patch_coverage_from_resolved(
        changed,
        resolved,
        threshold=threshold,
        label=label,
        reports=[str(p) for p in report_paths],
        min_coverable_lines=min_coverable_lines,
        ignore=cfg.ignore,
        gates=cfg.gates,
    )
    return pc, resolved, head_reports


def _first_set(flag, configured):
    """The flag's value, else the config's: ``0`` is a value, not unset."""
    return configured if flag is None else flag


def _cli_reports(args: tuple[str, ...], flag: str = "--report") -> dict[Path, str | None]:
    """*flag* values (``--report``, ``--base-report``), relative to cwd; one that matches no file
    cannot be evaluated."""
    from repowise.core.analysis.health.coverage import expand_report_args

    try:
        # Relative, so reports read as the caller named them.
        return expand_report_args(args, Path())
    except FileNotFoundError as exc:
        raise CannotEvaluateError("report_not_found", f"{flag}: {exc}") from None


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


@dataclass(frozen=True)
class _Reports:
    """Report files and how to read them: the repository, its ``coverage:`` config, the format."""

    root: Path
    cfg: Any
    prefixes: dict[Path, str | None]
    report_format: str | None

    def build(self, keys):
        """``build_coverage_map`` over these reports, against the repository paths *keys*."""
        from repowise.core.analysis.health.coverage import build_coverage_map

        return build_coverage_map(
            self.root,
            list(self.prefixes),
            set(keys),
            coverage_format=self.report_format or self.cfg.format,
            strip_prefix=self.cfg.strip_prefix,
            path_prefix=self.cfg.path_prefix,
            report_prefixes=self.prefixes,
            ignore=self.cfg.ignore,
        )


def _resolve_reports(reports: _Reports, notices, keys=None, side: str = ""):
    """Parse the reports against the files git tracks, failing when nothing usable remains.

    *keys* are the paths to resolve against (default: what git tracks now);
    *side* prefixes each failure, for the base report.
    """
    from repowise.core import git_refs

    keys = git_refs.tracked_paths(str(reports.root)) if keys is None else keys
    resolved, errors = reports.build(keys)
    for path, err in errors:
        notices.print(f"[yellow]{escape(path.name)}: {escape(err)}[/yellow]")
    if len(errors) == len(reports.prefixes):
        raise CannotEvaluateError(
            "report_unreadable",
            f"{side}No coverage report could be read; see the messages above.",
        )
    if not resolved.files and resolved.ignored and not resolved.total:
        raise CannotEvaluateError(
            "report_all_ignored",
            f"{side}All {resolved.ignored} report entries match coverage.ignore, so nothing "
            "is left to measure. Narrow coverage.ignore in .repowise/config.yaml.",
        )
    if not resolved.files:
        raise CannotEvaluateError(
            "report_unmatched",
            f"{side}No report path matched a file in this repository. If the report paths "
            "carry a build prefix, set coverage.strip_prefix in .repowise/config.yaml."
        )
    if resolved.mapping_partial:
        notices.print(
            "[yellow]More than half the report paths did not match a file in this "
            "repository; check coverage.strip_prefix / coverage.path_prefix.[/yellow]"
        )
    return resolved


def _coverage_config(
    root: Path,
    *,
    validate_threshold: bool,
    validate_min_lines: bool,
    validate_risky: bool = False,
    validate_max_drop: bool = False,
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
        ("max_drop", cfg.max_drop, validate_max_drop),
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
        _print_outside_change(pc)


def _print_summary(pc) -> None:
    from repowise.core.analysis.patch_coverage import (
        headline,
        project_line,
        risk_basis_line,
        risky_line,
        scope_line,
    )

    console.print(escape(headline(pc, markdown=False)))
    if project := project_line(pc, markdown=False):
        console.print(escape(project))
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


def _print_outside_change(pc) -> None:
    """The files whose coverage changed outside the change, when a base report named them."""
    from rich.table import Table

    from repowise.core.analysis.patch_coverage import indirect_row, outside_change_rows
    from repowise.core.ci.markdown import ROW_LIMIT, more_line

    rows = outside_change_rows(pc)
    note = pc.project.outside_change_note if pc.project is not None else None
    if note:
        console.print(f"[dim]Coverage outside the change, files left out: {escape(note)}.[/dim]")
    if not rows:
        return
    table = Table(title="Coverage outside the change", show_edge=False, pad_edge=False)
    for name, justify in (
        ("File", "left"),
        ("Before", "right"),
        ("After", "right"),
        ("Newly uncovered lines", "left"),
        ("Cause", "left"),
    ):
        table.add_column(name, justify=justify)
    for c in rows[:ROW_LIMIT]:
        table.add_row(*(escape(v) for v in indirect_row(c)))
    console.print(table)
    if len(rows) > ROW_LIMIT:
        console.print(f"[dim]{more_line(len(rows) - ROW_LIMIT, 'files')}[/dim]")


@dataclass(frozen=True)
class _ProjectAsk:
    """What a project comparison reads, and who asked for it.

    *head* is the head's resolved reports (``None`` when read from the index),
    read as *reports* says. *strict* when a flag asked (``--max-drop``,
    ``--base-report``): a base the check cannot use then exits 2.
    """

    reports: _Reports
    head: Any
    base_reports: tuple[str, ...]
    label: str
    notices: Any
    max_drop: float | None
    strict: bool

    @property
    def root(self) -> Path:
        return self.reports.root


def _project_ask(pc, head, reports: _Reports, base_reports, max_drop, notices):
    """The project comparison asked for, or ``None``: without ``--base-report`` or a
    max-drop gate (flag or config) nothing is read."""
    cfg = _coverage_config(
        reports.root,
        validate_threshold=False,
        validate_min_lines=False,
        validate_max_drop=max_drop is None,
    )
    strict = max_drop is not None or bool(base_reports)
    max_drop = _first_set(max_drop, cfg.max_drop)
    if not base_reports and max_drop is None:
        return None
    return _ProjectAsk(
        reports, head, tuple(base_reports), pc.scope.label, notices, max_drop, strict
    )


def _with_project(pc, ask: _ProjectAsk):
    """*pc* with project coverage at the base against the head.

    A base report gives the totals and the files that changed outside the
    change; without one, an index's ingest at the base commit gives the
    totals. A base the check cannot use (none, incomparable, no coverable line
    on a side) exits 2 when a flag asked for it, and is a note when only the
    config did, or when another gate already failed: that verdict stands.
    """
    from repowise.core import git_refs

    root = str(ask.root)
    base_commit = git_refs.change_base(root, ask.label) or None
    head_commit = git_refs.resolve(root, _revspec_head(ask.label)) or None
    try:
        measure = _report_delta if ask.base_reports else _history_delta
        project = _usable(measure(ask, base_commit), ask.max_drop, head_commit)
    except CannotEvaluateError as exc:
        if ask.strict and pc.gate != "fail":
            raise
        _table_note(ask.notices, f"Project coverage not evaluated: {exc}")
        return pc
    return replace(pc, project=project)


def _usable(project, max_drop, head_commit):
    """*project* gated by *max_drop*, or why it cannot be judged."""
    if project is None:
        raise CannotEvaluateError(
            "project_base_missing",
            "The max-drop gate needs coverage measured at the change's base: pass it with "
            "--base-report, or ingest one there with `repowise coverage add`.",
        )
    project = replace(project, max_drop=max_drop, head_commit=head_commit)
    if project.incomparable:
        raise CannotEvaluateError(
            "project_scope_mismatch",
            "Project coverage cannot be compared with the base: "
            f"{'; '.join(project.incomparable)}.",
        )
    if project.gate == "no_data":
        side = "base" if not (project.base and project.base.pct is not None) else "head"
        raise CannotEvaluateError(
            "project_base_missing",
            f"Project coverage cannot be compared: the {side} measured no coverable line.",
        )
    return project


def _table_note(notices, text: str) -> None:
    # Machine formats say it with ``project: null``; the note is for a reader.
    if notices is console:
        notices.print(f"[dim]{escape(text)}[/dim]")


def _revspec_head(label: str) -> str:
    from repowise.core.analysis.change_risk.features import revspec_head

    return revspec_head(label or None)


def _report_delta(ask: _ProjectAsk, base_commit: str | None):
    """Totals, and the files changed outside the change, from a report measured at the base.

    Both sides count the same files: with a head read from the index, the
    base keeps the files the index lists (what stored ingests resolve against),
    plus those the change renamed or deleted, so an indexed file the head
    ingest no longer names is still ``no_longer_measured``.
    """
    from repowise.core import git_refs
    from repowise.core.analysis.patch_coverage import (
        ProjectDelta,
        incomparable_reasons,
        indirect_changes,
        project_totals,
    )

    if base_commit is None:
        raise CannotEvaluateError(
            "project_base_missing",
            f"Could not find the base commit of {ask.label}, so --base-report cannot "
            "be lined up with the change. Fetch the base branch's history.",
        )
    base = _resolve_reports(
        replace(ask.reports, prefixes=_cli_reports(ask.base_reports, "--base-report")),
        ask.notices,
        keys=git_refs.tracked_paths_at(str(ask.root), base_commit),
        side="Base report: ",
    )
    head_cov, head_scope, index_keys = _head_coverage(ask.root, ask.head)
    diffs, renames, deleted = _change_diff(ask.root, base_commit, _revspec_head(ask.label))
    base_cov = {fc.file_path: fc for fc in base.files}
    if index_keys is not None:
        base_cov = {
            p: fc
            for p, fc in base_cov.items()
            if p in index_keys or p in deleted or p in renames
        }
    incomparable = incomparable_reasons(base.scope, head_scope)
    rows = note = None
    if not incomparable:
        scan = indirect_changes(base_cov, head_cov, diffs, renames, deleted)
        note = scan.note(base_commit)
        if scan.rows is not None:
            changed = set(diffs) | deleted | set(renames.values())
            rows = _with_causes(ask.root, scan.rows, changed, deleted)
    return ProjectDelta(
        base=project_totals(base_cov),
        head=project_totals(head_cov),
        basis="base_report",
        base_commit=base_commit,
        incomparable=incomparable,
        outside_change=rows,
        outside_change_note=note,
    )


def _head_coverage(root: Path, head) -> tuple[dict, Any, set[str] | None]:
    """``({path: FileCoverage}, scope, index file keys)`` of the head.

    From its reports (keys ``None``: they resolved against every tracked
    file), else from the index, with the file keys its ingests resolve against.
    """
    if head is not None:
        return {fc.file_path: fc for fc in head.files}, head.scope, None
    found = run_async(_stored_head(root))
    if found is None:
        raise CannotEvaluateError(
            "index_unreadable", "Could not read the index's coverage for the head."
        )
    return found


async def _stored_head(root: Path):
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.cli.commands.coverage_cmd import _repo_file_keys
    from repowise.core.analysis.patch_coverage import ingest_scope
    from repowise.core.persistence.crud import load_file_coverage, load_newest_ingest

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        try:
            ingest = await load_newest_ingest(session, repo_id)
            files = await load_file_coverage(session, repo_id)
            keys = await _repo_file_keys(session, repo_id)
        except SQLAlchemyError:
            return None
        return files, ingest_scope(ingest) if ingest is not None else None, keys


def _change_diff(root: Path, base_commit: str, head_rev: str):
    """``(diffs, renames, deleted)`` from the base commit to the head, or why not."""
    import subprocess

    from repowise.core.analysis.changed_lines import change_diff

    try:
        return change_diff(str(root), base_commit, head_rev)
    except ValueError as exc:
        raise CannotEvaluateError("diff_failed", f"Could not diff the change: {exc}") from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise CannotEvaluateError("git_failed", f"Could not run git: {exc}") from exc


def _history_delta(ask: _ProjectAsk, base_commit: str | None):
    """Totals from the index's ingest at *base_commit*; ``None`` when there is none."""
    if base_commit is None or not has_db_store(ask.root):
        return None
    found = run_async(_read_history(ask.root, ask.head, base_commit))
    if found is None or ask.head is None:
        return found
    row, keys = found
    return _report_against_ingest(ask, row, keys)


async def _read_history(root: Path, head, base_commit: str):
    """The stored delta when the head is stored too; else ``(base ingest, index file keys)``."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.cli.commands.coverage_cmd import _repo_file_keys
    from repowise.core.analysis.patch_coverage import history_delta
    from repowise.core.persistence.crud import load_ingest_at_commit

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        try:
            if head is None:
                return await history_delta(session, repo_id, base_commit)
            row = await load_ingest_at_commit(session, repo_id, base_commit)
            keys = await _repo_file_keys(session, repo_id) if row is not None else set()
        except (SQLAlchemyError, OSError):
            # An index too old or damaged to answer has no base to offer.
            return None
    return None if row is None else (row, keys)


def _report_against_ingest(ask: _ProjectAsk, row, keys):
    """The head report's totals against a stored base ingest.

    The ingest counted the files the index knows, so the head report is
    resolved against the same keys, not against every file git tracks.
    """
    from repowise.core.analysis.patch_coverage import (
        ProjectDelta,
        incomparable_reasons,
        ingest_scope,
        ingest_totals,
        project_totals,
    )

    reasons = incomparable_reasons(ingest_scope(row), ask.head.scope)
    head_totals = None
    if not keys:
        reasons += ("the index lists no files to resolve the head report against",)
    else:
        resolved, _errors = ask.reports.build(keys)
        head_totals = project_totals({fc.file_path: fc for fc in resolved.files})
    return ProjectDelta(
        base=ingest_totals(row),
        head=head_totals,
        basis="history",
        base_commit=row.ingested_commit_sha,
        incomparable=reasons,
    )


def _with_causes(root: Path, rows, changed: set[str], deleted: set[str]):
    """*rows* with the changed files that explain each one's lost coverage (advice only).

    From the index when one opens, else from git by name; an index that fails
    the read leaves the causes unassessed.
    """
    from repowise.core.analysis.patch_coverage import apply_causes, name_causes, needs_causes

    causes = None
    if has_db_store(root):
        try:
            causes = run_async(_read_causes(root, rows, changed, deleted))
        except Exception:
            causes = None  # advice: an index that breaks the read leaves causes unassessed
    else:
        causes = name_causes(needs_causes(rows), changed, deleted)
    return apply_causes(rows, causes)


async def _read_causes(root: Path, rows, changed: set[str], deleted: set[str]):
    from repowise.core.analysis.patch_coverage import (
        name_causes,
        needs_causes,
        read_indirect_causes,
    )

    async with repo_index_session(root) as opened:
        if opened is None:
            return name_causes(needs_causes(rows), changed, deleted)
        session, repo_id = opened
        return await read_indirect_causes(session, repo_id, rows, changed, deleted)
