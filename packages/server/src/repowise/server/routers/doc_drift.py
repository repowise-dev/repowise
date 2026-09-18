"""/api/repos/{repo_id}/doc-drift — documentation drift endpoints.

Two questions, in opposite directions. The findings endpoint asks which
documents make assertions the repository no longer satisfies; the references
endpoint asks which documents name one file, which is the question a reader
about to change that file has.

Every derivation here is a call into
:mod:`repowise.core.analysis.doc_drift.serialize`. A rule computed in a handler
is a rule the next consumer of this data re-implements, so the handlers select
rows and hand them straight to the engine.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.doc_drift.constants import (
    DEFAULT_MIN_CONFIDENCE,
    REFERENCE_BASIS,
    UNAVAILABLE_NO_TABLE,
    UNAVAILABLE_NOT_COMPUTED,
    UNAVAILABLE_READ_FAILED,
)
from repowise.core.analysis.doc_drift.serialize import (
    collapse_reference_sites,
    documents_with_drift,
    summarize_findings,
)
from repowise.core.persistence import crud
from repowise.core.persistence.sql import is_missing_table
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    DocDriftFindingResponse,
    DocDriftReferencesResponse,
    DocDriftResponse,
)

router = APIRouter(
    tags=["doc-drift"],
    dependencies=[Depends(verify_api_key)],
)

#: Rows one findings request may serve. The table is a defect list and is
#: bounded by design, so this is a guard against a pathological tree rather
#: than a paging scheme; ``summary.findings_total`` always counts the rest.
_MAX_FINDINGS = 500

#: Reference rows one reverse answer may serve. A widely-named file can be
#: mentioned by hundreds of documents, and a reader scans the first screen.
_MAX_REFERENCES = 200


def _unavailable_reason(exc: Exception) -> str:
    """Which refusal *exc* is, since "reindex" is wrong advice for a lock."""
    return UNAVAILABLE_NO_TABLE if is_missing_table(exc) else UNAVAILABLE_READ_FAILED


async def _require_repo(session: AsyncSession, repo_id: str) -> None:
    """404 an unknown repository rather than reporting it as documented.

    Every other refusal here is a 200 carrying a cause. This one is not: an id
    that names nothing is a bad request, and answering it with a clean drift
    report is the same lie the ``unavailable`` field exists to prevent.
    """
    if await crud.get_repository(session, repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")


@router.get(
    "/api/repos/{repo_id}/doc-drift",
    response_model=DocDriftResponse,
)
async def list_doc_drift(
    repo_id: str,
    min_confidence: float = Query(DEFAULT_MIN_CONFIDENCE, ge=0.0, le=1.0),
    kind: str | None = Query(None, description="Filter by reference class"),
    document: str | None = Query(None, description="Only this document's findings"),
    limit: int = Query(100, ge=1, le=_MAX_FINDINGS),
    session: AsyncSession = Depends(get_db_session),
) -> DocDriftResponse:
    """Documents whose assertions the repository no longer satisfies.

    The summary describes the same filters as the list, so the two cannot
    disagree, and counts before ``limit`` so a capped page is never mistaken
    for the repository.
    """
    await _require_repo(session, repo_id)

    try:
        rows = await crud.get_doc_drift_findings(
            session,
            repo_id,
            file_path=document,
            min_confidence=min_confidence,
        )
        # An empty answer is not a clean tree: the drift pass only runs when an
        # update has work to do, so "upgraded, never analysed" looks exactly
        # like "every document is correct". Both stores are probed, unfiltered,
        # and only when the answer would otherwise be the strong one.
        #
        # Unfiltered is the whole point. Probing with ``rows`` would let a
        # narrowing query answer a question about the index: a repository whose
        # documents resolve to nothing has no reference rows, so
        # ``?min_confidence=0.7`` over findings that all sit lower would report
        # a tree holding findings as one nobody has ever checked.
        computed = (
            bool(rows)
            or await crud.doc_drift_findings_stored(session, repo_id)
            or await crud.doc_drift_references_stored(session, repo_id)
        )
    except (SQLAlchemyError, OSError, LookupError) as exc:
        return DocDriftResponse(
            findings=[],
            findings_emitted=0,
            summary=None,
            unavailable=_unavailable_reason(exc),
        )

    if not computed:
        return DocDriftResponse(
            findings=[],
            findings_emitted=0,
            summary=None,
            unavailable=UNAVAILABLE_NOT_COMPUTED,
        )

    if kind is not None:
        rows = [row for row in rows if row.kind == kind]

    serialized = [crud.serialize_doc_drift_row(row) for row in rows]
    emitted = serialized[:limit]
    return DocDriftResponse(
        findings=[DocDriftFindingResponse.from_dict(f) for f in emitted],
        findings_emitted=len(emitted),
        summary=summarize_findings(serialized),
    )


@router.get(
    "/api/repos/{repo_id}/doc-drift/references",
    response_model=DocDriftReferencesResponse,
)
async def doc_drift_references(
    repo_id: str,
    target: str = Query(..., description="Repo-relative path of the file"),
    limit: int = Query(50, ge=1, le=_MAX_REFERENCES),
    session: AsyncSession = Depends(get_db_session),
) -> DocDriftReferencesResponse:
    """Which documents name *target*, and which of them carry drift.

    A reference says a document names a file the repository still has. It does
    not say the document describes it, and ``documents_with_drift`` is a fact
    about the document rather than about *target*: a drifted reference resolves
    to nothing, so the store cannot express "this description of your file is
    wrong" and must not be read as doing so.
    """
    await _require_repo(session, repo_id)

    try:
        rows = await crud.get_doc_drift_references(
            session, repo_id, target_paths=[target]
        )
        stored = bool(rows) or await crud.doc_drift_references_stored(session, repo_id)
        # Read whole, and only when there is something to match against: the
        # findings table is a defect list and is bounded by design.
        finding_rows = (
            await crud.get_doc_drift_findings(session, repo_id) if rows else []
        )
    except (SQLAlchemyError, OSError, LookupError) as exc:
        # Returns immediately, and must keep doing so: a failed statement
        # poisons the transaction on Postgres, so anything issued on this
        # session between here and the return would raise on its own.
        return _empty_references(target, _unavailable_reason(exc))

    if not stored:
        return _empty_references(target, UNAVAILABLE_NOT_COMPUTED)

    references = collapse_reference_sites(
        [crud.serialize_doc_drift_reference_row(row) for row in rows]
    )
    drift_counts: dict[str, int] = {}
    for finding in finding_rows:
        drift_counts[finding.file_path] = drift_counts.get(finding.file_path, 0) + 1

    emitted = references[:limit]
    return DocDriftReferencesResponse(
        target_path=target,
        references=emitted,
        references_emitted=len(emitted),
        # Both counts are over the uncapped list: a document past the display
        # cap still names the target and still carries its drift, and reading
        # either off the capped list would report it as absent. That is why the
        # emitted count is reported beside them rather than left to be inferred
        # from the length of a list the caller cannot tell is complete.
        references_total=len(references),
        documents=len({r["document"] for r in references}),
        documents_with_drift=documents_with_drift(references, drift_counts),
        references_basis=REFERENCE_BASIS,
    )


def _empty_references(target: str, reason: str) -> DocDriftReferencesResponse:
    """A refusal that still carries what a reference row would have claimed."""
    return DocDriftReferencesResponse(
        target_path=target,
        references=[],
        references_emitted=0,
        references_total=0,
        documents=0,
        documents_with_drift=[],
        references_basis=REFERENCE_BASIS,
        unavailable=reason,
    )
