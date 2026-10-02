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

_FORMATS = (*CI_FORMATS, "sarif")


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


def _read_live(
    root: Path,
    *,
    min_confidence: float | None,
    kinds: tuple[str, ...],
) -> tuple[Any, list[dict[str, Any]]]:
    """Run the analyzer over the working tree: ``(report, finding dicts)``."""
    from repowise.core.analysis.doc_drift.live import run_live
    from repowise.core.analysis.doc_drift.serialize import serialize_finding

    config = {"min_confidence": min_confidence} if min_confidence is not None else None
    report = run_live(root, config=config)
    findings = [serialize_finding(f) for f in report.findings]
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
    elif fmt in ("markdown", "github"):
        markdown = render.render_markdown(
            findings,
            gate=gate,
            documents_scanned=report.documents_scanned if report is not None else None,
            suppressed=report.suppressed if report is not None else 0,
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
    """Refuse and exit 1; machine formats other than json keep stdout empty."""
    if fmt in ("json", "table"):
        emit_refusal(code, message, fmt, remedy=remedy, repo=repo)
    from rich.markup import escape

    notices = ci_notices(fmt)
    notices.print(f"[red]{escape(message)}[/red]")
    notices.print(f"[dim]{escape(remedy)}[/dim]")
    raise click.exceptions.Exit(1)


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
) -> None:
    from repowise.cli.helpers import silence_logs_for_machine_output
    from repowise.core.analysis.doc_drift.baseline import (
        BaselineError,
        read_baseline,
        write_baseline,
    )
    from repowise.core.analysis.doc_drift.gate import evaluate_gate
    from repowise.core.analysis.doc_drift.live import LiveTreeError

    # The analyzer's debug line would land in the report a CI log shows.
    silence_logs_for_machine_output()
    if min_confidence is not None and min_confidence > fail_on:
        ci_notices(fmt).print(
            f"[yellow]--min-confidence {min_confidence:.2f} is above --fail-on-confidence "
            f"{fail_on:.2f}; findings between them are hidden from the gate.[/yellow]"
        )
    try:
        report, findings = _read_live(root, min_confidence=min_confidence, kinds=kinds)
    except LiveTreeError as exc:
        cannot_evaluate(fmt, "not_a_git_repository", str(exc))
    findings = _only_documents(findings, documents)

    if write_baseline_path is not None:
        try:
            written = write_baseline(write_baseline_path, findings)
        except OSError as exc:
            cannot_evaluate(
                fmt, "baseline_unwritable", f"cannot write baseline {write_baseline_path}: {exc}"
            )
        ci_notices(fmt).print(
            f"Recorded {len(findings)} finding(s) as {written} baseline entr"
            f"{'y' if written == 1 else 'ies'} in {write_baseline_path}."
        )
        return

    baseline = None
    if baseline_path is not None:
        try:
            baseline = read_baseline(baseline_path)
        except BaselineError as exc:
            cannot_evaluate(fmt, "baseline_unreadable", str(exc))

    gate = evaluate_gate(findings, fail_on=fail_on, baseline=baseline)
    payload = _payload(root, findings, min_confidence)
    payload.update(
        documents_scanned=report.documents_scanned,
        references_checked=report.references_checked,
        suppressed=report.suppressed,
        gate=gate.to_dict(),
    )
    _emit(fmt, payload, gate=gate, report=report, accepted=baseline or frozenset())
    if not gate.passed:
        raise click.exceptions.Exit(EXIT_GATE_FAILED)


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
    help="Read the working tree without an index and exit 1 when the gate fails.",
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
@click.option("--repo", "repo_alias", default=None, help="In workspace mode, target one repo.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
@format_option(
    choices=_FORMATS,
    help="Output format. 'github' prints annotations and fills the job summary.",
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
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
) -> None:
    """Show documentation that the repository no longer matches."""
    if not check and (baseline_path or write_baseline_path):
        raise click.UsageError("--baseline and --write-baseline require --check.")
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
