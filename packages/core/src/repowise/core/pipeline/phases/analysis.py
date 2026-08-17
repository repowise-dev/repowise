"""Pipeline analysis phase.

Extracted from the former monolithic ``orchestrator.py``; ``run_pipeline`` (in
orchestrator.py) imports these phase functions. No CLI/click/rich imports.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from repowise.core.pipeline.progress import ProgressCallback

from ._common import _phase_done

logger = structlog.get_logger(__name__)


# Maximum seconds to spend on decision extraction before giving up.
# Large repos with tens of thousands of files can take arbitrarily long.
DECISION_EXTRACTION_TIMEOUT_SECS = 300

# Where a decision came from, in report order, spelled the way a reader who has
# never seen the extractor would say it ("ADR" alone is unexplained jargon).
_DECISION_SOURCE_LABELS: tuple[tuple[str, str], ...] = (
    ("inline_marker", "from inline markers"),
    ("adr", "from ADR files"),
    ("pr", "from pull requests"),
    ("git_archaeology", "from git history"),
    ("comment", "from comments"),
    ("session", "from agent sessions"),
)


async def _run_dead_code_analysis(
    graph_builder: Any,
    git_meta_map: dict[str, dict],
    *,
    source_map: dict[str, bytes] | None = None,
    repo_path: Any | None = None,
    traversal_stats: Any | None = None,
    progress: ProgressCallback | None,
) -> Any | None:
    """Run dead code detection (pure graph traversal, no LLM)."""
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        # Four detectors run sequentially inside analyze(); drive a
        # determinate bar so users see progress instead of "0".
        dead_code_steps = 4
        if progress:
            progress.on_phase_start("dead_code", dead_code_steps)

        # Files the traverser dropped on size, plus those it dropped for having
        # no language spec. Without them the analyzer cannot tell "nothing
        # imports this" from "the importer was never read", which is the
        # cascade in #1237.
        unindexed_source_files = [
            (skipped.path, skipped.reason)
            for skipped in (
                *getattr(traversal_stats, "skipped_source_files", []),
                *getattr(traversal_stats, "unknown_language_files", []),
            )
        ]

        analyzer = DeadCodeAnalyzer(
            graph_builder.graph(),
            git_meta_map,
            parsed_files=graph_builder._parsed_files,
            source_map=source_map,
            repo_root=repo_path,
            unindexed_source_files=unindexed_source_files,
        )

        def _step(_stage: str) -> None:
            if progress:
                progress.on_item_done("dead_code")

        report = await asyncio.to_thread(analyzer.analyze, None, on_step=_step)

        if progress:
            unreachable = sum(1 for f in report.findings if f.kind.value == "unreachable_file")
            unused_exports = sum(1 for f in report.findings if f.kind.value == "unused_export")
            progress.on_message(
                "info",
                f"→ {unreachable} unreachable files · "
                f"{unused_exports} unused exports · ~{report.deletable_lines:,} deletable lines",
            )

        _phase_done(progress, "dead_code")
        return report
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Dead code detection skipped: {exc}")
        _phase_done(progress, "dead_code")
        return None


def _build_pipeline_coverage(
    repo_path: Path,
    parsed_files: list[Any],
    explicit_paths: list[Path] | None,
    *,
    progress: ProgressCallback | None,
) -> tuple[dict[str, dict], list[Any], str | None]:
    """Discover/parse/resolve coverage reports for an indexing run.

    Returns ``(coverage_map, resolved_files, source_format)``. Best-effort:
    any failure logs and yields an empty map so health analysis proceeds
    without coverage. Unmatched report files are surfaced via *progress* so
    "coverage didn't show up" is never silent.
    """
    try:
        from repowise.core.analysis.health.coverage import (
            CoverageConfig,
            build_coverage_map,
            discover_artifacts,
        )
        from repowise.core.repo_config import load_repo_config

        cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))

        if explicit_paths:
            report_paths = list(explicit_paths)
        elif cfg.paths:
            report_paths = [repo_path / p for p in cfg.paths if (repo_path / p).is_file()]
        elif cfg.auto_discover:
            report_paths = discover_artifacts(repo_path, globs=cfg.artifacts or None)
        else:
            return {}, [], None

        if not report_paths:
            return {}, [], None

        repo_keys = {pf.file_info.path for pf in parsed_files}
        resolved, errors = build_coverage_map(
            repo_path,
            report_paths,
            repo_keys,
            coverage_format=cfg.format,
            strip_prefix=cfg.strip_prefix,
            path_prefix=cfg.path_prefix,
        )

        if progress:
            names = ", ".join(p.name for p in report_paths[:3])
            more = f" (+{len(report_paths) - 3} more)" if len(report_paths) > 3 else ""
            progress.on_message(
                "info",
                f"→ coverage: {resolved.matched} files matched from {names}{more}",
            )
            skipped = len(resolved.unmatched) + len(resolved.ambiguous)
            if skipped:
                sample = ", ".join((resolved.unmatched + resolved.ambiguous)[:3])
                progress.on_message(
                    "warning",
                    f"  ↳ {skipped} report file(s) did not map to the repo tree "
                    f"(e.g. {sample}). Set coverage.strip_prefix in "
                    f".repowise/config.yaml if paths are off.",
                )
            for path, err in errors:
                progress.on_message("warning", f"  ↳ coverage {path.name}: {err}")

        return resolved.coverage_map, resolved.files, resolved.source_format
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Coverage ingestion skipped: {exc}")
        logger.debug("pipeline_coverage_failed", error=str(exc))
        return {}, [], None


async def _run_health_analysis(
    graph_builder: Any,
    git_meta_map: dict[str, dict],
    parsed_files: list[Any],
    *,
    repo_path: Path | None = None,
    coverage_report_paths: list[Path] | None = None,
    progress: ProgressCallback | None,
) -> Any | None:
    """Run code-health analysis (complexity + biomarkers + scoring)."""
    try:
        from repowise.core.analysis.health import HealthAnalyzer
        from repowise.core.analysis.health.config import HealthConfig

        if progress:
            # Two ticks per parsed file: one when its AST walk completes,
            # one when its biomarker evaluation completes. Without the
            # walk tick the bar sits at 0 through the duplication scan and
            # the entire pre-walk — most of the phase's wall-clock.
            progress.on_phase_start("health", 2 * len(parsed_files))

        # Build a {file_path → community label} map so per-file metrics
        # carry a real module name, not None. Community detection is
        # already computed for the graph view, so this is essentially
        # free.
        module_map: dict[str, str] = {}
        try:
            cd = graph_builder.community_detection()
            ci = graph_builder.community_info()
            for node_id, comm_id in cd.items():
                info = ci.get(comm_id)
                label = getattr(info, "label", None) if info else None
                if label:
                    module_map[node_id] = label
        except Exception as exc:
            logger.debug("health_module_map_failed", error=str(exc))

        # Ingest coverage (auto-discovered or explicitly passed) so biomarkers
        # see real line/branch coverage instead of the has_test_file fallback.
        coverage_map: dict[str, dict] = {}
        coverage_files: list[Any] = []
        coverage_format: str | None = None
        if repo_path is not None:
            coverage_map, coverage_files, coverage_format = _build_pipeline_coverage(
                repo_path, parsed_files, coverage_report_paths, progress=progress
            )

        analyzer = HealthAnalyzer(
            graph_builder.graph(),
            git_meta_map=git_meta_map,
            parsed_files=parsed_files,
            module_map=module_map,
            coverage_map=coverage_map,
            duplication_cache_dir=(repo_path / ".repowise") if repo_path is not None else None,
            repo_root=repo_path,
        )

        # Load per-file override rules from `.repowise/health-rules.json`.
        # Missing or malformed file → empty config (no-op).
        analyzer_config: dict[str, object] | None = None
        if repo_path is not None:
            cfg = HealthConfig.load(repo_path)
            if cfg.has_overrides():
                file_paths = [pf.file_info.path for pf in parsed_files]
                analyzer_config = cfg.to_analyzer_config(file_paths)

        def _step(_path: str) -> None:
            if progress:
                progress.on_item_done("health")

        # Parallel path for repos large enough to benefit (tree-sitter
        # releases the GIL during parsing, so asyncio.gather over a
        # thread pool actually scales). Threshold chosen so small repos
        # avoid the overhead.
        if len(parsed_files) >= 500:
            report = await analyzer.analyze_async(analyzer_config, on_step=_step)
        else:
            report = await asyncio.to_thread(analyzer.analyze, analyzer_config, on_step=_step)

        # Carry resolved coverage onto the report so the persister can write
        # the coverage_files table alongside the health metrics.
        if coverage_files:
            report.coverage_files = coverage_files
            report.coverage_format = coverage_format

        if progress:
            findings_count = len(report.findings)
            avg = report.kpis.get("average_health", 10.0)
            worst = report.kpis.get("worst_performer_score", 10.0)
            progress.on_message(
                "info",
                f"→ {findings_count} health findings · avg {avg}/10 · worst {worst}/10",
            )

        _phase_done(progress, "health")
        return report
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Health analysis skipped: {exc}")
        _phase_done(progress, "health")
        return None


async def _run_decision_extraction(
    repo_path: Path,
    *,
    llm_client: Any | None,
    graph_builder: Any,
    git_meta_map: dict[str, dict],
    parsed_files: list[Any],
    source_map: dict[str, bytes] | None = None,
    progress: ProgressCallback | None,
) -> Any | None:
    """Extract architectural decisions from source and git history."""
    try:
        from repowise.core.analysis.decision_extractor import (
            DecisionExtractor,
            enabled_source_names,
        )
        from repowise.core.repo_config import load_repo_config

        # Sources run concurrently inside extract_all(); drive a determinate
        # bar so users see live progress. ``decisions.sources`` in the repo
        # config can disable individual sources (#751).
        repo_cfg = load_repo_config(repo_path)
        enabled = enabled_source_names(repo_cfg)
        if progress:
            progress.on_phase_start("decisions", len(enabled))

        extractor = DecisionExtractor(
            repo_path=repo_path,
            provider=llm_client,
            graph=graph_builder.graph(),
            git_meta_map=git_meta_map,
            parsed_files=parsed_files,
            source_map=source_map,
        )

        def _decision_step(_source: str) -> None:
            if progress:
                progress.on_item_done("decisions")

        report = await asyncio.wait_for(
            extractor.extract_all(on_step=_decision_step, enabled_sources=enabled),
            timeout=DECISION_EXTRACTION_TIMEOUT_SECS,
        )

        # Session-sourced decisions: a repo indexed for the first time on a
        # machine with existing agent-session history gets them at init, not
        # only from the first update. Appended after extract_all's substring
        # gate on purpose: the miner enforces its own grounding contract, and
        # re-gating without a source_text would wipe its verification. A
        # server-side index has no transcript directory and no-ops here.
        try:
            from repowise.core.sessions.miners.decisions import (
                mine_session_decisions,
                session_mining_enabled,
            )

            # Not gated on a provider: the same pass records transcript
            # episodes, which need no model, and gating the read on a key
            # would leave a keyless index with no transcript supply at all.
            # The miner skips its own structuring pass when provider is None.
            if session_mining_enabled(repo_cfg):
                session_decisions = await asyncio.wait_for(
                    mine_session_decisions(repo_path, provider=llm_client),
                    timeout=DECISION_EXTRACTION_TIMEOUT_SECS,
                )
                if session_decisions:
                    report.decisions.extend(session_decisions)
                    report.by_source["session"] = len(session_decisions)
                    report.total_found = len(report.decisions)
        except Exception as exc:
            if progress:
                progress.on_message("warning", f"Session decision mining skipped: {exc}")

        if progress:
            # Only the sources that actually found something. On a typical
            # first index most of these are zero, and the line read as
            # "0 ADR · 0 changelog · 0 PR ·" with the real numbers buried.
            bs = report.by_source
            found = [
                f"{bs[source]} {label}"
                for source, label in _DECISION_SOURCE_LABELS
                if bs.get(source)
            ]
            summary = ", ".join(found) if found else ""
            progress.on_message(
                "info",
                f"→ {report.total_found} architectural decisions found"
                + (f": {summary}" if summary else ""),
            )
            # And the sources that produced nothing. Hiding these was what made
            # a source failing outright indistinguishable from a source with
            # honestly nothing to find: every failure inside the extractor is
            # swallowed and reported only through ``logger.warning``, which no
            # default CLI run renders. A repo with no ADR files legitimately
            # scores zero there, so this is not itself a failure report — it is
            # the list you need to have before you can tell the two apart.
            # The labels read "from git history" because they are normally
            # rendered as "5 from git history"; strung together after one
            # "No decisions" they would read "No decisions from inline
            # markers, from ADR files". Drop the preposition and name the
            # sources plainly.
            #
            # ``session`` is in the label table but is not one of the
            # extractor's own sources, so ``source in enabled`` leaves it out
            # of this list — correct, since it is mined separately below.
            #
            # A source that *failed* is listed separately and as a warning.
            # It is not "nothing found": the repo may be full of decisions
            # nobody could read. Same shape the update path already uses for
            # its ``degraded`` list, so both runs report an outage the same
            # way instead of one of them printing a reassuring zero.
            failures = getattr(report, "failures", {}) or {}
            empty = [
                label.removeprefix("from ")
                for source, label in _DECISION_SOURCE_LABELS
                if source in enabled and not bs.get(source) and source not in failures
            ]
            if empty:
                progress.on_message("info", f"→ Nothing found in: {', '.join(empty)}")
            for source, label in _DECISION_SOURCE_LABELS:
                if source in failures:
                    progress.on_message(
                        "warning",
                        f"Decision source {label.removeprefix('from ')} failed, "
                        f"so this run found none there: {failures[source]}",
                    )

        _phase_done(progress, "decisions")
        return report
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Decision extraction skipped: {exc}")
        _phase_done(progress, "decisions")
        return None
