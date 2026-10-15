"""Change Record response models.

The fields mirror ``repowise.core.changes.record.change_record``, the native
versioned JSON every surface serves. A list row leaves out the file and
commit lists and carries their counts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_serializer


class ChangeIdentity(BaseModel):
    """``pr`` | ``change_id`` | ``merge`` | ``commit``, first that applies."""

    kind: str
    value: str


class ChangeAuthor(BaseModel):
    name: str
    email: str


class ChangeFile(BaseModel):
    path: str
    added: int
    deleted: int


class ChangeProvenance(BaseModel):
    """Three-state AI provenance over the change's commits, with its coverage."""

    label: str
    agent_commits: int
    human_attested_commits: int
    unknown_commits: int
    coverage: float | None = None


class ChangeForgeMetadata(BaseModel):
    """Forge facts from a metadata source; ``None`` means unknown."""

    source: str | None = None
    reviewers: list[str] | None = None
    approvals: int | None = None
    labels: list[str] | None = None
    linked_issues: list[str] | None = None


class ChangeSummary(BaseModel):
    schema_version: int
    id: str
    identity: ChangeIdentity
    shape: str
    forge: str
    forge_ref: str | None = None
    title: str
    merged_at: str | None = None
    head_sha: str
    author: ChangeAuthor
    commit_count: int
    files_changed: int
    lines_added: int
    lines_deleted: int
    category: str
    # Null while AI attribution is off, or on rows indexed before it.
    provenance: ChangeProvenance | None = None


class EvidenceLine(BaseModel):
    """An item's value as a message key and its parameters, for a client to word."""

    key: str
    params: dict[str, Any]


class EvidenceItem(BaseModel):
    """One checklist row: what was found, whether it needs a look, how it is known."""

    kind: str
    label: str
    status: Literal["ok", "attention", "unknown"]
    basis: Literal["measured", "inferred", "declared", "unresolved"]
    value: Any = None
    details: list[dict[str, Any]]
    source: str
    #: ``value`` for a reader (``repowise.core.changes.evidence.item_line``).
    line: EvidenceLine | None = None


class ChangeEvidence(BaseModel):
    """The evidence checklist (``repowise.core.changes.evidence``), versioned."""

    schema_version: int
    change_id: str | None = None
    revspec: str | None = None
    generated_at: str
    index_commit: str | None = None
    items: list[EvidenceItem]


class ChangeRecord(ChangeSummary):
    commits: list[str]
    files: list[ChangeFile]
    metadata: ChangeForgeMetadata
    # Present only when the request asked for it (``?evidence=true``).
    evidence: ChangeEvidence | None = None
    # True when the checklist is still being built; ask again shortly.
    evidence_building: bool = False


class ChangeEvidenceSummary(BaseModel):
    """Status counts of a change's kept checklist; never builds one."""

    generated_at: str
    ok: int
    attention: int
    unknown: int


class ChangeListItem(ChangeSummary):
    # Sent only when the list asked for it (``?evidence=true``) and the change
    # has a kept checklist; absent otherwise, so the default row is unchanged.
    evidence_summary: ChangeEvidenceSummary | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_summary(self, handler):  # no return annotation: keeps the schema
        data = handler(self)
        if self.evidence_summary is None:
            data.pop("evidence_summary", None)
        return data


class ChecklistsBuilt(BaseModel):
    """How many of the changes a list's filters match keep an evidence checklist."""

    built: int
    changes: int


class ChangeListResponse(BaseModel):
    items: list[ChangeListItem]
    total: int
    limit: int
    offset: int
    #: False when ``attribution.enabled`` is off for the repository.
    attribution: bool
    # Sent only with ``?evidence=true``; absent otherwise.
    checklists_built: ChecklistsBuilt | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_count(self, handler):  # no return annotation: keeps the schema
        data = handler(self)
        if self.checklists_built is None:
            data.pop("checklists_built", None)
        return data


def summary_from_record(record: dict) -> ChangeListItem:
    return ChangeListItem(
        **{k: v for k, v in record.items() if k in ChangeSummary.model_fields},
        commit_count=len(record["commits"]),
    )


class SuspectsRequest(BaseModel):
    """A symptom and the window to rank the changes merged in."""

    symptom: str = Field("", max_length=20_000)
    files: list[str] = Field([], max_length=50)
    trace: str = Field("", max_length=20_000)
    #: ISO 8601; default 14 days before ``until``.
    since: str | None = None
    #: ISO 8601, when the symptom was seen; default now.
    until: str | None = None
    limit: int = Field(10, ge=1, le=200)


class SuspectSignal(BaseModel):
    rank: int
    evidence: list[str]


class Suspect(BaseModel):
    """One change in reading order; ``signals`` holds each signal that fired."""

    id: str
    ref: str | None = None
    title: str
    merged_at: str | None = None
    category: str
    head_sha: str
    files_changed: int
    provenance: str | None = None
    rank: int
    signals: dict[str, SuspectSignal]


class SuspectsResponse(BaseModel):
    window: dict[str, str]
    candidates: int
    #: Changes git could not read; they are ranked on title and files alone.
    texts_missing: int = 0
    symptom_files: list[str] = []
    suspects: list[Suspect]
    note: str
