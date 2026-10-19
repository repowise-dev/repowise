"""The decision passes a docs update runs before regenerating pages.

Each is best-effort and shares one ``decisions`` timing row; a failure lands in
the run's degraded list instead of failing the update.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from repowise.cli.helpers import console, run_async
from repowise.core.pipeline import timed

from .incremental import _build_update_vector_store
from .options import DocsPass, UpdateOptions, UpdateRun

# Wider than the update cadence: a late firing is re-judged once later calls exist.
_EFFICACY_LOOKBACK_SECONDS = 7 * 86400.0


def run_decision_passes(opts: UpdateOptions, run: UpdateRun, docs: DocsPass) -> None:
    """Run every decision pass in order; fills the decision fields of *docs*."""
    run.timings.start("decisions")

    from repowise.core.analysis.decisions.policy import resolve_policy

    policy = resolve_policy(run.cfg).policy

    # A history-window change re-runs git archaeology; its failure must fail the
    # run, or the new fingerprint would suppress the only retry.
    if run.config.git_changed and policy.source_enabled("git_archaeology"):
        _rerun_git_archaeology(run, docs, policy)

    run.timings.start("decisions.rescan")
    _rescan_inline_markers(opts, run, docs, policy)
    run.timings.stop("decisions.rescan")

    run.timings.start("decisions.sessions")
    _run_session_passes(opts, run, docs, policy)
    run.timings.stop("decisions.sessions")

    # Shared by evolution and the decision upsert.
    with timed(run.timings, "decisions.vector_store"):
        docs.decision_vector_store = _build_update_vector_store(
            run.repo_path,
            run.cfg,
            run.degraded,
            required=(run.config.traversal_changed or run.config.generation_changed),
        )

    # Before generation: evolved decisions queue their governed pages for this run.
    run.timings.start("decisions.evolution")
    _evolve_decisions(opts, run, docs)
    run.timings.stop("decisions.evolution")
    run.timings.stop("decisions")


def _rerun_git_archaeology(run: UpdateRun, docs: DocsPass, policy: Any) -> None:
    try:
        from repowise.core.analysis.decision_extractor import DecisionExtractor

        history_extractor = DecisionExtractor(
            repo_path=run.repo_path,
            provider=docs.provider,
            graph=run.graph_builder.graph(),
            git_meta_map=run.git_meta_map,
            parsed_files=run.parsed_files,
            source_map=run.source_map,
            policy=policy,
        )
        with docs.cost_tracker.record_as("decision_extraction"):
            history_report = run_async(
                history_extractor.extract_all(enabled_sources={"git_archaeology"})
            )
        if history_report.failures:
            details = "; ".join(
                f"{source}: {error}" for source, error in sorted(history_report.failures.items())
            )
            raise RuntimeError(details)
        docs.new_decision_markers.extend(history_report.decisions)
    except Exception as exc:
        raise RuntimeError(
            "Configuration-triggered Git decision extraction failed; the previous "
            "fingerprint was retained so the next update retries."
        ) from exc


def _rescan_inline_markers(
    opts: UpdateOptions, run: UpdateRun, docs: DocsPass, policy: Any
) -> None:
    try:
        from repowise.core.analysis.decision_extractor import DecisionExtractor

        changed_paths = [fd.path for fd in run.file_diffs if fd.status in ("added", "modified")]
        # Reads uncommitted files on purpose: a marker is source text, not history.
        if changed_paths and policy.source_enabled("inline_marker"):
            extractor = DecisionExtractor(
                repo_path=run.repo_path,
                provider=docs.provider,
                graph=run.graph_builder.graph(),
                git_meta_map=run.git_meta_map,
                policy=policy,
            )
            with docs.cost_tracker.record_as("decision_extraction"):
                docs.new_decision_markers.extend(
                    run_async(extractor.scan_inline_markers(restrict_to_files=changed_paths))
                )
            if docs.new_decision_markers and opts.verbose:
                console.print(
                    f"New decision markers found: [green]{len(docs.new_decision_markers)}[/green]"
                )
    except Exception as exc:
        _skipped(opts, run, "Decision re-scan", "Decision re-scan skipped", exc)


def _skipped(opts: UpdateOptions, run: UpdateRun, label: str, verbose_label: str, exc) -> None:
    """Record a best-effort pass that failed, and say so under ``--verbose``."""
    run.degraded.append(f"{label}: {exc}")
    if opts.verbose:
        console.print(f"[yellow]{verbose_label}: {exc}[/yellow]")


def _run_session_passes(
    opts: UpdateOptions, run: UpdateRun, docs: DocsPass, policy: Any
) -> None:
    # The indexed file set bounds which paths a session-mined record may govern.
    indexed_files = frozenset(run.source_map) if run.source_map else None
    _mine_session_decisions(opts, run, docs, policy, indexed_files)
    # Second: the mining pass is what fills discovery's queue.
    _discover_session_decisions(opts, run, docs, policy, indexed_files)
    _apply_injection_feedback(opts, run, policy)
    _classify_hook_efficacy(opts, run, policy)


def _mine_session_decisions(
    opts: UpdateOptions, run: UpdateRun, docs: DocsPass, policy: Any, indexed_files
) -> None:
    try:
        from repowise.core.sessions.miners.decisions import mine_session_decisions

        if policy.source_enabled("session"):
            session_provider = docs.provider if policy.llm_allowed("session") else None
            docs.session_decisions = run_async(
                mine_session_decisions(
                    run.repo_path,
                    provider=session_provider,
                    harnesses=policy.harnesses,
                    collect_discovery_spans=policy.llm_allowed("session_discovery"),
                    indexed=indexed_files,
                )
            )
            if docs.session_decisions and opts.verbose:
                promoted_titles = {d.title for d in docs.session_decisions}
                console.print(f"Session decisions promoted: [green]{len(promoted_titles)}[/green]")
    except Exception as exc:
        _skipped(opts, run, "Session decision mining", "Session decision mining skipped", exc)


def _discover_session_decisions(
    opts: UpdateOptions, run: UpdateRun, docs: DocsPass, policy: Any, indexed_files
) -> None:
    try:
        from repowise.core.analysis.decisions.discovery import run_update_discovery

        with docs.cost_tracker.record_as("decision_extraction"):
            outcome = run_async(
                run_update_discovery(
                    run.repo_path,
                    provider=docs.provider,
                    policy=policy,
                    indexed=indexed_files,
                )
            )
        docs.session_decisions = [*docs.session_decisions, *outcome.decisions]
        if outcome.report.status == "failed":
            run.degraded.append(f"Session discovery: {outcome.report.reason}")
        if opts.verbose:
            funnel = outcome.report
            console.print(
                f"Session discovery: {funnel.status}"
                + (f" ({funnel.reason})" if funnel.reason else "")
                + f" · {funnel.calls} call(s) · {funnel.candidates_grounded} grounded"
                + f" · {funnel.spans_deferred} span(s) queued"
            )
    except Exception as exc:
        _skipped(opts, run, "Session discovery", "Session discovery skipped", exc)


def _apply_injection_feedback(opts: UpdateOptions, run: UpdateRun, policy: Any) -> None:
    """Judge injected decisions against their sessions' corrections (no model)."""
    # Gated on the session lane: its mined corrections are the only evidence, and
    # an evaluation is terminal, so judging with the lane off would settle every
    # row as unjudgeable for good.
    try:
        from repowise.core.sessions.miners.decisions import apply_injection_feedback

        if policy.source_enabled("session"):
            feedback = run_async(_injection_feedback(run.repo_path, apply_injection_feedback))
            if opts.verbose and (feedback.get("followed") or feedback.get("contradicted")):
                console.print(
                    f"Injected-decision feedback: [green]{feedback.get('followed', 0)} "
                    f"followed[/green], [yellow]{feedback.get('contradicted', 0)} "
                    "contradicted[/yellow]"
                )
    except Exception as exc:
        _skipped(opts, run, "Injection feedback", "Injection feedback skipped", exc)


