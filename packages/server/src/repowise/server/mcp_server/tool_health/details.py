"""get_health detail modes: one finding, plan or opportunity looked up by id."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.analysis.health.counts import DEFAULT_COUNTS
from repowise.core.analysis.health.scope import DEFAULT_SCOPE
from repowise.core.persistence.crud import get_health_finding_by_public_id
from repowise.core.persistence.models import HealthFinding
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_health.analysis_meta import _attach_repository_analysis_meta
from repowise.server.mcp_server.tool_health.pillars import (
    _PERFORMANCE_EVIDENCE_PAGE_CAP,
    _REFACTORING_EVIDENCE_CAP,
    _REFACTORING_EVIDENCE_PAGE_CAP,
    _REFACTORING_OPPORTUNITY_PREFIX,
    _REFACTORING_STEP_PAGE_CAP,
)
from repowise.server.mcp_server.tool_health.serialize import (
    _health_finding_id,
    _legacy_health_finding_id,
    _serialize_finding,
)
from repowise.server.services.performance_health import (
    PerformanceHealthService,
    evidence_block,
)
from repowise.server.services.refactoring_health import RefactoringHealthService


async def _detail_response(
    session: Any,
    repository: Any,
    reference_repository: str,
    *,
    finding_id: str | None,
    plan_id: str | None,
    opportunity_id: str | None,
    only_set: set[str],
    limit: int,
    cursor: int,
) -> dict[str, Any] | None:
    """Answer a lookup by id, or ``None`` when no selector was passed.

    At most one selector is set by the time this runs: ``_selector_conflict``
    refuses two before any read.
    """
    if finding_id:
        return await _finding_detail_response(
            session, repository, reference_repository, finding_id
        )
    if opportunity_id and opportunity_id.startswith(_REFACTORING_OPPORTUNITY_PREFIX):
        return await _refactoring_detail_response(
            session,
            repository,
            reference_repository,
            opportunity_id,
            evidence_only=only_set == {"refactoring_evidence"},
            limit=limit,
            cursor=cursor,
        )
    if opportunity_id:
        return await _performance_detail_response(
            session,
            repository,
            reference_repository,
            opportunity_id,
            evidence_only=only_set == {"performance_evidence"},
            limit=limit,
            cursor=cursor,
        )
    if plan_id:
        return await _plan_detail_response(session, repository, reference_repository, plan_id)
    return None


async def _finding_detail_response(
    session: Any, repository: Any, reference_repository: str, finding_id: str
) -> dict[str, Any]:
    match = await _resolve_finding(session, repository.id, finding_id, reference_repository)
    result = {
        "mode": "finding",
        "finding_id": finding_id,
        "finding": (
            _serialize_finding(match, reference_repository) if match else None
        ),
        "resolved": match is not None,
        "_meta": _build_meta(
            repository=repository,
            targets=[match.file_path] if match else None,
        ),
    }
    await _attach_repository_analysis_meta(session, repository, result["_meta"])
    return result


async def _plan_detail_response(
    session: Any, repository: Any, reference_repository: str, plan_id: str
) -> dict[str, Any]:
    """One stored plan, and the composed opportunity it is a step of, if any.

    An indexed seek and one hydration, not a full load and a linear
    scan: resolving one id used to cost every open plan in the repo.
    """
    service = RefactoringHealthService(session, repository.id, reference_repository)
    resolved = await service.plan_detail(plan_id)
    plan = resolved.get("plan") if resolved.get("resolved") else None
    if plan is not None:
        plan.setdefault("id", plan_id)
        plan["repository"] = reference_repository
    result = {
        "mode": "refactoring_plan",
        "plan_id": plan_id,
        "plan": plan,
        "resolved": bool(resolved.get("resolved")),
        "_meta": _build_meta(
            repository=repository,
            targets=[plan["file_path"]] if plan else None,
        ),
    }
    if resolved.get("opportunity_id"):
        result["opportunity_id"] = resolved["opportunity_id"]
        result["next_action"] = resolved["next_action"]
    elif plan is not None:
        result["opportunity_note"] = (
            "This plan is addressable but is not a step of any composed "
            "opportunity; a demoted clone is supporting evidence, not work."
        )
    await _attach_repository_analysis_meta(session, repository, result["_meta"])
    return result


def _selector_conflict(**selectors: str | None) -> dict[str, Any] | None:
    """Refuse two detail selectors instead of answering about one of them.

    Preferring whichever was checked first gave a caller a confident answer to
    a question they had not only asked, with no sign the other was dropped.
    """
    named = sorted(name for name, value in selectors.items() if value)
    if len(named) < 2:
        return None
    return {
        "mode": "conflict",
        "resolved": False,
        "reason": "mutually_exclusive_selectors",
        "selectors": named,
        "detail": "Pass exactly one of finding_id, plan_id, opportunity_id.",
    }


def _note_inapplicable_controls(
    result: dict[str, Any], scope: str, counts: str
) -> dict[str, Any]:
    """Name ``scope`` / ``counts`` when a detail lookup cannot honour them.

    These select a population; a lookup by id answers about one stored row and
    is always the calibrated reading. Silently accepting the control returned
    that row to a caller who believes they asked for a different one.
    """
    inapplicable = {
        name: value
        for name, value, default in (
            ("scope", scope, DEFAULT_SCOPE),
            ("counts", counts, DEFAULT_COUNTS),
        )
        if value is not None and value != default
    }
    if inapplicable:
        result["ignored_arguments"] = {**result.get("ignored_arguments", {}), **inapplicable}
    return result


async def _resolve_finding(
    session: Any, repository_id: str, finding_id: str, repository: str
) -> Any:
    """Find one health finding by any id form a caller can be holding.

    The public id is a column, so the common case is a seek. The scan is the
    compatibility path: a raw storage id, or an id minted before the column
    existed, still has to resolve.
    """
    match = await get_health_finding_by_public_id(session, repository_id, finding_id)
    if match is not None:
        return match
    rows = (
        (
            await session.execute(
                select(HealthFinding).where(
                    HealthFinding.repository_id == repository_id,
                    HealthFinding.status == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    return next(
        (
            row
            for row in rows
            if finding_id
            in {
                row.id,
                _health_finding_id(row, repository),
                _legacy_health_finding_id(row, repository),
            }
        ),
        None,
    )


async def _performance_detail_response(
    session: Any,
    repository: Any,
    reference_repository: str,
    opportunity_id: str,
    *,
    evidence_only: bool,
    limit: int,
    cursor: int,
) -> dict[str, Any]:
    """One causal opportunity, or one page of the evidence behind it.

    ``limit`` means what it means everywhere else, ``0`` included: reading it
    as "unset" would return a page the caller declined.
    """
    service = PerformanceHealthService(session, repository.id, reference_repository)
    evidence_limit = min(max(limit, 0), _PERFORMANCE_EVIDENCE_PAGE_CAP)
    if evidence_only:
        rows, total = await service.evidence(
            opportunity_id, limit=evidence_limit, offset=cursor
        )
        return {
            "mode": "performance_evidence",
            "opportunity_id": opportunity_id,
            "resolved": total > 0,
            **evidence_block(rows, total, cursor),
            "_meta": _build_meta(repository=repository),
        }
    detail = await service.detail(opportunity_id, evidence_limit=evidence_limit)
    file_path = detail.get("file_path")
    result = {
        "mode": "performance_opportunity",
        **detail,
        "_meta": _build_meta(
            repository=repository, targets=[file_path] if file_path else None
        ),
    }
    await _attach_repository_analysis_meta(session, repository, result["_meta"])
    return result


async def _refactoring_detail_response(
    session: Any,
    repository: Any,
    reference_repository: str,
    opportunity_id: str,
    *,
    evidence_only: bool,
    limit: int,
    cursor: int,
) -> dict[str, Any]:
    """One composed opportunity, or one page of the evidence behind it."""
    service = RefactoringHealthService(session, repository.id, reference_repository)
    if evidence_only:
        detail = await service.detail(
            opportunity_id,
            step_limit=0,
            evidence_limit=min(max(limit, 0), _REFACTORING_EVIDENCE_PAGE_CAP),
            evidence_offset=cursor,
            with_plans=False,
        )
        block = {k: v for k, v in detail.items() if k.startswith("evidence")}
        result = {
            "mode": "refactoring_evidence",
            "opportunity_id": opportunity_id,
            "resolved": bool(detail.get("resolved")),
            **block,
            "_meta": _build_meta(repository=repository),
        }
        if block.get("evidence_next_cursor") is not None:
            result["recovery"] = {
                "evidence": {
                    "remaining": block["evidence_total"] - block["evidence_next_cursor"],
                    "call": (
                        f"get_health(opportunity_id={opportunity_id!r}, "
                        f"only=['refactoring_evidence'], limit={limit}, "
                        f"cursor={block['evidence_next_cursor']})"
                    ),
                }
            }
        return result
    detail = await service.detail(
        opportunity_id,
        step_limit=min(max(limit, 0), _REFACTORING_STEP_PAGE_CAP),
        step_offset=cursor,
        evidence_limit=_REFACTORING_EVIDENCE_CAP,
    )
    file_path = detail.get("file_path")
    if not detail.get("resolved"):
        detail.setdefault(
            "model_state", _refactoring_model_state(opportunity_id)
        )
    result = {
        "mode": "refactoring_opportunity",
        **detail,
        "_meta": _build_meta(
            repository=repository, targets=[file_path] if file_path else None
        ),
    }
    await _attach_repository_analysis_meta(session, repository, result["_meta"])
    return result


def _refactoring_model_state(opportunity_id: str) -> dict[str, Any]:
    """Tell a stale-model id apart from a wrong one, from the string alone."""
    from repowise.core.analysis.health.refactoring.identity import (
        REFACTORING_MODEL_VERSION,
        model_state,
    )

    state = model_state(
        opportunity_id.replace(_REFACTORING_OPPORTUNITY_PREFIX, "refac", 1)
    )
    state["public_id"] = opportunity_id
    state["refactoring_model_version"] = REFACTORING_MODEL_VERSION
    return state
