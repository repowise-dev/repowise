"""Decision-record request/response models (records, evidence, lineage, graph)."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from repowise.core.analysis.decisions.policy import DISCOVERY_BOUNDS
from repowise.core.analysis.decisions.scope import derive_decision_scope


class EvidencePreview(BaseModel):
    """The top-ranked evidence row, slimmed for list rows."""

    source: str
    source_quote: str
    verification: str
    evidence_file: str | None = None
    evidence_line: int | None = None


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
    # Derived granularity: file | module | cross-module, or None when the
    # record has no code linkage at all. Computed at serialization time from
    # the linkage fields, so old records get it too.
    scope: str | None = None
    superseded_by: str | None
    last_code_change: datetime | None
    created_at: datetime
    updated_at: datetime
    # List-row evidence preview: the top-ranked evidence row's verbatim quote
    # plus how many evidence rows back the record. Populated by the list
    # endpoint only (None on detail/graph responses, which have the full
    # /evidence endpoint instead).
    evidence_count: int | None = None
    evidence_preview: EvidencePreview | None = None
    # Effective currency from the acceptance, or None for a candidate. This is
    # the authority answer; ``status`` is the projection kept in step for
    # readers that predate the split. A record can be stored ``active`` and
    # carry no currency at all, which is precisely what a candidate is.
    currency: str | None = None

    @classmethod
    def from_orm(cls, obj: object) -> DecisionRecordResponse:
        affected_files = json.loads(obj.affected_files_json)  # type: ignore[attr-defined]
        affected_modules = json.loads(obj.affected_modules_json)  # type: ignore[attr-defined]
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            status=obj.status,  # type: ignore[attr-defined]
            context=obj.context,  # type: ignore[attr-defined]
            # Body fallback: the substring gate can clear a paraphrased
            # ``decision`` while an evidence quote keeps the record alive,
            # historically leaving a title-only record. Fall back to the title
            # (the model's canonical one-line summary, always present) so no read
            # surface emits a body-less decision. New records get this at write
            # time in the harvest path; this covers pre-fix stored records.
            decision=(obj.decision or "").strip() or obj.title,  # type: ignore[attr-defined]
            rationale=obj.rationale,  # type: ignore[attr-defined]
            alternatives=json.loads(obj.alternatives_json),  # type: ignore[attr-defined]
            consequences=json.loads(obj.consequences_json),  # type: ignore[attr-defined]
            affected_files=affected_files,
            affected_modules=affected_modules,
            tags=json.loads(obj.tags_json),  # type: ignore[attr-defined]
            source=obj.source,  # type: ignore[attr-defined]
            evidence_commits=json.loads(obj.evidence_commits_json),  # type: ignore[attr-defined]
            evidence_file=obj.evidence_file,  # type: ignore[attr-defined]
            evidence_line=obj.evidence_line,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            staleness_score=obj.staleness_score,  # type: ignore[attr-defined]
            verification=obj.verification,  # type: ignore[attr-defined]
            scope=derive_decision_scope(
                affected_files,
                affected_modules,
                evidence_file=obj.evidence_file,  # type: ignore[attr-defined]
            ),
            superseded_by=obj.superseded_by,  # type: ignore[attr-defined]
            last_code_change=obj.last_code_change,  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


class DecisionCountsResponse(BaseModel):
    """Counts by status for a repository, from a grouped COUNT.

    Exists so a caller can state a total it actually measured. The list
    endpoint caps at 500 rows, so counting the page reported "97 of 100" on a
    repository holding several hundred records.
    """

    total: int
    active: int
    proposed: int
    superseded: int
    deprecated: int


class DecisionLaneCountsResponse(BaseModel):
    """Records per review lane, from a scan of the acceptance join.

    ``candidates`` and the four currency lanes partition the repository and sum
    to ``total``; ``governing`` is the roll-up of the two that still bind, so a
    caller can state "N rules" without adding two tabs together.
    """

    candidates: int
    active: int
    needs_review: int
    uncheckable: int
    history: int
    governing: int
    total: int


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


class DecisionHealthResponse(BaseModel):
    """Governance rollup: what is stale, awaiting review, and ungoverned."""

    #: Record counts by status, plus ``stale``, ``unscoped`` and ``conflicts``.
    summary: dict[str, int] = {}
    stale_decisions: list[DecisionRecordResponse] = []
    proposed_awaiting_review: list[DecisionRecordResponse] = []
    #: Hotspot paths no active decision names, worst-ranked first.
    ungoverned_hotspots: list[str] = []


class DecisionEvidenceListResponse(BaseModel):
    evidence: list[DecisionEvidenceResponse] = []


class DecisionLineageResponse(BaseModel):
    """The supersedes/refines chain, root first."""

    lineage: list[DecisionLineageEntry] = []


# ---------------------------------------------------------------------------
# Capture policy
# ---------------------------------------------------------------------------


#: Sourced from the policy registry so the wire bounds cannot drift from the
#: ones the resolver enforces.
_DISCOVERY_DEFAULTS = {key: bounds[2] for key, bounds in DISCOVERY_BOUNDS.items()}
_DISCOVERY_RANGE = {key: {"ge": bounds[0], "le": bounds[1]} for key, bounds in DISCOVERY_BOUNDS.items()}


class DecisionSourceState(BaseModel):
    """One capture source's capabilities and resolved state."""

    key: str
    label: str
    description: str
    #: ``machine`` for inferred capture, ``human`` for authority routes.
    authority: str
    deterministic: bool
    supports_llm: bool
    #: False for authority routes, which have no capture to switch off.
    togglable: bool
    enabled: bool
    llm_enabled: bool
    #: enabled | disabled | deterministic_only | skipped_no_provider | always_on
    status: str
    reason: str