async def _injection_feedback(repo_path: Path, apply_injection_feedback) -> dict:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        res = await apply_injection_feedback(session, repo.id, repo_path)
    await engine.dispose()
    return res


def _classify_hook_efficacy(opts: UpdateOptions, run: UpdateRun, policy: Any) -> None:
    """Classify hook firings by what the agent did next (reads transcripts)."""
    # The session lane is the privacy boundary for reading transcripts, so this
    # fails closed; `repowise hook backfill` fills the columns on demand.
    try:
        from repowise.core.sessions.efficacy import ingest_transcript_efficacy

        if policy.source_enabled("session"):
            classified = ingest_transcript_efficacy(
                run.repo_path, since=time.time() - _EFFICACY_LOOKBACK_SECONDS
            )
            if opts.verbose and classified:
                console.print(f"Hook firings classified: [green]{sum(classified.values())}[/green]")
    except Exception as exc:
        _skipped(opts, run, "Hook efficacy", "Hook efficacy classification skipped", exc)


def _evolve_decisions(opts: UpdateOptions, run: UpdateRun, docs: DocsPass) -> None:
    try:
        evo = _run_decision_evolution(
            run.repo_path,
            run.file_diffs,
            run.git_meta_map,
            base_ref=run.base_ref,
            head=run.head,
            provider=docs.provider,
            cost_tracker=docs.cost_tracker,
            max_concurrency=docs.config.max_concurrency,
        )
        if evo is not None:
            _apply_evolution(opts, run, docs, evo)
    except Exception as exc:
        _skipped(opts, run, "Decision evolution", "Decision evolution skipped", exc)


