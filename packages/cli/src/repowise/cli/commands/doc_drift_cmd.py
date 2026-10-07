"""``repowise doc-drift`` - assertions this repository's documents no longer satisfy.

By default it reads the findings the last ``init``/``update`` persisted, serialized
through the same function ``get_health(include=["doc_drift"])`` uses. ``--check``
runs the analyzer over the working tree with no index and gates on it (the CI mode).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import click

from repowise.cli.ci import (
    CI_FORMATS,
    EXIT_GATE_FAILED,
    SHALLOW_CLONE_HINT,
    append_step_summary,
    cannot_evaluate,
    ci_notices,
)
from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
from repowise.cli.output import emit_json, emit_refusal, format_option
from repowise.core.analysis.doc_drift.constants import (
    DETECTION_BASIS,
    HIGH_CONFIDENCE_THRESHOLD,
    bucket_confidences,
    confidence_tier,
)
from repowise.core.analysis.doc_drift.models import DriftKind

if TYPE_CHECKING:
    from repowise.core.analysis.doc_drift.gate import GateResult
    from repowise.core.analysis.doc_drift.models import DocDriftReport

#: Sentinels. Both are distinct from "no findings": an index that cannot be
#: read, and one written before the drift table existed. Reporting either as
#: zero drift would be the detector claiming a clean bill it never checked.
_NO_INDEX = object()
_STALE_INDEX = object()

_FORMATS = (*CI_FORMATS, "sarif", "gitlab")


def _repo_path(path: str | None, repo_alias: str | None, no_workspace: bool, fmt: str) -> Path:
    """The repository to read. One repo: a drift finding belongs to one tree."""
    target = resolve_command_target(
        path=path, no_workspace_flag=no_workspace, repo_alias=repo_alias
    )
    target.notice(ci_notices(fmt), command="doc-drift")
    return target.single_repo_path().resolve()


async def _read(root: Path, *, min_confidence: float | None, kinds: tuple[str, ...]) -> Any:
    """Persisted findings for *root*, or :data:`_NO_INDEX` when there is none."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence.crud import get_doc_drift_findings, serialize_doc_drift_row

    async with repo_index_session(root) as opened:
        if opened is None:
            return _NO_INDEX
        session, repo_id = opened
        try:
            rows = await get_doc_drift_findings(session, repo_id, min_confidence=min_confidence)
        except (SQLAlchemyError, OSError, LookupError):
            # An index written before migration 0065 has no ``doc_drift_findings``
            # table, and the query raises rather than returning nothing. Measured
            # on a real checkout; ``repo_index_session`` shields the open, not the
            # read, so this is the caller's to catch, as ``overlap`` does.
            return _STALE_INDEX
        # Filtered here rather than in the query: the store has no index on
        # ``kind`` and the table is one row per drifted reference, so the scan
        # the filter would ride on is the one already being done.
        if kinds:
            rows = [r for r in rows if r.kind in kinds]
        return [serialize_doc_drift_row(r) for r in rows]


async def _index_symbol_names(root: Path) -> frozenset[str] | None:
    """Every symbol name the index at *root* holds, or ``None`` when none opens.

    Read-only: the schema reconcile is skipped, so a CI read writes nothing.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence.crud import get_symbol_names

    async with repo_index_session(root, reconcile=False) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        try:
            return await get_symbol_names(session, repo_id)
        except (SQLAlchemyError, OSError):
            return None


def _read_live(
    root: Path,
    *,
    min_confidence: float | None,
    kinds: tuple[str, ...],
    symbol_names: frozenset[str] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Run the analyzer over the working tree: ``(report, finding dicts)``.

    The ``symbol`` kind runs only with *symbol_names*, which ``--kind symbol``
    reads from the index.
    """
    from repowise.core.analysis.doc_drift.live import run_live
    from repowise.core.analysis.doc_drift.serialize import serialize_finding

    config = {"min_confidence": min_confidence} if min_confidence is not None else None
    report = run_live(root, config=config, symbol_names=symbol_names)
    findings = [serialize_finding(f, live=True) for f in report.findings]
    if kinds:
        findings = [f for f in findings if f["kind"] in kinds]
    return report, findings


def _only_documents(findings: list[dict[str, Any]], documents: tuple[str, ...]) -> list[dict]:
    if not documents:
        return findings
    wanted = {d.replace("\\", "/").removeprefix("./") for d in documents}
    return [f for f in findings if f["file_path"] in wanted]


