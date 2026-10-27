"""Console rendering for ``repowise init`` — analysis + completion panels.

Pure presentation: every function takes a finished :class:`PipelineResult` (or
workspace tallies) and prints Rich panels. No persistence or generation work
happens here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, NamedTuple

import structlog

from repowise.cli.helpers import console, load_config, load_state
from repowise.cli.ui import (
    build_contextual_next_steps,
    format_elapsed,
    mascot,
)
from repowise.cli.ui.finish_panel import (
    FinishFacts,
    ModelUpgrade,
    StoodOut,
    print_finish_panel,
)
from repowise.cli.ui.mode_names import pages, provider_model
from repowise.cli.ui.workspace_finish import (
    RepoOutcome,
    WorkspaceFacts,
    print_workspace_finish_panel,
)

logger = structlog.get_logger(__name__)


def _dead_counts(result: Any) -> tuple[int, int]:
    findings = result.dead_code_report.findings if result.dead_code_report else []
    kinds = [f.kind.value for f in findings]
    return kinds.count("unreachable_file"), kinds.count("unused_export")


def _lang_summary(result: Any) -> str:
    dist = result.repo_structure.root_language_distribution
    if not dist:
        return ""
    top = sorted(dist.items(), key=lambda x: -x[1])[:4]
    summary = ", ".join(f"{lang} {pct:.0%}" for lang, pct in top)
    if len(dist) > 4:
        summary += f" +{len(dist) - 4} more"
    return summary


def _hotspots(result: Any) -> int:
    summary = result.git_summary
    return summary.hotspots if summary and hasattr(summary, "hotspots") else 0


def _render_defect_accuracy(result: Any) -> None:
    """Print a one-line "does the score find the bugs?" validation callout.

    Stays silent unless the health analysis ran and the repo has enough files
    and recent bug-fix history for an honest number (the core compute returns
    ``None`` otherwise).
    """
    report = getattr(result, "health_report", None)
    if report is None:
        return
    try:
        from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
        from repowise.core.analysis.health.ranking import deduction_by_path

        stat = compute_defect_accuracy(
            report.metrics,
            report.findings,
            deductions=deduction_by_path(report.findings),
        )
    except Exception:
        return
    if not stat:
        return

    months = max(1, round(stat["window_days"] / 30))
    window = "month" if months == 1 else f"{months} months"
    line = (
        f"  [dim]Health check: [/dim]"
        f"[bold]{stat['hits']}/{stat['k']}[/bold]"
        f"[dim] lowest-health files had a bug fix in the last {window}[/dim]"
    )
    if stat.get("lift") is not None:
        base_pct = round(stat["base_rate"] * 100)
        line += f"[dim], [/dim][bold]{stat['lift']}x[/bold][dim] the {base_pct}% baseline.[/dim]"
    else:
        line += "[dim].[/dim]"
    console.print(line)
    console.print()


#: From this many files, the hosted pitch is staying fresh without a long local
#: run. Lower than the fast-mode offer on purpose: a hint costs nothing to skip.
_LARGE_REPO_FILES = 1000


def completion_hint(run_mode: str, file_count: int, first_index: bool = False) -> str | None:
    """Which hint a finished init earns: big repos hear about freshness.

    A first index ends on its own findings and tour, so it earns no
    ``init_success`` hint.
    """
    if run_mode == "fast" or file_count >= _LARGE_REPO_FILES:
        return "large_repo"
    return None if first_index else "init_success"


class Completion(NamedTuple):
    """What the finish panel showed, for the ``--progress json`` summary."""

    steps: list[tuple[str, str]]
    stood_out: StoodOut
    model_upgrade: ModelUpgrade | None


def show_completion(
    *,
    repo_path: Any,
    result: Any,
    start: float,
    effective_index_only: bool,
    run_mode: str,
    provider: Any,
    setup: Any = None,
    files_written: list[Path] | None = None,
    verbose: bool = False,
    owl: str = mascot.DONE,
) -> Completion:
    """Render the one finish panel and return what it showed; generation checks only under ``-v``.

    *owl* is the mini owl state on the panel title: how the run finished.

    *setup* is the :class:`~repowise.cli.editor_setup.EditorSetupOutcome`
    snapshot and *files_written* what editor setup put in the working tree,
    so the panel reports what setup actually did. Its ``first_index`` also
    keeps the ``init_success`` hint off a first index.
    """
    from repowise.core.index_scope import resolve_index_scope

    repo_path = Path(repo_path)
    scope = resolve_index_scope(load_state(repo_path), load_config(repo_path))
    graph = result.graph_builder.graph()
    dead_unreachable, dead_unused = _dead_counts(result)
    # A most-committed file is only worth naming when git analysis calls it a hotspot.
    hotspot = _top_hotspot(result) if _hotspots(result) else None
    top_hotspot = hotspot[0].rsplit("/", 1)[-1] if hotspot else ""
    stood_out = _stood_out(result, repo_path, hotspot)
    # Fast mode has its own keyless next step: the full index, before any model.
    keyless_docs = effective_index_only and run_mode != "fast"
    documentation, spend = (
        _template_pages(result) if effective_index_only else _model_pages(result)
    )
    facts = FinishFacts(
        scope=scope,
        files=result.file_count,
        symbols=result.symbol_count,
        languages=_lang_summary(result) or str(len(getattr(result, "languages", []))),
        graph_nodes=graph.number_of_nodes(),
        graph_edges=graph.number_of_edges(),
        elapsed=format_elapsed(time.monotonic() - start),
        top_hotspot=top_hotspot,
        pages=documentation,
        spend=spend,
        embed_failed=getattr(result, "embed_failed_pages", 0),
        stood_out=stood_out,
        model_upgrade=_model_upgrade(repo_path) if keyless_docs else None,
    )
    steps = build_contextual_next_steps(
        fast_mode=(run_mode == "fast"),
        dead_unreachable=dead_unreachable,
        dead_unused=dead_unused,
        hotspot_count=_hotspots(result),
        decision_count=stood_out.decision_candidates or 0,
        top_hotspot=top_hotspot,
        setup=setup,
    )
    console.print()
    print_finish_panel(
        console,
        facts,
        steps=steps,
        setup=setup,
        repo_path=repo_path,
        files_written=files_written,
        owl=owl,
    )
    console.print()
    _render_defect_accuracy(result)

    # One quiet stderr line about repowise.dev (see hints.py for when it is
    # shown); a big repo hears about staying fresh instead.
    from repowise.cli.hints import maybe_hint

    hint = completion_hint(run_mode, result.file_count, bool(getattr(setup, "first_index", False)))
    if hint:
        maybe_hint(hint)

    if verbose:
        _show_generation_checks(result)
    return Completion(steps, stood_out, facts.model_upgrade)


def _top_hotspot(result: Any) -> tuple[str, int] | None:
    """The most-committed file and its commit count, for the findings, risk step and question."""
    if not result.git_meta_map:
        return None
    path, meta = max(result.git_meta_map.items(), key=lambda x: x[1].get("commit_count", 0))
    return path, int(meta.get("commit_count", 0) or 0)


def _stood_out(result: Any, repo_path: Path, hotspot: tuple[str, int] | None) -> StoodOut:
    """The finish panel's findings, each from data this run already has."""
    path, commits = hotspot or (None, None)
    return StoodOut(
        **_health_finding(result),
        **_dead_finding(result),
        hotspot_file=path,
        hotspot_commits=commits,
        decision_candidates=_decision_candidates(repo_path),
    )