def _apply_evolution(opts: UpdateOptions, run: UpdateRun, docs: DocsPass, evo: dict) -> None:
    docs.decisions_evolved = (
        evo.get("superseded", 0) + evo.get("amended", 0) + evo.get("reaffirmed", 0)
    )
    evo_regen = evo.get("regen_files") or set()
    if not evo_regen:
        return
    # A file page's id is its path.
    affected = run.affected
    affected.regenerate = list(dict.fromkeys([*affected.regenerate, *sorted(evo_regen)]))
    if opts.verbose:
        console.print(
            f"Decision evolution: [cyan]{evo.get('superseded', 0)} superseded[/cyan], "
            f"[cyan]{evo.get('amended', 0)} amended[/cyan], "
            f"[green]{evo.get('reaffirmed', 0)} reaffirmed[/green]; "
            f"+{len(evo_regen)} governed page(s) queued for regen."
        )


def _run_decision_evolution(
    repo_path: Path,
    file_diffs: list,
    git_meta_map: dict,
    *,
    base_ref: str,
    head: str | None,
    provider: Any,
    cost_tracker: Any,
    max_concurrency: int,
) -> dict | None:
    """Run decision evolution over ``base_ref..head``, or ``None`` with no changed file."""
    changed_set = {fd.path for fd in file_diffs if fd.status in ("added", "modified")}
    if not changed_set:
        return None

    from repowise.core.analysis.decision_evolution import (
        commits_in_range,
        run_update_evolution,
        update_evidence_by_file,
    )

    evidence_by_file = update_evidence_by_file(
        file_diffs, git_meta_map, commits_in_range(repo_path, base_ref, head)
    )

    async def _run() -> dict:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )

        engine = create_engine(get_db_url_for_repo(repo_path))
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
            res = await run_update_evolution(
                session,
                repo.id,
                changed_files=changed_set,
                evidence_by_file=evidence_by_file,
                provider=provider,
                max_concurrency=max_concurrency,
            )
        await engine.dispose()
        return res

    with cost_tracker.record_as("decision_extraction"):
        return run_async(_run())
