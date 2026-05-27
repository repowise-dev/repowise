"""Decision-record request/response models (records, evidence, lineage, graph)."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel


class DecisionRecordResponse(BaseModel):
    id: str
    repository_id: str
    title: str
    status: str
    context: str
    decision: str
    rationale: str
    alternatives: list[str]
    consequences: list[str]
    affected_files: list[str]
    affected_modules: list[str]
    tags: list[str]
    source: str
    evidence_commits: list[str]
    evidence_file: str | None
    evidence_line: int | None
    confidence: float
    staleness_score: float
    verification: str = "unverified"
    superseded_by: str | None
    last_code_change: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> DecisionRecordResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            context=obj.context,  # type: ignore[attr-defined]
            decision=obj.decision,  # type: ignore[attr-defined]
            rationale=obj.rationale,  # type: ignore[attr-defined]
            alternatives=json.loads(obj.alternatives_json),  # type: ignore[attr-defined]
            consequences=json.loads(obj.consequences_json),  # type: ignore[attr-defined]
            affected_files=json.loads(obj.affected_files_json),  # type: ignore[attr-defined]
            affected_modules=json.loads(obj.affected_modules_json),  # type: ignore[attr-defined]
            tags=json.loads(obj.tags_json),  # type: ignore[attr-defined]
            source=obj.source,  # type: ignore[attr-defined]
            evidence_commits=json.loads(obj.evidence_commits_json),  # type: ignore[attr-defined]
            evidence_file=obj.evidence_file,  # type: ignore[attr-defined]
            evidence_line=obj.evidence_line,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            staleness_score=obj.staleness_score,  # type: ignore[attr-defined]
            verification=obj.verification,  # type: ignore[attr-defined]
            superseded_by=obj.superseded_by,  # type: ignore[attr-defined]
            last_code_change=obj.last_code_change,  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


class DecisionCreate(BaseModel):
    title: str
    context: str = ""
    decision: str = ""
    rationale: str = ""
    alternatives: list[str] = []
    consequences: list[str] = []
    affected_files: list[str] = []
    affected_modules: list[str] = []
    tags: list[str] = []


class DecisionStatusUpdate(BaseModel):
    """PATCH body for /decisions/{id}.

    All fields are optional — clients can update status alone (the historical
    contract), the linked modules / files alone (governance editor), or both
    in a single request. Fields left at ``None`` are preserved.
    """

    status: str | None = None
    superseded_by: str | None = None
    affected_modules: list[str] | None = None
    affected_files: list[str] | None = None


class DecisionEvidenceResponse(BaseModel):
    """One provenance row supporting a decision record."""

    id: str
    source: str
    source_rank: int
    evidence_file: str | None
    evidence_line: int | None
    evidence_commit: str | None
    source_quote: str
    confidence: float
    verification: str
    created_at: str  # ISO-8601

    @classmethod
    def from_orm(cls, obj: object) -> DecisionEvidenceResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            source=obj.source,  # type: ignore[attr-defined]
            source_rank=obj.source_rank,  # type: ignore[attr-defined]
            evidence_file=obj.evidence_file,  # type: ignore[attr-defined]
            evidence_line=obj.evidence_line,  # type: ignore[attr-defined]
            evidence_commit=obj.evidence_commit,  # type: ignore[attr-defined]
            source_quote=obj.source_quote,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            verification=obj.verification,  # type: ignore[attr-defined]
            created_at=obj.created_at.isoformat(),  # type: ignore[attr-defined]
        )


class DecisionLineageEntry(BaseModel):
    """One node in a decision lineage chain (root → … → current)."""

    id: str
    title: str
    status: str
    source: str
    relation: str | None  # edge kind that *reached* this node (None for the leaf)


class DecisionGraphNode(BaseModel):
    """A decision record represented as a graph node."""

    id: str
    title: str
    status: str
    source: str
    confidence: float
    staleness_score: float
    verification: str

    @classmethod
    def from_orm(cls, obj: object) -> DecisionGraphNode:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            source=obj.source,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            staleness_score=obj.staleness_score,  # type: ignore[attr-defined]
            verification=obj.verification,  # type: ignore[attr-defined]
        )


class DecisionGraphEdge(BaseModel):
    """A typed directed edge between two decision records."""

    src: str
    dst: str
    kind: str
    confidence: float
    evidence: str


class DecisionCodeEdge(BaseModel):
    """A link from a decision to a governed file or module."""

    decision_id: str
    node_id: str
    link_type: str  # file | module


class DecisionGraphResponse(BaseModel):
    """Full decision graph: nodes, decision→decision edges, decision→code edges."""

    nodes: list[DecisionGraphNode]
    decision_edges: list[DecisionGraphEdge]
    code_edges: list[DecisionCodeEdge]
