"""Dead-code, security and repo-stats response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DeadCodeFindingResponse(BaseModel):
    id: str
    kind: str
    file_path: str
    symbol_name: str | None
    symbol_kind: str | None
    confidence: float
    reason: str
    lines: int
    safe_to_delete: bool
    primary_owner: str | None
    status: str
    note: str | None

    @classmethod
    def from_orm(cls, obj: object) -> DeadCodeFindingResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            kind=obj.kind,  # type: ignore[attr-defined]
            file_path=obj.file_path,  # type: ignore[attr-defined]
            symbol_name=obj.symbol_name,  # type: ignore[attr-defined]
            symbol_kind=obj.symbol_kind,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            reason=obj.reason,  # type: ignore[attr-defined]
            lines=obj.lines,  # type: ignore[attr-defined]
            safe_to_delete=obj.safe_to_delete,  # type: ignore[attr-defined]
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


class RepoStatsResponse(BaseModel):
    file_count: int
    symbol_count: int
    entry_point_count: int
    doc_coverage_pct: float
    freshness_score: float
    dead_export_count: int
