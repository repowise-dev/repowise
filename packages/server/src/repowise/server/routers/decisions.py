"""/api/repos/{repo_id}/decisions — Architectural decision record endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud, decision_graph
from repowise.core.persistence.models import DecisionEvidence
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    DecisionCodeEdge,
    DecisionCreate,
    DecisionEvidenceResponse,
    DecisionGraphEdge,
    DecisionGraphNode,
    DecisionGraphResponse,
    DecisionLineageEntry,
    DecisionRecordResponse,
    DecisionStatusUpdate,
)
from repowise.server.schemas.decisions import EvidencePreview

router = APIRouter(
    tags=["decisions"],
    dependencies=[Depends(verify_api_key)],
)


@router.get(
    "/api/repos/{repo_id}/decisions",
    response_model=list[DecisionRecordResponse],
)
async def list_decisions(
    repo_id: str,
    status: str | None = Query(None, description="Filter by status"),
    source: str | None = Query(None, description="Filter by source"),
    tag: str | None = Query(None, description="Filter by tag"),
    module: str | None = Query(None, description="Filter by module path"),
    include_proposed: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[DecisionRecordResponse]:
    """List architectural decision records for a repository.

    Each row carries an ``evidence_preview`` (the top-ranked evidence row's
    verbatim quote) plus the total ``evidence_count``, so the table can show
    provenance without N+1 calls to the per-decision /evidence endpoint.
    """
    decisions = await crud.list_decisions(
        session,
        repo_id,
        status=status,
        source=source,
        tag=tag,
        module=module,
        include_proposed=include_proposed,
        limit=limit,
        offset=offset,
    )
    items = [DecisionRecordResponse.from_orm(d) for d in decisions]

    ids = [d.id for d in decisions]
    if ids:
        rows = (
            await session.execute(
                select(DecisionEvidence)
                .where(DecisionEvidence.decision_id.in_(ids))
                .order_by(
                    DecisionEvidence.source_rank.desc(),
                    DecisionEvidence.confidence.desc(),
                )
            )
        ).scalars()
        counts: dict[str, int] = {}
        best: dict[str, DecisionEvidence] = {}
        for ev in rows:
            counts[ev.decision_id] = counts.get(ev.decision_id, 0) + 1
            # Rows arrive best-first, so the first row per decision wins.
            best.setdefault(ev.decision_id, ev)
        for item in items:
            item.evidence_count = counts.get(item.id, 0)
            top = best.get(item.id)
            if top is not None and top.source_quote:
                item.evidence_preview = EvidencePreview(
                    source=top.source,
                    source_quote=top.source_quote,
                    verification=top.verification,
                    evidence_file=top.evidence_file,
                    evidence_line=top.evidence_line,
                )
    return items


@router.get(
    "/api/repos/{repo_id}/decisions/health",
)
async def decision_health(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Get decision health summary: stale, proposed, ungoverned hotspots."""
    summary = await crud.get_decision_health_summary(session, repo_id)
    return {
        "summary": summary["summary"],
        "stale_decisions": [DecisionRecordResponse.from_orm(d) for d in summary["stale_decisions"]],
        "proposed_awaiting_review": [
            DecisionRecordResponse.from_orm(d) for d in summary["proposed_awaiting_review"]
        ],
        "ungoverned_hotspots": summary["ungoverned_hotspots"],
    }