def _health_finding(result: Any) -> dict[str, Any]:
    """Repo score, its mark (withheld when too little was analysed), and the lowest file."""
    report = getattr(result, "health_report", None)
    kpis = getattr(report, "kpis", None) or {}
    score = kpis.get("average_health")
    if not isinstance(score, (int, float)):
        return {}
    from repowise.core.analysis.health.grading import headline_band, mark_for

    graded = headline_band(float(score), kpis.get("analysed_share")) is not None
    return {
        "health_score": float(score),
        "health_mark": mark_for(float(score)) if graded else None,
        "lowest_file": kpis.get("worst_performer_path"),
        "lowest_score": kpis.get("worst_performer_score"),
    }


def _dead_finding(result: Any) -> dict[str, Any]:
    """Unreachable files, and the findings that pass the deletion gate.

    A whole unreachable file is review-only and never passes the gate, so the
    safe count is over every finding, not over those files.
    """
    report = getattr(result, "dead_code_report", None)
    if report is None:
        return {}
    from repowise.core.analysis.dead_code.serving import is_safe

    findings = report.findings
    return {
        "dead_unreachable": sum(1 for f in findings if f.kind.value == "unreachable_file"),
        "dead_safe": sum(1 for f in findings if is_safe(f)),
    }


def _decision_candidates(repo_path: Path) -> int | None:
    """The decision layer's own candidate count from the store; None when it cannot be read."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.cli.helpers import run_async

    try:
        return run_async(_count_candidates(repo_path))
    except (SQLAlchemyError, OSError):
        # A missing or unreadable store: the row is left out, never shown as 0.
        return None
    except Exception:
        # Anything else is a bug in the count. Logged, and the row is still
        # left out so the finish panel never fails on it.
        logger.exception("decision_candidate_count_failed", repo=str(repo_path))
        return None


async def _count_candidates(repo_path: Path) -> int | None:
    from sqlalchemy import select

    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud.authority import count_decisions_by_lane
    from repowise.core.persistence.models import Repository

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo_id = (
                await session.execute(
                    select(Repository.id).where(Repository.local_path == str(repo_path))
                )
            ).scalar_one_or_none()
            if repo_id is None:
                return None
            return (await count_decisions_by_lane(session, repo_id))["candidates"]
    finally:
        await engine.dispose()


def _model_upgrade(repo_path: Path) -> ModelUpgrade:
    """The command that adds a model, for the source this machine already has.

    A signed-in agent CLI first, then a key, else the bare command. A sign-in
    is never called free: it does not prove a plan.
    """
    from repowise.core.index_scope import MODEL_COMMAND

    source = _signed_in_agent() or _keyed_provider(repo_path)
    if source is None:
        return ModelUpgrade(MODEL_COMMAND)
    name, line = source
    return ModelUpgrade(f"{MODEL_COMMAND} --provider {name}", line)


def _signed_in_agent() -> tuple[str, str] | None:
    """``(provider id, source line)`` for the first signed-in agent CLI worth offering."""
    from repowise.cli.ui.subscription import logged_in_agent_providers
    from repowise.core.agents.identity import identity_for_provider

    for name, auth in logged_in_agent_providers().items():
        agent = identity_for_provider(name)
        if auth.offer and agent is not None:
            return name, f"Uses your {agent.display_name} sign-in"
    return None


def _keyed_provider(repo_path: Path) -> tuple[str, str] | None:
    """``(provider id, source line)`` for the first provider a key or local model is set for."""
    from repowise.core.providers.llm.resolve import credentialed_providers
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    for name in credentialed_providers(repo_path):
        spec = PROVIDER_SPECS.get(name)
        if spec is None or spec.agent or name == "mock":
            continue
        if spec.local:
            return name, f"Uses {spec.label.removesuffix(' (Local)')} on this machine"
        return name, f"Uses your {spec.label} key"
    return None


def _template_pages(result: Any) -> tuple[str | None, None]:
    count = len(result.generated_pages or [])
    return (pages(count) if count else None), None


def _model_pages(result: Any) -> tuple[str, str]:
    """Pages and spend for a model run; a stub left by a failed call counts as failed."""
    from repowise.core.generation.models import count_stub_fallbacks

    generated = result.generated_pages or []
    failed = getattr(result, "failed_page_ids", []) or []
    written = len(generated) - count_stub_fallbacks(generated)
    from_code = sum(1 for p in generated if getattr(p, "provider_name", "") == "template")
    label = pages(written)
    if failed:
        label += f" ({len(failed)} failed)"
    elif from_code:
        label += f" ({len(generated) - from_code:,} written by the model · "
        label += f"{from_code:,} built from code)"
    tokens = sum(p.total_tokens for p in generated)
    cost = getattr(result, "llm_cost_usd", None)
    spend = f"${cost:.2f} ({tokens:,} tokens)" if cost is not None else f"{tokens:,} tokens"
    return label, spend


def _show_generation_checks(result: Any) -> None:
    """The quality checks on the pages this run wrote (``-v`` only)."""
    try:
        from repowise.core.generation.report import GenerationReport, render_generation_checks

        render_generation_checks(GenerationReport.from_pages(result.generated_pages or []), console)
    except Exception as exc:
        # The run still exits 0 and the documentation is written.
        console.print(f"  [dim]Generation checks were skipped ({type(exc).__name__}: {exc}).[/dim]")


def show_workspace_completion(
    *,
    selected: list[Any],
    errors: list,
    total_files: int,
    total_symbols: int,
    primary_alias: str,
    elapsed: float,
    index_only: bool,
    provider: Any,
    docs_outcomes: dict[str, tuple[int, str | None]],
    dry_run: bool = False,
    setup: Any = None,
) -> None:
    """Render the workspace finish panel, the per-repo documentation inside it."""
    from repowise.core.index_scope import resolve_index_scope

    failed = dict(errors)
    repos = []
    for repo in selected:
        if repo.alias in failed:
            repos.append(RepoOutcome(repo.alias, error=failed[repo.alias]))
            continue
        count, skipped = docs_outcomes.get(repo.alias, (0, None))
        scope = resolve_index_scope(load_state(repo.path), load_config(repo.path))
        repos.append(RepoOutcome(repo.alias, scope, pages=count, skipped=skipped))
    facts = WorkspaceFacts(
        repos=repos,
        files=total_files,
        symbols=total_symbols,
        primary=primary_alias,
        dry_run=dry_run,
        elapsed=format_elapsed(elapsed),
        provider=None
        if index_only or provider is None
        else provider_model(provider.provider_name, provider.model_name),
    )
    console.print()
    print_workspace_finish_panel(
        console, facts, setup=setup, owl=mascot.WARNED if errors else mascot.DONE
    )
    console.print()