def _payload(
    root: Path, findings: list[dict[str, Any]], min_confidence: float | None
) -> dict[str, Any]:
    return {
        "repo": str(root),
        "min_confidence": min_confidence,
        "total": len(findings),
        "documents": len({f["file_path"] for f in findings}),
        "confidence": bucket_confidences(f["confidence"] for f in findings),
        "findings_basis": DETECTION_BASIS,
        "findings": findings,
    }


#: Tier name to terminal colour. Keyed off :func:`confidence_tier` rather than
#: re-comparing the thresholds, so this cannot disagree with the high/medium/low
#: summary printed directly above it.
_TIER_COLOUR = {"high": "red", "medium": "yellow", "low": "dim"}


def _render(payload: dict[str, Any]) -> None:
    """Grouped by document, because the document is the file a reader edits."""
    from rich.markup import escape

    # Sorted here, not relied upon from the query: the store orders by
    # confidence first, so one document's findings are contiguous only when
    # they happen to share a confidence, and a document with both a 0.95 anchor
    # and a 0.90 path would print two headers for itself.
    findings = sorted(payload["findings"], key=lambda f: (f["file_path"], f["line_number"]))
    if not findings:
        console.print("No documentation drift found.")
        console.print(f"[dim]{escape(DETECTION_BASIS)}[/dim]")
        return

    buckets = payload["confidence"]
    console.print(
        f"[bold]{payload['total']} finding(s) across {payload['documents']} document(s)[/bold] "
        f"[dim]({buckets['high']} high, {buckets['medium']} medium, {buckets['low']} low "
        f"confidence)[/dim]"
    )

    current = None
    for f in findings:
        if f["file_path"] != current:
            current = f["file_path"]
            console.print(f"\n[cyan]{escape(current)}[/cyan]")
        console.print(
            f"  [dim]:{f['line_number']}[/dim] "
            f"[{_TIER_COLOUR[confidence_tier(f['confidence'])]}]{f['confidence']:.2f}[/] "
            f"{escape(f['kind'])}  {escape(f['reason'])}"
        )
        if f.get("suggestion"):
            console.print(
                f"      [green]likely now: {escape(f['suggestion'])}[/green] "
                f"[dim]({escape(f.get('suggestion_basis') or '')})[/dim]"
            )
        for line in f.get("evidence") or []:
            console.print(f"      [dim]{escape(line)}[/dim]")

    console.print(f"\n[dim]{escape(DETECTION_BASIS)}[/dim]")


def _emit(
    fmt: str,
    payload: dict[str, Any],
    *,
    gate: GateResult | None = None,
    report: DocDriftReport | None = None,
    accepted: frozenset[str] = frozenset(),
) -> None:
    """Write *payload* in *fmt*; *gate* and *report* exist only under ``--check``."""
    scope = payload.get("scope") or {}
    from repowise.core.analysis.doc_drift import render

    findings = payload["findings"]
    if fmt == "json":
        emit_json(payload)
    elif fmt == "sarif":
        from repowise.cli import __version__

        fail_on = gate.fail_on if gate is not None else HIGH_CONFIDENCE_THRESHOLD
        emit_json(
            render.render_sarif(
                findings, tool_version=__version__, fail_on=fail_on, accepted=accepted
            )
        )
    elif fmt == "gitlab":
        fail_on = gate.fail_on if gate is not None else HIGH_CONFIDENCE_THRESHOLD
        emit_json(render.render_gitlab(findings, fail_on=fail_on, accepted=accepted))
    elif fmt in ("markdown", "github"):
        markdown = render.render_markdown(
            findings,
            gate=gate,
            documents_scanned=report.documents_scanned if report is not None else None,
            suppressed=report.suppressed if report is not None else 0,
            scope_label=scope.get("revspec"),
            out_of_scope=scope.get("out_of_scope", 0),
        )
        if fmt == "markdown":
            click.echo(markdown)
        else:
            for line in render.render_github_annotations(findings, gate=gate):
                click.echo(line)
            append_step_summary(markdown)
    else:
        _render(payload)
        if gate is not None:
            _print_verdict(gate)


def _print_verdict(gate: GateResult) -> None:
    if gate.passed:
        console.print(f"[green]Gate passed[/green] [dim](fails at {gate.fail_on:.2f})[/dim]")
        return
    accepted = f", {len(gate.baselined)} accepted by baseline" if gate.baselined else ""
    console.print(
        f"[red]Gate failed:[/red] {len(gate.failing)} finding(s) at or above "
        f"{gate.fail_on:.2f}{accepted}"
    )