@router.get(
    "/api/repos/{repo_id}/decisions/graph",
    response_model=DecisionGraphResponse,
)
async def get_decision_graph(
    repo_id: str,
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DecisionGraphResponse:
    """Return the full decision graph for a repository.

    Nodes are capped at *limit* (default 200), preferring active + superseded +
    proposed statuses. Decision→decision typed edges and decision→code links are
    returned without an additional cap (they scale with the node set).
    """
    # Fetch decisions ordered by staleness (most relevant first): active, then
    # superseded/proposed, then deprecated. Use list_decisions without status
    # filter so we get all statuses, capped.
    all_decisions = await crud.list_decisions(
        session,
        repo_id,
        include_proposed=True,
        limit=limit,
        offset=0,
    )

    nodes = [DecisionGraphNode.from_orm(d) for d in all_decisions]

    raw_edges = await decision_graph.list_all_decision_edges(session, repo_id)
    decision_edges = [
        DecisionGraphEdge(
            src=e.src_decision_id,
            dst=e.dst_decision_id,
            kind=e.kind,
            confidence=e.confidence,
            evidence=e.evidence,
        )
        for e in raw_edges
    ]

    raw_links = await decision_graph.list_decision_node_links(session, repo_id)
    code_edges = [
        DecisionCodeEdge(
            decision_id=lnk.decision_id,
            node_id=lnk.node_id,
            link_type=lnk.link_type,
        )
        for lnk in raw_links
    ]

    return DecisionGraphResponse(nodes=nodes, decision_edges=decision_edges, code_edges=code_edges)


@router.get(
    "/api/repos/{repo_id}/decisions/{decision_id}",
    response_model=DecisionRecordResponse,
)
async def get_decision(
    repo_id: str,
    decision_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DecisionRecordResponse:
    """Get a single decision record by ID."""
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    return DecisionRecordResponse.from_orm(rec)


@router.get(
    "/api/repos/{repo_id}/decisions/{decision_id}/evidence",
)
async def list_decision_evidence(
    repo_id: str,
    decision_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Return provenance evidence rows for a single decision record.

    Returns ``{"evidence": [...]}`` where each item carries the verbatim source
    quote, evidence file/line/commit, per-source confidence, and verification
    badge (``exact`` | ``fuzzy`` | ``unverified``). 404 if the decision does not
    exist or belongs to a different repository.
    """
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    rows = await crud.list_decision_evidence(session, decision_id)
    return {"evidence": [DecisionEvidenceResponse.from_orm(r) for r in rows]}


@router.get(
    "/api/repos/{repo_id}/decisions/{decision_id}/lineage",
)
async def get_decision_lineage(
    repo_id: str,
    decision_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Return the lineage chain for a decision (root → … → current).

    Walks ``supersedes``/``refines`` edges back to the earliest ancestor so the
    UI can render a timeline. An isolated decision returns a single-entry chain.
    404 if the decision does not exist or belongs to a different repository.
    """
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    chain = await decision_graph.build_lineage_chain(session, decision_id)
    return {"lineage": [DecisionLineageEntry(**entry) for entry in chain]}


@router.post(
    "/api/repos/{repo_id}/decisions",
    response_model=DecisionRecordResponse,
    status_code=201,
)
async def create_decision(
    repo_id: str,
    body: DecisionCreate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DecisionRecordResponse:
    """Create a new decision record (e.g. from CLI capture via API)."""
    rec = await crud.upsert_decision(
        session,
        repository_id=repo_id,
        title=body.title,
        status="active",
        context=body.context,
        decision=body.decision,
        rationale=body.rationale,
        alternatives=body.alternatives,
        consequences=body.consequences,
        affected_files=body.affected_files,
        affected_modules=body.affected_modules,
        tags=body.tags,
        source="cli",
        confidence=1.0,
    )
    return DecisionRecordResponse.from_orm(rec)


@router.patch(
    "/api/repos/{repo_id}/decisions/{decision_id}",
    response_model=DecisionRecordResponse,
)
async def patch_decision(
    repo_id: str,
    decision_id: str,
    body: DecisionStatusUpdate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> DecisionRecordResponse:
    """Update a decision record.

    Accepts status transitions (confirm / deprecate / supersede) and / or
    governance edits (``affected_modules``, ``affected_files``). Any field
    left as ``None`` in the body is preserved.
    """
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")

    if body.status is not None:
        try:
            rec = await crud.update_decision_status(
                session,
                decision_id,
                body.status,
                superseded_by=body.superseded_by,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if rec is None:
            raise HTTPException(status_code=404, detail="Decision not found")
    elif body.superseded_by is not None:
        raise HTTPException(
            status_code=400,
            detail="superseded_by requires status='superseded'",
        )

    if body.affected_modules is not None or body.affected_files is not None:
        rec = await crud.update_decision_metadata(
            session,
            decision_id,
            affected_modules=body.affected_modules,
            affected_files=body.affected_files,
        )
        if rec is None:
            raise HTTPException(status_code=404, detail="Decision not found")

    assert rec is not None
    return DecisionRecordResponse.from_orm(rec)
