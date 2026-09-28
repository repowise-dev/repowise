"""/api/repos/{repo_id}/decisions — Architectural decision record endpoints."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import (
    AGREEMENT_KIND,
    ARCHITECTURAL_KIND,
    is_governing,
)
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


def _attach_signature(item: DecisionRecordResponse, signature) -> None:
    """Copy who signed onto a response row. A candidate is left null."""
    if signature is None:
        return
    item.accepter = signature.accepter or signature.artifact
    item.accepter_kind = signature.kind
    item.accepter_session = signature.session


async def _one_with_signature(session, repo_id: str, rec) -> DecisionRecordResponse:
    """One record, carrying its authority and who signed it.

    Both, never one: a consumer reading a null ``currency`` as "candidate"
    would otherwise call an accepted decision one and find a signature on it.
    """
    item = DecisionRecordResponse.from_orm(rec)
    item.currency = await crud.current_currency(session, rec)
    signatures = await crud.decision_signatures(session, repo_id, [rec])
    _attach_signature(item, signatures.get(rec.id))
    return item


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


def _page_of_lane(decisions: list, currencies: dict, lane: str, page: slice) -> list:
    """The records of *decisions* in *lane*, cut to *page*.

    ``candidates`` was already paged in SQL. Every other lane was over-fetched
    because its currency is derived, so its page is cut here.
    """
    in_lane = [d for d in decisions if _in_lane(currencies.get(d.id), lane)]
    if lane == "candidates":
        return in_lane
    return in_lane[page]


async def _attach_evidence(session: AsyncSession, items: list[DecisionRecordResponse]) -> None:
    """Give each row its evidence count and its top-ranked quote as a preview."""
    if not items:
        return
    rows = (
        await session.execute(
            select(DecisionEvidence)
            .where(DecisionEvidence.decision_id.in_([item.id for item in items]))
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
    # Acceptance is a SQL predicate, but every lane except ``candidates`` also
    # filters on a derived currency, so those over-fetch the accepted set (small,
    # since each acceptance is a human action) and are paged after derivation.
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
        decisions = _page_of_lane(decisions, currencies, lane, slice(offset, offset + limit))
    signatures = await crud.decision_signatures(session, repo_id, decisions)
    items = [DecisionRecordResponse.from_orm(d) for d in decisions]
    for item in items:
        item.currency = currencies.get(item.id)
        _attach_signature(item, signatures.get(item.id))
    await _attach_evidence(session, items)
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
    # No status filter: every status is a node, capped in priority order.
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
        agent_acceptance=policy.agent_acceptance,
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


@contextlib.contextmanager
def _value_error_as_400():
    """Report a policy the caller asked for but cannot have as a 400."""
    try:
        yield
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _apply_settings_update(policy, body: DecisionSettingsUpdate):
    """*policy* with the fields *body* sent applied, preset first."""
    from repowise.core.analysis.decisions.policy import preset_policy

    if body.preset is not None:
        with _value_error_as_400():
            # A preset names source membership, not a budget and not which
            # harnesses are read; what the caller did not send is theirs and
            # survives.
            policy = replace(
                preset_policy(body.preset),
                discovery=policy.discovery,
                harnesses=policy.harnesses,
                agent_acceptance=policy.agent_acceptance,
                capture_prompt=policy.capture_prompt,
            )
    if body.enabled is not None:
        policy = policy.with_enabled(body.enabled)
    if body.llm is not None:
        policy = policy.with_llm(body.llm)
    if body.agent_acceptance is not None:
        policy = policy.with_agent_acceptance(body.agent_acceptance)
    return _apply_source_and_discovery_patches(policy, body)


def _apply_source_and_discovery_patches(policy, body: DecisionSettingsUpdate):
    with _value_error_as_400():
        for key, patch in (body.sources or {}).items():
            policy = policy.with_source(key, enabled=patch.enabled, llm=patch.llm)
        discovery = body.discovery.model_dump(exclude_none=True) if body.discovery else {}
        if discovery:
            policy = policy.with_discovery(**discovery)
    return policy


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
    from repowise.core.analysis.decisions.policy_store import PolicyConflictError, write_policy

    repo_path = await _local_repo_path(session, repo_id)
    policy = _apply_settings_update(_load_policy_or_400(repo_path).policy, body)
    try:
        resolution = write_policy(repo_path, policy, expected_etag=body.etag)
    except PolicyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _settings_payload(repo_path, resolution)


async def _live_decision_id(session: AsyncSession, decision_id: str) -> str:
    """The id a caller-supplied decision id names today.

    A retired id follows its alias, so ids written down before a decision moved
    onto a derived id keep working. A live record always wins: unlike
    ``resolve_decision_id`` alone, this never redirects to a merge target. An
    id with neither record nor alias resolves to itself, so the caller 404s.
    """
    if await crud.get_decision(session, decision_id) is not None:
        return decision_id
    return await crud.resolve_decision_id(session, decision_id) or decision_id


async def _decision_in_repo(session: AsyncSession, repo_id: str, decision_id: str):
    """The record *decision_id* names today, or a 404 unless it is in *repo_id*."""
    rec = await crud.get_decision(session, await _live_decision_id(session, decision_id))
    if rec is None or rec.repository_id != repo_id:
        raise HTTPException(status_code=404, detail="Decision not found")
    return rec


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
    rec = await _decision_in_repo(session, repo_id, decision_id)
    return await _one_with_signature(session, repo_id, rec)


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
    rec = await _decision_in_repo(session, repo_id, decision_id)
    rows = await crud.list_decision_evidence(session, rec.id)
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
    rec = await _decision_in_repo(session, repo_id, decision_id)
    chain = await decision_graph.build_lineage_chain(session, rec.id)
    return {"lineage": [DecisionLineageEntry(**entry) for entry in chain]}


async def _is_accepted_with_scope(session: AsyncSession, existing) -> bool:
    """Whether *existing* is an accepted record that governs a scope.

    Asks what the stored record would lose, not what either side calls it: an
    agreement can be given a real scope.
    """
    if existing is None or not crud.names_a_scope(existing):
        return False
    return await crud.is_accepted(session, existing.id)


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

    A record the acceptance contract will not take is stored as a candidate
    instead of being refused, which is what ``repowise decision add`` does with
    the same input. That covers a record naming no file or module, which cannot
    be checked against the code or reach an agent editing a governed file, and
    a record stating no reason, which has not said why it binds. Discarding the
    fields the author did fill in would be worse than keeping the entry
    unaccepted. The response's ``status`` says which of the two happened, and a
    form can predict it from the same one field.
    """
    # ``upsert_decision`` dedups on the title and overwrites the scope, so a
    # scope-less re-post of an accepted decision would silently withdraw what
    # it governs. Refuse and name the record instead.
    existing = await crud.find_decision_by_title(
        session, repo_id, body.title, source="cli"
    )
    named = bool(body.affected_files or body.affected_modules)
    # What this body says, or what the record already is: a body naming no
    # kind must not un-agree a stored agreement.
    kind = body.kind or (existing.kind if existing is not None else ARCHITECTURAL_KIND)
    # An agreement names no file because its scope is the repository.
    # Requiring one would leave the noun permanently unacceptable.
    scoped = named or kind == AGREEMENT_KIND
    if not named and await _is_accepted_with_scope(session, existing):
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
        # None when the body named none: ``upsert_decision`` then leaves an
        # existing record's noun alone.
        kind=body.kind,
        source="cli",
        # No confidence: upsert_decision scores a manual entry.
    )
    if scoped:
        # A refused acceptance (e.g. no rationale) keeps the record as a candidate.
        with contextlib.suppress(crud.AcceptanceRefusedError):
            await crud.accept_decision(session, rec, accepter="web", kind="person")
    return await _one_with_signature(session, repo_id, rec)


async def _transition_status(
    session: AsyncSession, decision_id: str, status: str, superseded_by: str | None
):
    """Move a decision to *status* as a person on the web, or 400/404."""
    # The successor is a caller-supplied id too, and storing a retired one
    # would record a pointer that no longer resolves.
    if superseded_by is not None:
        superseded_by = await _live_decision_id(session, superseded_by)
    try:
        rec = await crud.update_decision_status(
            session,
            decision_id,
            status,
            superseded_by=superseded_by,
            accepter="web",
            kind="person",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if rec is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return rec


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
    left as ``None`` in the body is preserved, except that sending
    ``affected_files`` without ``affected_modules`` re-derives the modules
    from those files so the two halves of the scope cannot disagree.
    """
    rec = await _decision_in_repo(session, repo_id, decision_id)
    decision_id = rec.id

    if body.status is not None:
        rec = await _transition_status(session, decision_id, body.status, body.superseded_by)
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
    return await _one_with_signature(session, repo_id, rec)
