"""Change Record response models.

The fields mirror ``repowise.core.changes.record.change_record``, the native
versioned JSON every surface serves. A list row leaves out the file and
commit lists and carries their counts.
"""

from __future__ import annotations

from pydantic import BaseModel


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


class ChangeRecord(ChangeSummary):
    commits: list[str]
    files: list[ChangeFile]
    metadata: ChangeForgeMetadata


class ChangeListResponse(BaseModel):
    items: list[ChangeSummary]
    total: int
    limit: int
    offset: int
    #: False when ``attribution.enabled`` is off for the repository.
    attribution: bool


def summary_from_record(record: dict) -> ChangeSummary:
    return ChangeSummary(
        **{k: v for k, v in record.items() if k in ChangeSummary.model_fields},
        commit_count=len(record["commits"]),
    )
