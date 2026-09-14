"""/api/repos/{repo_id}/decisions — Architectural decision record endpoints."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import is_governing
from repowise.core.persistence import crud, decision_graph
from repowise.core.persistence.models import DecisionEvidence
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import (
    DecisionCodeEdge,
    DecisionCountsResponse,
    DecisionCreate,
    DecisionDiscoveryBudget,
    DecisionEvidenceListResponse,
    DecisionEvidenceResponse,
    DecisionGraphEdge,
    DecisionGraphNode,
    DecisionGraphResponse,
    DecisionHealthResponse,
    DecisionLaneCountsResponse,
    DecisionLineageEntry,
    DecisionLineageResponse,
    DecisionRecordResponse,
    DecisionSettings,
    DecisionSettingsUpdate,
    DecisionSourceState,
    DecisionStatusUpdate,
)
from repowise.server.schemas.decisions import EvidencePreview

router = APIRouter(
    tags=["decisions"],
    dependencies=[Depends(verify_api_key)],
)


def _in_lane(currency: str | None, lane: str) -> bool:
    """Whether a record at *currency* belongs in review lane *lane*.

    ``None`` means no acceptance row, which is the whole definition of a
    candidate. The accept/candidate half of this is pushed into SQL by
    ``list_decisions(accepted=...)``; only the currency, which comes from the
    record's scope and staleness rather than from the acceptance row, is
    resolved here.
    """
    if lane == "candidates":
        return currency is None
    if lane == "governing":
        return currency is not None and is_governing(currency)
    if lane == "history":
        return currency in ("superseded", "dismissed")
    return currency == lane


#: How many accepted records a currency-derived lane scans before paging in
#: Python. Matches the endpoint's own ``limit`` ceiling: past it the lane would
#: need the currency in SQL, which the derivation cannot give it.
_LANE_SCAN_CAP = 500


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
    lane: str | None = Query(
        None,
        pattern="^(candidates|governing|active|needs_review|uncheckable|history)$",
        description=(
            "Review lane. candidates: never accepted. governing: accepted and "
            "still binding. history: accepted and withdrawn. Applied after the "
            "page is fetched, because the lane is a join and not a column."
        ),
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query(
        "priority",
        pattern="^(priority|recent)$",
        description="priority: confirmed rules first, then likeliest proposals. recent: newest first.",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> list[DecisionRecordResponse]:
    """List architectural decision records for a repository.

    Each row carries an ``evidence_preview`` (the top-ranked evidence row's
    verbatim quote) plus the total ``evidence_count``, so the table can show
    provenance without N+1 calls to the per-decision /evidence endpoint, and a
    ``currency`` naming what its acceptance currently amounts to. A row with no
    ``currency`` is a candidate: nobody has accepted it.

    Defaults to ``sort=priority``. Newest-first buried every confirmed
    decision under the unreviewed proposals the indexer had just mined, so
    page one was entirely machine guesses.
    """
    # The acceptance half of the lane is a SQL predicate, so a page of a lane
    # is a page of that lane. The currency half cannot be: ``needs_review`` and
    # ``uncheckable`` are derived from the record's scope and staleness. Those
    # lanes therefore over-fetch the accepted set and cut the page afterwards,
    # which is affordable because an accepted decision requires a human action
    # and the set stays small by construction.
    # Every lane but ``candidates`` filters the accepted set by currency, and a
    # currency is derived from the record rather than stored, so the page has
    # to be cut after the derivation. ``governing`` is in here for the same
    # reason as the rest: cutting first returned an empty tab on a repository
    # whose newest accepted records had all been superseded.
    derived = lane is not None and lane != "candidates"
    decisions = await crud.list_decisions(
        session,
        repo_id,
        status=status,
        source=source,
        tag=tag,
        module=module,
        include_proposed=include_proposed,
        accepted=None if lane is None else lane != "candidates",
        # History has to reach a decision that was accepted and then dismissed,
        # which carries a tombstone status the default listing hides.
        include_dismissed=lane == "history",
        limit=max(_LANE_SCAN_CAP, offset + limit) if derived else limit,
        offset=0 if derived else offset,
        sort=sort,
    )
    currencies = await crud.decision_currencies(session, repo_id, decisions)
    if lane is not None:
        decisions = [d for d in decisions if _in_lane(currencies.get(d.id), lane)]
    if derived:
        decisions = decisions[offset : offset + limit]
    items = [DecisionRecordResponse.from_orm(d) for d in decisions]
    for item in items:
        item.currency = currencies.get(item.id)

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
    response_model=DecisionHealthResponse,
)
async def decision_health(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
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
    "/api/repos/{repo_id}/decisions/counts",
    response_model=DecisionCountsResponse,
)
async def decision_counts(
    repo_id: str,
    source: str | None = Query(None, description="Filter by source"),
    tag: str | None = Query(None, description="Filter by tag"),
    module: str | None = Query(None, description="Filter by module path"),
    include_proposed: bool = Query(True),
    session: AsyncSession = Depends(get_db_session),
) -> DecisionCountsResponse:
    """Counts by status, as a grouped COUNT rather than a page of rows.

    Declared above ``/{decision_id}`` on purpose: FastAPI matches in
    declaration order, so a literal sub-path below it would be swallowed by
    the parameterised route and "counts" would be looked up as a decision id.
    """
    counts = await crud.count_decisions_by_status(
        session,
        repo_id,
        source=source,
        tag=tag,
        module=module,
        include_proposed=include_proposed,
    )
    return DecisionCountsResponse(
        total=counts["total"],
        active=counts["active"],
        proposed=counts["proposed"],
        superseded=counts["superseded"],
        deprecated=counts["deprecated"],
    )


@router.get(
    "/api/repos/{repo_id}/decisions/lane-counts",
    response_model=DecisionLaneCountsResponse,
)
async def decision_lane_counts(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DecisionLaneCountsResponse:
    """Counts per review lane.

    Separate from ``/counts``, which groups the ``status`` column. That column
    is the projection kept in step for readers that predate the acceptance
    split, so its ``active`` and this endpoint's ``active`` are different
    questions: a record can be stored active and have no acceptance at all.
    A tab row must not be labelled from the other one's answer.

    Declared above ``/{decision_id}`` for the same reason ``/counts`` is:
    FastAPI matches in declaration order.
    """
    return DecisionLaneCountsResponse(
        **await crud.count_decisions_by_lane(session, repo_id)
    )


@router.get(
    "/api/repos/{repo_id}/decisions/graph",
    response_model=DecisionGraphResponse,
)
async def get_decision_graph(
    repo_id: str,
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
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


# ---------------------------------------------------------------------------
# Capture policy
#
# Declared before the dynamic ``/{decision_id}`` GET so the static ``settings``
# path wins. Backed by ``.repowise/config.yaml``, so it needs a local checkout.
# ---------------------------------------------------------------------------


async def _local_repo_path(session: AsyncSession, repo_id: str) -> Path:
    repo = await crud.get_repository(session, repo_id)
    if repo is None or not repo.local_path:
        raise HTTPException(status_code=404, detail=f"repository not found: {repo_id}")
    repo_path = Path(repo.local_path)
    if not repo_path.exists():
        raise HTTPException(
            status_code=404, detail="repository checkout not accessible on this server"
        )
    return repo_path


def _settings_payload(repo_path: Path, resolution) -> DecisionSettings:
    from repowise.core.analysis.decisions.policy_store import policy_etag

    policy = resolution.policy
    available = _provider_available(repo_path)
    return DecisionSettings(
        enabled=policy.enabled,
        llm=policy.llm,
        preset=policy.preset_name(),
        discovery=DecisionDiscoveryBudget(**policy.discovery.to_dict()),
        sources=[
            DecisionSourceState(**rt.to_dict())
            for rt in policy.runtime(provider_available=available)
        ],
        provider_available=available,
        warnings=list(resolution.warnings),
        legacy_keys=list(resolution.legacy_keys),
        etag=policy_etag(policy),
    )


def _load_policy_or_400(repo_path: Path):
    """Resolve the policy, turning an unparseable config into a 400.

    A malformed ``decisions:`` block is a warning, but a malformed *file* never
    reaches the resolver, so it would otherwise surface as a 500 with nothing
    the user could act on.
    """
    from repowise.core.analysis.decisions.policy_store import load_policy
    from repowise.core.repo_config import RepoConfigError

    try:
        return load_policy(repo_path)
    except RepoConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _provider_available(repo_path: Path) -> bool:
    """Whether a provider resolves, without constructing one."""
    from repowise.core.providers.llm.registry import provider_available_for_repo

    return provider_available_for_repo(repo_path)


@router.get(
    "/api/repos/{repo_id}/decisions/settings",
    response_model=DecisionSettings,
)
async def get_decision_settings(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DecisionSettings:
    """The resolved decision capture policy and source registry."""
    repo_path = await _local_repo_path(session, repo_id)
    return _settings_payload(repo_path, _load_policy_or_400(repo_path))


@router.put(
    "/api/repos/{repo_id}/decisions/settings",
    response_model=DecisionSettings,
)
async def update_decision_settings(
    repo_id: str,
    body: DecisionSettingsUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> DecisionSettings:
    """Apply a partial policy change to ``.repowise/config.yaml``.

    Omitted fields keep their current value, so a UI can send one switch.
    ``preset`` is applied before the per-source overrides.
    """
    from repowise.core.analysis.decisions.policy import preset_policy
    from repowise.core.analysis.decisions.policy_store import PolicyConflictError, write_policy

    repo_path = await _local_repo_path(session, repo_id)
    policy = _load_policy_or_400(repo_path).policy

    if body.preset is not None:
        try:
            # A preset names source membership, not a budget; the budget the
            # caller did not send is theirs and survives.
            policy = replace(preset_policy(body.preset), discovery=policy.discovery)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.enabled is not None:
        policy = policy.with_enabled(body.enabled)
    if body.llm is not None:
        policy = policy.with_llm(body.llm)
    for key, patch in (body.sources or {}).items():
        try:
            policy = policy.with_source(key, enabled=patch.enabled, llm=patch.llm)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    patch = body.discovery.model_dump(exclude_none=True) if body.discovery else {}
    if patch:
        try:
            policy = policy.with_discovery(**patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        resolution = write_policy(repo_path, policy, expected_etag=body.etag)
    except PolicyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _settings_payload(repo_path, resolution)


async def _live_decision_id(session: AsyncSession, decision_id: str) -> str:
    """The id a caller-supplied decision id names today.

    Only for an id that no longer names a record. Ids get retired underneath
    the places they were written down when a decision moves onto a derived id,
    and following the alias is what keeps those working instead of reading as
    deleted.

    A live record always wins, so this cannot redirect one request to a
    different decision. ``resolve_decision_id`` alone would: it follows a merge
    even when the merged record still exists, which is right where the caller
    is asking about the constraint, and wrong here, where the caller named a
    row it is looking at in the candidates lane. An id with neither record nor
    alias resolves to itself, so the handler still raises its own 404.
    """
    if await crud.get_decision(session, decision_id) is not None:
        return decision_id
    return await crud.resolve_decision_id(session, decision_id) or decision_id


@router.get(
    "/api/repos/{repo_id}/decisions/{decision_id}",
    response_model=DecisionRecordResponse,
)
async def get_decision(
    repo_id: str,
    decision_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DecisionRecordResponse:
    """Get a single decision record by ID."""
    decision_id = await _live_decision_id(session, decision_id)
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    return DecisionRecordResponse.from_orm(rec)


@router.get(
    "/api/repos/{repo_id}/decisions/{decision_id}/evidence",
    response_model=DecisionEvidenceListResponse,
)
async def list_decision_evidence(
    repo_id: str,
    decision_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return provenance evidence rows for a single decision record.

    Returns ``{"evidence": [...]}`` where each item carries the verbatim source
    quote, evidence file/line/commit, per-source confidence, and verification
    badge (``exact`` | ``fuzzy`` | ``unverified``). 404 if the decision does not
    exist or belongs to a different repository.
    """
    decision_id = await _live_decision_id(session, decision_id)
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    rows = await crud.list_decision_evidence(session, decision_id)
    return {"evidence": [DecisionEvidenceResponse.from_orm(r) for r in rows]}


