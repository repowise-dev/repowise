"""Documentation drift response models.

The wire shape, and the only place it is declared. Each model is built from a
dict the engine produced rather than from an ORM row, so the route serves what
:mod:`repowise.core.analysis.doc_drift.serialize` emits without a second
hand-maintained mapping in between.

``unavailable`` is a first-class field on both responses and not an HTTP error.
"The drift pass has not run here" is an answer about the index, not a failure
of the request, and a client that received a 4xx would have to guess which of
the three causes it was. An empty list with no ``unavailable`` is the strong
claim that the tree is clean, and only a populated store can support it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from repowise.core.analysis.doc_drift.constants import (
    UNAVAILABLE_NO_TABLE,
    UNAVAILABLE_NOT_COMPUTED,
    UNAVAILABLE_READ_FAILED,
)
from repowise.core.analysis.doc_drift.serialize import derive_doc_drift_id

#: Why a drift answer could not be given, from the engine's own vocabulary so
#: the literal cannot be renamed on one side alone.
DocDriftUnavailable = Literal[
    UNAVAILABLE_NOT_COMPUTED,  # type: ignore[valid-type]
    UNAVAILABLE_NO_TABLE,  # type: ignore[valid-type]
    UNAVAILABLE_READ_FAILED,  # type: ignore[valid-type]
]


class DocDriftFindingResponse(BaseModel):
    """One assertion a document makes that the repository no longer satisfies.

    ``file_path`` is the *document* to edit, never the target it names. A
    reader who reads it as the broken file has been told the opposite of the
    truth, which is why the field keeps the name every other drift surface
    gives it.
    """

    #: Stable across stores and rebuilds; see :func:`derive_doc_drift_id`.
    id: str
    file_path: str
    line_number: int
    kind: str
    target: str
    confidence: float
    origin: str
    reason: str
    raw: str
    context: str
    evidence: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> DocDriftFindingResponse:
        """One serialized finding, given the id the store cannot hold."""
        return cls(
            id=derive_doc_drift_id(
                data["file_path"],
                data["kind"],
                data["line_number"],
                data["target"],
            ),
            file_path=data["file_path"],
            line_number=data["line_number"],
            kind=data["kind"],
            target=data["target"],
            confidence=data["confidence"],
            origin=data["origin"],
            reason=data["reason"],
            raw=data["raw"],
            context=data["context"],
            evidence=data.get("evidence", []),
        )


class DocDriftSummaryResponse(BaseModel):
    """The rollup above the list, computed server-side over the same rows.

    ``findings_total`` counts every finding matching the query, including any
    the display cap left out, so a client cannot recompute a page as if it were
    the repository.
    """

    findings_total: int
    documents: int
    confidence: dict[str, int]
    by_kind: dict[str, int]
    #: What the counts do and do not cover. No surface may show them without it.
    findings_basis: str


class DocDriftResponse(BaseModel):
    """Findings for a repository, with the rollup that makes them legible."""

    findings: list[DocDriftFindingResponse]
    #: Rows served after ``limit``; below ``summary.findings_total`` when capped.
    findings_emitted: int
    summary: DocDriftSummaryResponse | None
    unavailable: DocDriftUnavailable | None = None


class DocDriftReferenceResponse(BaseModel):
    """A document that names a file the repository still has.

    Deliberately weaker than a finding: it does not claim the document
    describes the file, or that its prose is current.
    """

    document: str
    line: int
    kind: str
    section: str = ""


class DocDriftDocumentDriftResponse(BaseModel):
    """A naming document that carries drift somewhere in it.

    ``findings`` counts the whole document, not assertions about the file that
    was asked about. A drifted reference resolves to nothing, so no row here
    can say a document's description of that file is wrong.
    """

    document: str
    findings: int


class DocDriftReferencesResponse(BaseModel):
    """Which documents name one file: the reverse view, over HTTP."""

    target_path: str
    references: list[DocDriftReferenceResponse]
    #: Rows served after the cap; below ``references_total`` when capped.
    references_emitted: int
    #: Every reference to the target, so a capped list cannot read as complete.
    references_total: int
    #: Distinct documents naming the target, before any display cap.
    documents: int
    documents_with_drift: list[DocDriftDocumentDriftResponse]
    #: What a reference row claims, and what its absence does not prove.
    references_basis: str
    unavailable: DocDriftUnavailable | None = None
