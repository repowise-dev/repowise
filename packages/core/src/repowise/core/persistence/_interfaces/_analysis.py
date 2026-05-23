"""IndexStore mixin: git metadata, dead code, decisions, health, coverage.

The "analysis" surface of IndexStore — anything produced by reading the
repo's history or running quality biomarkers, plus the decision-record
catalog that hangs off the same tables.

Split out from :class:`IndexStore` to keep each interface file under 400
lines per the project code-quality rule.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ..models import (
    CoverageFile,
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    HealthFileMetric,
    HealthFinding,
    HealthSnapshot,
)


class AnalysisIndexStore(ABC):
    """GitMetadata, DeadCode, Decision, Health, Coverage CRUD."""

    # ------------------------------------------------------------------
    # GitMetadata
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_git_metadata(
        self, *, repository_id: str, file_path: str, **kwargs: object
    ) -> GitMetadata: ...

    @abstractmethod
    async def get_git_metadata(
        self, repository_id: str, file_path: str
    ) -> GitMetadata | None: ...

    @abstractmethod
    async def get_git_metadata_bulk(
        self, repository_id: str, file_paths: list[str]
    ) -> dict[str, GitMetadata]: ...

    @abstractmethod
    async def get_all_git_metadata(
        self, repository_id: str
    ) -> dict[str, GitMetadata]: ...

    @abstractmethod
    async def upsert_git_metadata_bulk(
        self, repository_id: str, metadata_list: list[dict]
    ) -> None: ...

    @abstractmethod
    async def recompute_git_percentiles(self, repository_id: str) -> int: ...

    # ------------------------------------------------------------------
    # DeadCode
    # ------------------------------------------------------------------

    @abstractmethod
    async def save_dead_code_findings(
        self, repository_id: str, findings: list[dict]
    ) -> None: ...

    @abstractmethod
    async def get_dead_code_findings(
        self,
        repository_id: str,
        *,
        kind: str | None = None,
        min_confidence: float = 0.0,
        status: str = "open",
    ) -> list[DeadCodeFinding]: ...

    @abstractmethod
    async def update_dead_code_status(
        self, finding_id: str, status: str, note: str | None = None
    ) -> DeadCodeFinding | None: ...

    @abstractmethod
    async def get_dead_code_summary(self, repository_id: str) -> dict: ...

    # ------------------------------------------------------------------
    # DecisionRecord
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_decision(self, **kwargs: Any) -> DecisionRecord: ...

    @abstractmethod
    async def bulk_upsert_decisions(
        self, repository_id: str, decisions: list[dict]
    ) -> None: ...

    @abstractmethod
    async def get_decision(self, decision_id: str) -> DecisionRecord | None: ...

    @abstractmethod
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
    ) -> list[DecisionRecord]: ...

    @abstractmethod
    async def update_decision_metadata(
        self,
        decision_id: str,
        *,
        affected_modules: list[str] | None = None,
        affected_files: list[str] | None = None,
    ) -> DecisionRecord | None: ...

    @abstractmethod
    async def update_decision_status(
        self,
        decision_id: str,
        status: str,
        *,
        superseded_by: str | None = None,
    ) -> DecisionRecord | None: ...

    @abstractmethod
    async def update_decision_by_id(
        self, decision_id: str, **fields: Any
    ) -> DecisionRecord | None: ...

    @abstractmethod
    async def delete_decision(self, decision_id: str) -> bool: ...

    @abstractmethod
    async def recompute_decision_staleness(
        self, repository_id: str, git_meta_map: dict[str, dict]
    ) -> int: ...

    @abstractmethod
    async def get_stale_decisions(
        self, repository_id: str, threshold: float = 0.5
    ) -> list[DecisionRecord]: ...

    @abstractmethod
    async def get_decision_health_summary(self, repository_id: str) -> dict: ...

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @abstractmethod
    async def save_health_findings(
        self, repository_id: str, findings: list[Any]
    ) -> None: ...

    @abstractmethod
    async def save_health_metrics(
        self, repository_id: str, metrics: list[Any]
    ) -> None: ...

    @abstractmethod
    async def upsert_health_findings(
        self,
        repository_id: str,
        findings: list[Any],
        *,
        file_paths: list[str],
    ) -> None: ...

    @abstractmethod
    async def upsert_health_metrics(
        self, repository_id: str, metrics: list[Any]
    ) -> None: ...

    @abstractmethod
    async def get_health_findings(
        self,
        repository_id: str,
        *,
        biomarker_type: str | None = None,
        min_severity: str | None = None,
        file_path: str | None = None,
        status: str = "open",
    ) -> list[HealthFinding]: ...

    @abstractmethod
    async def get_health_metrics(
        self, repository_id: str, *, file_paths: list[str] | None = None
    ) -> list[HealthFileMetric]: ...

    @abstractmethod
    async def get_health_summary(self, repository_id: str) -> dict: ...

    @abstractmethod
    async def update_health_finding_status(
        self, finding_id: str, status: str
    ) -> HealthFinding | None: ...

    @abstractmethod
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
    ) -> HealthSnapshot: ...

    @abstractmethod
    async def list_health_snapshots(
        self, repository_id: str, *, limit: int | None = None
    ) -> list[HealthSnapshot]: ...

    # ------------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------------

    @abstractmethod
    async def save_coverage_files(
        self,
        repository_id: str,
        files: list[Any],
        *,
        source_format: str,
        ingested_commit_sha: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def load_coverage_for_repo(
        self,
        repository_id: str,
        *,
        file_paths: list[str] | None = None,
    ) -> list[CoverageFile]: ...

    @abstractmethod
    async def get_coverage_summary(self, repository_id: str) -> dict[str, Any]: ...