@router.get(
    "/api/repos/{repo_id}/decisions/{decision_id}/lineage",
    response_model=DecisionLineageResponse,
)
async def get_decision_lineage(
    repo_id: str,
    decision_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return the lineage chain for a decision (root → … → current).

    Walks ``supersedes``/``refines`` edges back to the earliest ancestor so the
    UI can render a timeline. An isolated decision returns a single-entry chain.
    404 if the decision does not exist or belongs to a different repository.
    """
    decision_id = await _live_decision_id(session, decision_id)
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
    session: AsyncSession = Depends(get_db_session),
) -> DecisionRecordResponse:
    """Create a decision record, accepting it when it names a scope.

    Typing a decision by hand is an acceptance, but it is recorded as one
    rather than written straight into the status column, so this surface and
    the CLI agree about what made the record govern.

    A record naming no file or module is stored as a candidate instead of
    being refused, which is what ``repowise decision add`` does with the same
    input. It cannot be checked against the code and cannot reach an agent
    editing a governed file, so it cannot govern; discarding the fields the
    author did fill in would be worse. The response's ``status`` says which of
    the two happened, and a form can predict it from the same one field.
    """
    # ``upsert_decision`` dedups on the title and overwrites the scope with
    # whatever the body carries, so a second post of an accepted decision's
    # title with no files would clear the scope it governs and leave its
    # acceptance row pointing at a record that no longer binds. Refuse, and
    # name the record, rather than quietly retiring somebody's decision from a
    # call that says "create".
    existing = await crud.find_decision_by_title(
        session, repo_id, body.title, source="cli"
    )
    scoped = bool(body.affected_files or body.affected_modules)
    if existing is not None and not scoped and await crud.is_accepted(
        session, existing.id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{existing.title!r} is already an accepted decision "
                f"({existing.id}). Recording it again without the files it "
                "governs would withdraw its scope. Edit it instead, or post it "
                "with affected_files."
            ),
        )

    rec = await crud.upsert_decision(
        session,
        repository_id=repo_id,
        title=body.title,
        status="proposed",
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
    if scoped:
        try:
            await crud.accept_decision(session, rec, accepter="web")
        except crud.AcceptanceRefusedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DecisionRecordResponse.from_orm(rec)


@router.patch(
    "/api/repos/{repo_id}/decisions/{decision_id}",
    response_model=DecisionRecordResponse,
)
async def patch_decision(
    repo_id: str,
    decision_id: str,
    body: DecisionStatusUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> DecisionRecordResponse:
    """Update a decision record.

    Accepts status transitions (confirm / deprecate / supersede) and / or
    governance edits (``affected_modules``, ``affected_files``). Any field
    left as ``None`` in the body is preserved.
    """
    decision_id = await _live_decision_id(session, decision_id)
    rec = await crud.get_decision(session, decision_id)
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")

    if body.status is not None:
        # The successor is a caller-supplied id too, and storing a retired one
        # would record a pointer that no longer resolves.
        superseded_by = body.superseded_by
        if superseded_by is not None:
            superseded_by = await _live_decision_id(session, superseded_by)
        try:
            rec = await crud.update_decision_status(
                session,
                decision_id,
                body.status,
                superseded_by=superseded_by,
                accepter="web",
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