class DecisionDiscoveryBudget(BaseModel):
    """Per-update ceiling on the one broad session-discovery call."""

    model_config = ConfigDict(extra="forbid")

    max_sessions: int = _DISCOVERY_DEFAULTS["max_sessions"]
    max_input_tokens: int = _DISCOVERY_DEFAULTS["max_input_tokens"]


class DecisionDiscoveryPatch(BaseModel):
    """A change to the discovery budget. Omitted fields keep their value.

    Separate from :class:`DecisionDiscoveryBudget` because a response states
    both numbers while a write may set one, and a shared model would fill the
    other from its default and quietly reset it.
    """

    model_config = ConfigDict(extra="forbid")

    max_sessions: int | None = Field(default=None, **_DISCOVERY_RANGE["max_sessions"])
    max_input_tokens: int | None = Field(default=None, **_DISCOVERY_RANGE["max_input_tokens"])


class DecisionSettings(BaseModel):
    """The resolved decision capture policy for one repository."""

    enabled: bool = True
    llm: bool = True
    #: default | off | local_only | balanced | full | custom
    preset: str = "default"
    discovery: DecisionDiscoveryBudget = DecisionDiscoveryBudget()
    sources: list[DecisionSourceState] = []
    provider_available: bool = True
    #: Recoverable config problems, e.g. an unknown source key.
    warnings: list[str] = []
    #: Legacy keys still being honoured. A write through this endpoint
    #: replaces them, so a settings form can say so before saving.
    legacy_keys: list[str] = []
    #: Changes ``etag`` whenever the resolved policy changes. Pass it back on
    #: write to detect a concurrent edit.
    etag: str = ""


class DecisionSourcePatch(BaseModel):
    """A change to one source. Omitted fields keep their current value.

    ``extra="forbid"`` on purpose: an untyped mapping accepted a misspelt
    ``{"enable": true}`` and returned 200 having changed nothing, so a UI
    toggle read as saved when it was not.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    llm: bool | None = None


class DecisionSettingsUpdate(BaseModel):
    """A partial policy write. Omitted fields keep their current value."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    llm: bool | None = None
    #: Applied first, so a preset plus per-source overrides works in one call.
    preset: str | None = None
    sources: dict[str, DecisionSourcePatch] | None = None
    #: Budget for broad session discovery. Omitted fields keep their value.
    discovery: DecisionDiscoveryPatch | None = None
    etag: str | None = None
