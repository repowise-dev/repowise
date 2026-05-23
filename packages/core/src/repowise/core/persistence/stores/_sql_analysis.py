"""Analysis-domain delegations for :class:`SqlIndexStore`.

Git metadata, dead code, decision records, health, coverage — each method
delegates to :mod:`crud`. Split out to keep store files under 400 lines.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import crud
from .._interfaces._analysis import AnalysisIndexStore
from ..models import (
    CoverageFile,
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    HealthFileMetric,
    HealthFinding,
    HealthSnapshot,
)


class _SqlAnalysisMixin(AnalysisIndexStore):
    """Concrete delegations for the analysis IndexStore surface."""

    _session: AsyncSession

    async def upsert_git_metadata(
        self, *, repository_id: str, file_path: str, **kwargs: object
    ) -> GitMetadata:
        return await crud.upsert_git_metadata(
            self._session,
            repository_id=repository_id,
            file_path=file_path,
            **kwargs,
        )

    async def get_git_metadata(
        self, repository_id: str, file_path: str
    ) -> GitMetadata | None:
        return await crud.get_git_metadata(self._session, repository_id, file_path)

    async def get_git_metadata_bulk(
        self, repository_id: str, file_paths: list[str]
    ) -> dict[str, GitMetadata]:
        return await crud.get_git_metadata_bulk(
            self._session, repository_id, file_paths
        )

    async def get_all_git_metadata(
        self, repository_id: str
    ) -> dict[str, GitMetadata]:
        return await crud.get_all_git_metadata(self._session, repository_id)

    async def upsert_git_metadata_bulk(
        self, repository_id: str, metadata_list: list[dict]
    ) -> None:
        await crud.upsert_git_metadata_bulk(
            self._session, repository_id, metadata_list
        )

    async def recompute_git_percentiles(self, repository_id: str) -> int:
        return await crud.recompute_git_percentiles(self._session, repository_id)

    async def save_dead_code_findings(
        self, repository_id: str, findings: list[dict]
    ) -> None:
        await crud.save_dead_code_findings(self._session, repository_id, findings)

    async def get_dead_code_findings(
        self,
        repository_id: str,
        *,
        kind: str | None = None,
        min_confidence: float = 0.0,
        status: str = "open",
    ) -> list[DeadCodeFinding]:
        return await crud.get_dead_code_findings(
            self._session,
            repository_id,
            kind=kind,
            min_confidence=min_confidence,
            status=status,
        )

    async def update_dead_code_status(
        self, finding_id: str, status: str, note: str | None = None
    ) -> DeadCodeFinding | None:
        return await crud.update_dead_code_status(
            self._session, finding_id, status, note
        )

    async def get_dead_code_summary(self, repository_id: str) -> dict:
        return await crud.get_dead_code_summary(self._session, repository_id)

    async def upsert_decision(self, **kwargs: Any) -> DecisionRecord:
        return await crud.upsert_decision(self._session, **kwargs)

    async def bulk_upsert_decisions(
        self, repository_id: str, decisions: list[dict]
    ) -> None:
        await crud.bulk_upsert_decisions(self._session, repository_id, decisions)

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        return await crud.get_decision(self._session, decision_id)

    async def list_decisions(
        self,
        repository_id: str,
        *,
        status: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        module: str | None = None,
        include_proposed: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DecisionRecord]:
        return await crud.list_decisions(
            self._session,
            repository_id,
            status=status,
            source=source,
            tag=tag,
            module=module,
            include_proposed=include_proposed,
            limit=limit,
            offset=offset,
        )

    async def update_decision_metadata(
        self,
        decision_id: str,
        *,
        affected_modules: list[str] | None = None,
        affected_files: list[str] | None = None,
    ) -> DecisionRecord | None:
        return await crud.update_decision_metadata(
            self._session,
            decision_id,
            affected_modules=affected_modules,
            affected_files=affected_files,
        )

    async def update_decision_status(
        self,
        decision_id: str,
        status: str,
        *,
        superseded_by: str | None = None,
    ) -> DecisionRecord | None:
        return await crud.update_decision_status(
            self._session, decision_id, status, superseded_by=superseded_by
        )

    async def update_decision_by_id(
        self, decision_id: str, **fields: Any
    ) -> DecisionRecord | None:
        return await crud.update_decision_by_id(self._session, decision_id, **fields)

    async def delete_decision(self, decision_id: str) -> bool:
        return await crud.delete_decision(self._session, decision_id)

    async def recompute_decision_staleness(
        self, repository_id: str, git_meta_map: dict[str, dict]
    ) -> int:
        return await crud.recompute_decision_staleness(
            self._session, repository_id, git_meta_map
        )

    async def get_stale_decisions(
        self, repository_id: str, threshold: float = 0.5
    ) -> list[DecisionRecord]:
        return await crud.get_stale_decisions(
            self._session, repository_id, threshold
        )

    async def get_decision_health_summary(self, repository_id: str) -> dict:
        return await crud.get_decision_health_summary(self._session, repository_id)

    async def save_health_findings(
        self, repository_id: str, findings: list[Any]
    ) -> None:
        await crud.save_health_findings(self._session, repository_id, findings)

    async def save_health_metrics(
        self, repository_id: str, metrics: list[Any]
    ) -> None:
        await crud.save_health_metrics(self._session, repository_id, metrics)

    async def upsert_health_findings(
        self,
        repository_id: str,
        findings: list[Any],
        *,
        file_paths: list[str],
    ) -> None:
        await crud.upsert_health_findings(
            self._session, repository_id, findings, file_paths=file_paths
        )

    async def upsert_health_metrics(
        self, repository_id: str, metrics: list[Any]
    ) -> None:
        await crud.upsert_health_metrics(self._session, repository_id, metrics)

    async def get_health_findings(
        self,
        repository_id: str,
        *,
        biomarker_type: str | None = None,
        min_severity: str | None = None,
        file_path: str | None = None,
        status: str = "open",
    ) -> list[HealthFinding]:
        return await crud.get_health_findings(
            self._session,
            repository_id,
            biomarker_type=biomarker_type,
            min_severity=min_severity,
            file_path=file_path,
            status=status,
        )

    async def get_health_metrics(
        self, repository_id: str, *, file_paths: list[str] | None = None
    ) -> list[HealthFileMetric]:
        return await crud.get_health_metrics(
            self._session, repository_id, file_paths=file_paths
        )

    async def get_health_summary(self, repository_id: str) -> dict:
        return await crud.get_health_summary(self._session, repository_id)

    async def update_health_finding_status(
        self, finding_id: str, status: str
    ) -> HealthFinding | None:
        return await crud.update_health_finding_status(
            self._session, finding_id, status
        )

    async def save_health_snapshot(
        self,
        repository_id: str,
        *,
        hotspot_health: float,
        average_health: float,
        worst_performer_path: str | None,
        worst_performer_score: float | None,
        per_file_scores: dict[str, float] | None = None,
        taken_at: datetime | None = None,
    ) -> HealthSnapshot:
        return await crud.save_health_snapshot(
            self._session,
            repository_id,
            hotspot_health=hotspot_health,
            average_health=average_health,
            worst_performer_path=worst_performer_path,
            worst_performer_score=worst_performer_score,
            per_file_scores=per_file_scores,
            taken_at=taken_at,
        )

    async def list_health_snapshots(
        self, repository_id: str, *, limit: int | None = None
    ) -> list[HealthSnapshot]:
        return await crud.list_health_snapshots(
            self._session, repository_id, limit=limit
        )

    async def save_coverage_files(
        self,
        repository_id: str,
        files: list[Any],
        *,
        source_format: str,
        ingested_commit_sha: str | None = None,
    ) -> None:
        await crud.save_coverage_files(
            self._session,
            repository_id,
            files,
            source_format=source_format,
            ingested_commit_sha=ingested_commit_sha,
        )

    async def load_coverage_for_repo(
        self,
        repository_id: str,
        *,
        file_paths: list[str] | None = None,
    ) -> list[CoverageFile]:
        return await crud.load_coverage_for_repo(
            self._session, repository_id, file_paths=file_paths
        )

    async def get_coverage_summary(self, repository_id: str) -> dict[str, Any]:
        return await crud.get_coverage_summary(self._session, repository_id)
