"""``repowise health --score``: score files as they are on disk, before a PR exists.

Structure comes from parsing the bytes; history, coverage, test pairing, blame
findings and dependents come from the index (``IndexInputs``), so a file scores
about as the next ``repowise update`` would, with no update run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from repowise.cli.helpers import console, repo_index_session, run_async
from repowise.core.analysis.change_health.analyzer import RevisionAnalysis, RevisionHealthAnalyzer
from repowise.core.analysis.change_health.file_scores import (
    FileScoreDelta,
    file_score_deltas,
    score_change,
)
from repowise.core.analysis.change_health.index_inputs import IndexInputs, load_index_inputs
from repowise.core.analysis.change_health.sources import GitRevisionSource


@dataclass(frozen=True)
class LiveScores:
    rows: list[FileScoreDelta]
    head: RevisionAnalysis
    against: str | None
    #: False with no index: history and coverage are then left out.
    indexed: bool


def repo_paths(repo_path: Path, raw: tuple[str, ...]) -> list[str]:
    """Repo-relative POSIX paths for what the user typed, from any directory."""
    root = repo_path.resolve()
    out: list[str] = []
    outside: list[str] = []
    for item in raw:
        candidate = Path(item)
        absolute = candidate if candidate.is_absolute() else Path.cwd() / candidate
        try:
            out.append(absolute.resolve().relative_to(root).as_posix())
        except ValueError:
            outside.append(item)
    if outside:
        raise click.UsageError(f"--score paths outside {root}: {', '.join(outside)}")
    return list(dict.fromkeys(out))


def score_live(repo_path: Path, paths: list[str], against: str | None) -> LiveScores:
    """Score *paths* from disk, and at *against* too when given."""
    inputs = _index_inputs(repo_path)
    analyzer = RevisionHealthAnalyzer(
        repo_root=str(repo_path), config=analyzer_config(repo_path, paths)
    )
    source = GitRevisionSource(str(repo_path), hunks=False)
    head = _analyze(analyzer, source.read_working_tree(paths), paths, inputs)
    base_metrics: dict[str, Any] = {}
    if against is not None:
        try:
            sha = source.commit_sha(against)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        base_metrics = _analyze(analyzer, source.read(sha, paths), paths, inputs).metrics
    rows = file_score_deltas(base_metrics, head.metrics, paths, {}, full=True)
    return LiveScores(rows=rows, head=head, against=against, indexed=inputs.has_history)


def analyzer_config(repo_path: Path, paths: list[str]) -> dict | None:
    """The repo's ``.repowise/health-rules.json`` as analyzer config, or ``None``."""
    from repowise.core.analysis.health.config import HealthConfig

    health_cfg = HealthConfig.load(repo_path)
    if not (health_cfg.disabled_biomarkers or health_cfg.rules):
        return None
    return health_cfg.to_analyzer_config(paths)


def _index_inputs(repo_path: Path) -> IndexInputs:
    from sqlalchemy.exc import SQLAlchemyError

    async def _do() -> IndexInputs:
        async with repo_index_session(repo_path) as opened:
            if opened is None:
                return IndexInputs()
            session, repo_id = opened
            return await load_index_inputs(session, repo_id)

    try:
        return run_async(_do())
    except (SQLAlchemyError, OSError):
        return IndexInputs()


def _analyze(
    analyzer: RevisionHealthAnalyzer,
    sources: dict[str, bytes],
    paths: list[str],
    inputs: IndexInputs,
) -> RevisionAnalysis:
    return analyzer.analyze(
        sources, subject_paths=set(paths), dependents=inputs.dependents, index=inputs
    )


def render_live(scores: LiveScores, fmt: str) -> None:
    """The live scores as a table, or JSON with ``--format json``."""
    if fmt == "json":
        click.echo(json.dumps(live_payload(scores), indent=2))
        return
    table = Table(title="Live scores (from disk; history and coverage from the index)")
    table.add_column("File", style="cyan")
    if scores.against:
        table.add_column(f"At {scores.against}", justify="right")
    table.add_column("Now", justify="right")
    if scores.against:
        table.add_column("Change", justify="right")
    table.add_column("Structure", justify="right")
    table.add_column("History", justify="right")
    table.add_column("NLOC", justify="right")
    for row in scores.rows:
        table.add_row(*_cells(row, scores))
    console.print(table)
    change = score_change(scores.rows)
    if scores.against and change is not None:
        console.print(
            f"Average (weighted by nloc at {scores.against}): {change.base:.2f} -> "
            f"{change.head:.2f} ({change.head - change.base:+.2f}; structure deduction "
            f"{change.structure_delta:+.2f})"
        )
    if not scores.indexed:
        console.print(
            "[dim]No index history: history, coverage and test pairing are left out "
            "(code shape only).[/dim]"
        )
    missing = [r.path for r in scores.rows if r.head_score is None]
    if missing:
        console.print(f"[dim]Not scored (missing, or no health support): {', '.join(missing)}[/dim]")


def live_payload(scores: LiveScores) -> dict[str, Any]:
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION

    change = score_change(scores.rows)
    return {
        "basis": "live",
        "analyzer_version": HEALTH_ANALYZER_VERSION,
        "history_source": "index" if scores.indexed else "none",
        "against": scores.against,
        "files": [
            {**row.as_dict(), "head_history": _history(scores.head, row.path)}
            for row in scores.rows
        ],
        "score_change": change.as_dict() if change is not None else None,
    }


def _cells(row: FileScoreDelta, scores: LiveScores) -> list[str]:
    cells = [row.path]
    if scores.against:
        cells.append(_num(row.base_score))
    cells.append(_num(row.head_score))
    if scores.against:
        both = row.base_score is not None and row.head_score is not None
        cells.append(f"{row.head_score - row.base_score:+.2f}" if both else "-")
    cells += [
        _num(row.head_structure),
        _num(_history(scores.head, row.path)),
        str(row.nloc_head) if row.nloc_head is not None else "-",
    ]
    return cells


def _history(head: RevisionAnalysis, path: str) -> float | None:
    metric = head.metrics.get(path)
    return metric.history_deduction if metric is not None else None


def _num(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "-"