def _refuse(code: str, message: str, fmt: str, *, remedy: str, repo: str) -> NoReturn:
    """Refuse and exit 1; machine formats other than json and gitlab keep stdout empty.

    ``gitlab`` prints an empty issue list, so the report artifact stays valid.
    """
    if fmt in ("json", "table"):
        emit_refusal(code, message, fmt, remedy=remedy, repo=repo)
    elif fmt == "gitlab":
        click.echo("[]")
    from rich.markup import escape

    notices = ci_notices(fmt)
    notices.print(f"[red]{escape(message)}[/red]")
    notices.print(f"[dim]{escape(remedy)}[/dim]")
    raise click.exceptions.Exit(1)


def _scope_to(root: Path, since: str, findings: list[dict], fmt: str) -> tuple[list, dict]:
    """Keep the findings the change *since* is answerable for; exit 2 when it cannot diff."""
    import subprocess

    from repowise.core.analysis.doc_drift.scope import scope_findings, scope_since
    from repowise.core.ci.base import BaseNotFoundError, default_revspec

    try:
        revspec = default_revspec(str(root)) if since == "auto" else since
    except BaseNotFoundError as exc:
        cannot_evaluate(fmt, "base_not_found", str(exc))
    try:
        scope = scope_since(str(root), revspec)
    except ValueError as exc:
        cannot_evaluate(fmt, "diff_failed", f"Could not diff {revspec}: {exc}. {SHALLOW_CLONE_HINT}")
    except (subprocess.SubprocessError, OSError) as exc:
        cannot_evaluate(fmt, "git_failed", f"Could not run git: {exc}")
    kept, left_out = scope_findings(findings, scope)
    return kept, scope.summary(left_out)


def _run_check(
    root: Path,
    fmt: str,
    *,
    min_confidence: float | None,
    kinds: tuple[str, ...],
    documents: tuple[str, ...],
    fail_on: float,
    baseline_path: Path | None,
    write_baseline_path: Path | None,
    since: str | None = None,
) -> None:
    from repowise.cli.helpers import silence_logs_for_machine_output_until_close
    from repowise.core.analysis.doc_drift.gate import evaluate_gate
    from repowise.core.analysis.doc_drift.live import LiveTreeError

    # The analyzer's debug line would land in the report a CI log shows.
    silence_logs_for_machine_output_until_close()
    if min_confidence is not None and min_confidence > fail_on:
        ci_notices(fmt).print(
            f"[yellow]--min-confidence {min_confidence:.2f} is above --fail-on-confidence "
            f"{fail_on:.2f}; findings between them are hidden from the gate.[/yellow]"
        )
    symbol_names = _check_symbol_names(root, kinds, fmt)
    try:
        report, findings = _read_live(
            root, min_confidence=min_confidence, kinds=kinds, symbol_names=symbol_names
        )
    except LiveTreeError as exc:
        cannot_evaluate(fmt, "not_a_git_repository", str(exc))
    findings = _only_documents(findings, documents)
    scope = None
    if since is not None:
        findings, scope = _scope_to(root, since, findings, fmt)

    if write_baseline_path is not None:
        _write_check_baseline(write_baseline_path, findings, fmt)
        return

    baseline = _read_check_baseline(baseline_path, fmt)
    gate = evaluate_gate(findings, fail_on=fail_on, baseline=baseline)
    payload = _payload(root, findings, min_confidence)
    payload.update(
        documents_scanned=report.documents_scanned,
        references_checked=report.references_checked,
        suppressed=report.suppressed,
        gate=gate.to_dict(),
    )
    if scope is not None:
        payload["scope"] = scope
    _emit(fmt, payload, gate=gate, report=report, accepted=baseline or frozenset())
    if not gate.passed:
        raise click.exceptions.Exit(EXIT_GATE_FAILED)


def _check_symbol_names(root: Path, kinds: tuple[str, ...], fmt: str) -> frozenset[str] | None:
    """The index's symbol names when ``--kind symbol`` asks; exit 2 without an index."""
    if DriftKind.SYMBOL.value not in kinds:
        return None
    names = run_async(_index_symbol_names(root))
    if names is None:
        cannot_evaluate(
            fmt,
            "no_index",
            f"--kind symbol needs a Repowise index at {root}; run 'repowise init' there.",
        )
    return names


