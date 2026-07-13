"""Dead-code, security and repo-stats response models."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel

from repowise.core.analysis.dead_code.risk_factors import (
    effective_safe_to_delete,
    path_risk_factors,
)


class DeadCodeFindingResponse(BaseModel):
    id: str
    kind: str
    file_path: str
    symbol_name: str | None
    symbol_kind: str | None
    confidence: float
    reason: str
    lines: int
    start_line: int | None
    end_line: int | None
    # Effective deletion-readiness — re-derived from confidence + path risk
    # factors, not the raw persisted boolean. A config/bootstrap/database/
    # environment/script file is never deletion-ready even when stored safe.
    safe_to_delete: bool
    # Why this finding is a review candidate rather than a delete (empty for
    # ordinary modules). Drives the UI risk chips.
    risk_factors: list[str]
    # Human-readable signals behind the finding (in_degree, commit age, risk).
    evidence: list[str]
    primary_owner: str | None
    status: str
    note: str | None

    @classmethod
    def from_orm(cls, obj: object) -> DeadCodeFindingResponse:
        file_path: str = obj.file_path  # type: ignore[attr-defined]
        confidence: float = obj.confidence  # type: ignore[attr-defined]
        stored_safe: bool = obj.safe_to_delete  # type: ignore[attr-defined]
        try:
            evidence = json.loads(obj.evidence_json)  # type: ignore[attr-defined]
            if not isinstance(evidence, list):
                evidence = []
        except (AttributeError, TypeError, ValueError):
            evidence = []
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            kind=obj.kind,  # type: ignore[attr-defined]
            file_path=file_path,
            symbol_name=obj.symbol_name,  # type: ignore[attr-defined]
            symbol_kind=obj.symbol_kind,  # type: ignore[attr-defined]
            confidence=confidence,
            reason=obj.reason,  # type: ignore[attr-defined]
            lines=obj.lines,  # type: ignore[attr-defined]
            start_line=obj.start_line,  # type: ignore[attr-defined]
            end_line=obj.end_line,  # type: ignore[attr-defined]
            safe_to_delete=effective_safe_to_delete(confidence, file_path, stored_safe),
            risk_factors=list(path_risk_factors(file_path)),
            evidence=evidence,
            primary_owner=obj.primary_owner,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            note=obj.note,  # type: ignore[attr-defined]
        )


class DeadCodePatchRequest(BaseModel):
    status: str
    note: str | None = None


class DeadCodeSummaryResponse(BaseModel):
    total_findings: int
    confidence_summary: dict
    deletable_lines: int
    total_lines: int
    by_kind: dict


class SecurityFindingResponse(BaseModel):
    id: int
    file_path: str
    kind: str
    severity: str
    snippet: str | None
    detected_at: datetime
    # Present when the finding was sourced from git history (full-history
    # scan). ``None`` for working-tree findings produced during indexing.
    commit_sha: str | None
    found_in_history: bool


class RepoStatsResponse(BaseModel):
    file_count: int
    symbol_count: int
    entry_point_count: int
    doc_coverage_pct: float
    freshness_score: float
    dead_export_count: int
