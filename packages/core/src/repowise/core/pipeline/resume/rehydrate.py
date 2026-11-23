"""Reconstruct phase *inputs* from persisted DB rows for a resumed run.

The graph is rehydrated by the existing :func:`rehydrate_graph_builder`
(re-exported here for a single resume import surface). This module adds the
git side: turning persisted ``git_metadata`` rows back into the
``git_meta_map`` dict that the analysis phases consume.

Caveat — the transient per-line ``BlameIndex`` is intentionally never
persisted (see ``phases/git.py: drop_transient_git_signals``), so a
rehydrated ``git_meta_map`` carries every durable signal but no
``blame_index``. Blame-dependent biomarkers treat its absence as "no
signal" — which is exactly the case for FAST/ESSENTIAL-tier runs that
never computed blame in the first place. Split File's per-function commit sets
are the exception: :func:`attach_stored_commit_shas` puts the stored ones back.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import structlog

from ..upgrade import rehydrate_graph_builder

logger = structlog.get_logger(__name__)

__all__ = [
    "attach_commit_set_blame",
    "attach_stored_commit_shas",
    "rehydrate_dead_code_report",
    "rehydrate_decision_report",
    "rehydrate_git_meta_map",
    "rehydrate_graph_builder",
    "rehydrate_health_report",
    "stored_git_meta_for",
]


async def rehydrate_git_meta_map(session: Any, repo_id: str) -> dict[str, dict[str, Any]]:
    """Rebuild ``{file_path -> git metadata dict}`` from persisted rows.

    Returns an empty dict when nothing was persisted (so a caller can detect
    "git was never indexed" and fall back to recomputing).
    """
    from repowise.core.persistence import get_all_git_metadata
    from repowise.core.persistence.crud import git_metadata_dict

    try:
        rows = await get_all_git_metadata(session, repo_id)
    except Exception as exc:
        logger.debug("rehydrate_git_meta_failed", error=str(exc))
        return {}
    return {path: git_metadata_dict(row) for path, row in rows.items()}


async def stored_git_meta_for(
    session: Any, repo_id: str, paths: list[str]
) -> dict[str, dict[str, Any]]:
    """The persisted git rows of *paths*, as :func:`rehydrate_git_meta_map` shapes them.

    A path with no row is absent. Unlike the full rehydrate, a failed read
    raises: the caller has to tell "no history" from "could not read it".
    """
    from repowise.core.persistence import get_git_metadata_bulk
    from repowise.core.persistence.crud import git_metadata_dict

    rows = await get_git_metadata_bulk(session, repo_id, paths)
    return {path: git_metadata_dict(row) for path, row in rows.items()}


async def attach_stored_commit_shas(
    session: Any, repo_id: str, git_meta_map: dict[str, dict[str, Any]]
) -> None:
    """Put each file's stored per-function commit sets under
    ``"function_commit_shas"``, where a score without a blame index reads them.

    Only files already in *git_meta_map* get them. Best effort: a failed read
    leaves the map as it was.
    """
    from repowise.core.persistence.crud import get_function_commit_shas

    try:
        stored = await get_function_commit_shas(session, repo_id)
    except Exception as exc:
        logger.debug("stored_commit_shas_failed", error=str(exc))
        return
    for path, entries in stored.items():
        meta = git_meta_map.get(path)
        if meta is not None:
            meta["function_commit_shas"] = entries


def attach_commit_set_blame(
    repo_path: Any,
    git_meta_map: dict[str, dict[str, Any]],
    parsed_files: list[Any],
    *,
    git_tier: str | None,
) -> int:
    """Blame the Split File candidates that have no stored commit sets, under
    ``"commit_set_blame"``, so a re-score mints their plans as a full index
    would. Returns the number of files blamed.

    Rows written before the sets existed carry none, so without this the first
    re-score after an upgrade mints ids that move again at the next index. Only
    what an index would have blamed qualifies: a FULL-tier repo, a file with
    enough commits to keep its blame index, and one Split File could act on.
    Once the rows are written back the files are skipped, so the cost is paid
    once.
    """
    from repowise.core.analysis.health.refactoring.split_file import may_split
    from repowise.core.ingestion.git_indexer import GitIndexer
    from repowise.core.ingestion.git_indexer.function_blame import _MIN_COMMITS_FOR_BLAME
    from repowise.core.ingestion.git_indexer.tiers import GitIndexTier

    try:
        if git_tier and not GitIndexTier(git_tier).includes_blame:
            return 0
    except ValueError:
        pass
    paths: list[str] = []
    for pf in parsed_files:
        path = pf.file_info.path
        meta = git_meta_map.get(path)
        if (
            meta is None
            or "function_commit_shas" in meta
            or (meta.get("commit_count_total") or 0) < _MIN_COMMITS_FOR_BLAME
        ):
            continue
        top_level = sum(
            1 for s in pf.symbols if s.kind in ("class", "function") and not s.parent_name
        )
        try:
            line_count = Path(pf.file_info.abs_path).read_bytes().count(b"\n") + 1
        except OSError:
            continue
        if may_split(path, pf.file_info.language, line_count, top_level):
            paths.append(path)
    try:
        blamed = GitIndexer(repo_path).blame_indexes(paths)
    except Exception as exc:
        logger.debug("commit_set_blame_failed", error=str(exc))
        return 0
    for path, idx in blamed.items():
        git_meta_map[path]["commit_set_blame"] = idx
    return len(blamed)


# ---------------------------------------------------------------------------
# Analysis reports (generation INPUT only — never re-persisted)
# ---------------------------------------------------------------------------
#
# These thin views carry exactly the fields the page generator reads off a
# dead-code / decision report (see ``page_generator/helpers.py``:
# ``build_dead_code_map`` / ``build_decision_maps``) — nothing more. They are
# deliberately *not* the analysis dataclasses: a resumed run feeds them into
# generation but must never write them back (the persisted rows are already
# authoritative), so they skip ``deletable_lines`` / ``by_source`` and the
# other persist/summary-only fields a faithful round-trip would need.


async def rehydrate_dead_code_report(session: Any, repo_id: str) -> Any:
    """Rebuild a generation-shaped dead-code report from persisted findings.

    Returns a namespace with a ``findings`` list (empty when the completed
    analysis legitimately found none). Raises on a DB error so the caller can
    fall back to recomputing.
    """
    from repowise.core.persistence.crud import get_dead_code_findings

    rows = await get_dead_code_findings(session, repo_id, status="open")
    findings = [
        SimpleNamespace(
            file_path=r.file_path,
            symbol_name=r.symbol_name,
            symbol_kind=r.symbol_kind,
            kind=r.kind,  # stored as the clean string label, e.g. "unused_export"
            reason=r.reason,
            confidence=r.confidence,
            safe_to_delete=r.safe_to_delete,
        )
        for r in rows
    ]
    return SimpleNamespace(findings=findings)


async def rehydrate_health_report(session: Any, repo_id: str) -> Any:
    """Rebuild the whole-repo ``HealthReport`` from the stored health rows.

    For a caller that wants the report a full re-score would return, after an
    update that scored only the changed files: the store already holds the
    merged repository. Carries every metric row, the open findings, the open
    refactoring suggestions (performance plans included), the KPIs computed
    the way the analyzer computes them, and the stored function-mod p80.

    What it cannot carry: findings and plans a person triaged (they are not
    ``open``), each plan's ``validation`` (never stored; detectors leave it
    empty), and the write-side inputs (``function_facts``, ``function_tests``,
    ``execution_roles``, ``performance_plan_policy``, ``function_blame_rows``,
    coverage, authoritative paths), which are already persisted in their own
    tables. So the report is for reading, never for writing back.

    Returns ``None`` when no file has been scored. Raises on a DB error.
    """
    from dataclasses import fields
    from datetime import UTC, datetime

    from sqlalchemy import select

    from repowise.core.analysis.health.models import (
        HealthFileMetricData,
        HealthFindingData,
        HealthReport,
        Severity,
    )
    from repowise.core.analysis.health.ranking import deduction_by_path
    from repowise.core.analysis.health.refactoring.recommendations import rehydrate_suggestion
    from repowise.core.analysis.health.scoring import compute_kpis
    from repowise.core.persistence.crud import get_hotspot_file_paths
    from repowise.core.persistence.models import (
        HealthFileMetric,
        HealthFinding,
        RefactoringSuggestion,
        Repository,
    )

    async def rows(model: Any, *where: Any) -> list[Any]:
        query = select(model).where(model.repository_id == repo_id, *where)
        return list((await session.execute(query)).scalars().all())

    metric_rows = await rows(HealthFileMetric)
    if not metric_rows:
        return None
    metric_fields = [f.name for f in fields(HealthFileMetricData)]
    metrics = [
        HealthFileMetricData(**{name: getattr(row, name) for name in metric_fields})
        for row in metric_rows
    ]
    findings = [
        HealthFindingData(
            biomarker_type=row.biomarker_type,
            severity=Severity(row.severity),
            file_path=row.file_path,
            function_name=row.function_name,
            line_start=row.line_start,
            line_end=row.line_end,
            details=json.loads(row.details_json or "{}"),
            health_impact=row.health_impact,
            reason=row.reason or "",
            dimension=row.dimension or "defect",
        )
        for row in await rows(HealthFinding, HealthFinding.status == "open")
    ]
    suggestions = [
        rehydrate_suggestion(row)
        for row in await rows(RefactoringSuggestion, RefactoringSuggestion.status == "open")
    ]
    repo = await session.get(Repository, repo_id)
    # SQLite hands timestamps back naive; they were written in UTC.
    stamps = [
        row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=UTC)
        for row in metric_rows
        if row.updated_at is not None
    ]
    return HealthReport(
        repo_id=repo_id,
        analyzed_at=max(stamps) if stamps else datetime.now(UTC),
        findings=findings,
        metrics=metrics,
        kpis=compute_kpis(
            metrics,
            await get_hotspot_file_paths(session, repo_id),
            deductions=deduction_by_path(findings),
        ),
        repo_function_mod_p80=getattr(repo, "function_mod_p80", None),
        refactoring_suggestions=suggestions,
    )


async def rehydrate_decision_report(session: Any, repo_id: str) -> Any:
    """Rebuild a generation-shaped decision report from persisted records.

    Returns a namespace with a ``decisions`` list (empty when none were
    recorded). Raises on a DB error so the caller can fall back to recomputing.
    """
    from repowise.core.persistence.crud import list_decisions

    rows = await list_decisions(session, repo_id, limit=100_000)
    decisions = []
    for r in rows:
        try:
            affected = json.loads(r.affected_files_json or "[]")
        except Exception:
            affected = []
        decisions.append(
            SimpleNamespace(
                title=r.title,
                decision=r.decision,
                rationale=r.rationale,
                source=r.source,
                confidence=r.confidence,
                evidence_file=r.evidence_file,
                affected_files=affected,
                # Carried so the resumed run ranks and filters decisions the
                # same way a fresh one does. Without it every rehydrated
                # record read as a proposal, including the accepted ones.
                status=r.status,
                # Same reason: the per-file index reads this and fails
                # open without it, so a resumed run would differ from a fresh
                # one.
                scope_basis=r.scope_basis,
            )
        )
    return SimpleNamespace(decisions=decisions)