def _write_check_baseline(path: Path, findings: list[dict], fmt: str) -> None:
    from repowise.core.analysis.doc_drift.baseline import write_baseline

    try:
        written = write_baseline(path, findings)
    except OSError as exc:
        cannot_evaluate(fmt, "baseline_unwritable", f"cannot write baseline {path}: {exc}")
    ci_notices(fmt).print(
        f"Recorded {len(findings)} finding(s) as {written} baseline entr"
        f"{'y' if written == 1 else 'ies'} in {path}."
    )


def _read_check_baseline(path: Path | None, fmt: str) -> frozenset[str] | None:
    from repowise.core.analysis.doc_drift.baseline import BaselineError, read_baseline

    if path is None:
        return None
    try:
        return read_baseline(path)
    except BaselineError as exc:
        cannot_evaluate(fmt, "baseline_unreadable", str(exc))


@click.command("doc-drift")
@click.argument("path", required=False, default=None)
@click.option(
    "--min-confidence",
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help=(
        "Hide findings below this confidence. Defaults to showing everything "
        "the index stored; the pass already applied the repository's own cutoff "
        "when it wrote them."
    ),
)
@click.option(
    "--kind",
    "kinds",
    multiple=True,
    type=click.Choice([k.value for k in DriftKind]),
    help="Only this reference class. Repeatable.",
)
@click.option(
    "--document",
    "documents",
    multiple=True,
    help="Only findings in this document (repo-relative). Repeatable.",
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help=(
        "Read the working tree without an index and exit 1 when the gate fails. "
        "With --kind symbol it also checks symbol references, which needs an index."
    ),
)
@click.option(
    "--fail-on-confidence",
    type=click.FloatRange(0.0, 1.0),
    default=HIGH_CONFIDENCE_THRESHOLD,
    show_default=True,
    help="With --check, fail on findings at or above this confidence.",
)
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="With --check, accept findings recorded in this file; only new ones fail.",
)
@click.option(
    "--write-baseline",
    "write_baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="With --check, record the current findings to this file and exit 0.",
)
@click.option(
    "--since",
    metavar="REVSPEC",
    default=None,
    help=(
        "With --check, gate only drift this change is answerable for: documents it "
        "edits, documents naming files it deletes or renames, anchors into documents "
        "it edits, commands whose manifest it edits, and symbols whose defining file "
        "it edits or removes. A bare ref means REF...HEAD, "
        "plus uncommitted changes; 'auto' reads the target branch from CI."
    ),
)
@click.option("--repo", "repo_alias", default=None, help="In workspace mode, target one repo.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
@format_option(
    choices=_FORMATS,
    help=(
        "Output format. 'github' prints annotations and fills the job summary; "
        "'sarif' is for code-scanning upload; 'gitlab' is a GitLab Code Quality report."
    ),
)
def doc_drift_command(
    path: str | None,
    min_confidence: float | None,
    kinds: tuple[str, ...],
    documents: tuple[str, ...],
    check: bool,
    fail_on_confidence: float,
    baseline_path: Path | None,
    write_baseline_path: Path | None,
    since: str | None,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
) -> None:
    """Show documentation that the repository no longer matches."""
    if not check and (baseline_path or write_baseline_path):
        raise click.UsageError("--baseline and --write-baseline require --check.")
    if since is not None and not check:
        raise click.UsageError("--since requires --check.")
    if since is not None and write_baseline_path:
        raise click.UsageError("--write-baseline records every finding; drop --since.")
    root = _repo_path(path, repo_alias, no_workspace, fmt)

    if check:
        _run_check(
            root,
            fmt,
            min_confidence=min_confidence,
            kinds=kinds,
            documents=documents,
            fail_on=fail_on_confidence,
            baseline_path=baseline_path,
            write_baseline_path=write_baseline_path,
            since=since,
        )
        return

    result = run_async(_read(root, min_confidence=min_confidence, kinds=kinds))

    if result is _NO_INDEX:
        _refuse(
            "no_index",
            f"No readable Repowise index at {root}.",
            fmt,
            remedy="Run 'repowise init' there first, or use --check to read the tree directly.",
            repo=str(root),
        )
    if result is _STALE_INDEX:
        _refuse(
            "index_predates_doc_drift",
            f"The index at {root} was written before drift findings were stored.",
            fmt,
            remedy="Run 'repowise update' there to populate them.",
            repo=str(root),
        )

    _emit(fmt, _payload(root, _only_documents(result, documents), min_confidence))
