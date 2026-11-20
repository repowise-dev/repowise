"""/api/repos/{repo_id}/evidence/*: what stored runtime evidence measured.

Read-only and thin: run status is ``evidence.status``, the ranked rows are
``crud.measured_costs``, the per-function view is ``symbol_hotness``
through ``crud.hotness_for_symbols``, and test time is
``evidence.suite_cost.suite_costs``, the readers the CLI and MCP tools use.
Every measured value names its basis (workload class and name, commit, age,
samples); no row means unknown, never zero.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.evidence.status import evidence_status, run_status
from repowise.core.evidence.suite_cost import DEFAULT_LIMIT, suite_costs
from repowise.core.ingestion.symbol_identity import file_of_symbol
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas.runtime_evidence import (
    EvidenceRunsResponse,
    EvidenceStatusResponse,
    MeasuredFunctionsResponse,
    MeasuredResponse,
    SuiteCostResponse,
)

router = APIRouter(
    prefix="/api/repos",
    tags=["runtime-evidence"],
    dependencies=[Depends(verify_api_key)],
)

#: Rows per run a measured request may ask for.
MAX_MEASURED_LIMIT = 100


async def _repo(session: AsyncSession, repo_id: str) -> Any:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.get("/{repo_id}/evidence/status", response_model=EvidenceStatusResponse)
async def get_evidence_status(
    repo_id: str, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """The newest run of every workload with its freshness, and whether
    anything is ranked (``status``), with the command that moves it on."""
    repo = await _repo(session, repo_id)
    runs = await crud.list_evidence_runs(session, repo_id)
    status = await crud.measured_status(session, repo_id)
    return {
        "status": status,
        "command": crud.unranked_command(status),
        **evidence_status(runs, repo.head_commit),
    }


@router.get("/{repo_id}/evidence/runs", response_model=EvidenceRunsResponse)
async def list_evidence_runs(
    repo_id: str, session: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    """Every stored run, newest first. The store keeps a bounded history per
    workload, so the list is complete without paging."""
    repo = await _repo(session, repo_id)
    runs = await crud.list_evidence_runs(session, repo_id)
    return {
        "reference_commit": repo.head_commit,
        "runs": [run_status(run, repo.head_commit) for run in runs],
    }


@router.get("/{repo_id}/evidence/measured", response_model=MeasuredResponse)
async def get_measured(
    repo_id: str,
    run_id: str | None = Query(
        None, description="One run (a workload's newest) instead of every workload."
    ),
    limit: int = Query(10, ge=0, le=MAX_MEASURED_LIMIT, description="Rows per run."),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Each workload's newest run with its ranked production functions, best
    rank first: benchmark, entry point, load test and production runs under
    ``measured``, unit-test runs under ``measured_in_tests``."""
    await _repo(session, repo_id)
    view = await crud.measured_costs(session, repo_id, limit=limit, run_id=run_id)
    if view is None:
        status = await crud.measured_status(session, repo_id)
        # Something is ranked, just not in the asked run (an older run of its
        # workload, or one that ranks nothing).
        if status == "measured":
            status = "not_ranked"
        return {
            "basis": "unknown",
            "status": status,
            "command": crud.unranked_command(status),
            "measured": None,
            "measured_in_tests": None,
        }
    return {
        "basis": "measured",
        "status": "measured",
        "command": None,
        "measured": view.get("measured"),
        "measured_in_tests": view.get("measured_in_tests"),
    }


@router.get("/{repo_id}/evidence/functions", response_model=MeasuredFunctionsResponse)
async def get_measured_functions(
    repo_id: str,
    symbol_id: str | None = Query(None, max_length=4096, description="One function's id."),
    file: str | None = Query(None, max_length=4096, description="Every measured function in a file."),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """What runtime evidence says about one function, or each function of a
    file, from its basis run. A function with no row has no evidence."""
    if not symbol_id and not file:
        raise HTTPException(status_code=400, detail="Pass symbol_id or file")
    await _repo(session, repo_id)
    # Symbol ids carry forward slashes, whatever the caller typed.
    files = [file.replace("\\", "/")] if file else []
    rows = await crud.hotness_for_symbols(
        session, repo_id, [symbol_id] if symbol_id else [], files=files
    )
    return {"functions": [_function(row) for _, row in sorted(rows.items())]}


@router.get("/{repo_id}/evidence/tests", response_model=SuiteCostResponse)
async def get_suite_costs(
    repo_id: str,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_MEASURED_LIMIT, description="Tests and files."),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The slowest tests and the test time per production file, from stored
    JUnit timings; per file ``measured`` with a per-test coverage map, else
    ``inferred`` from the call graph."""
    repo = await _repo(session, repo_id)
    return await suite_costs(session, repo_id, repo.head_commit, limit=limit)


def _function(row: Any) -> dict[str, Any]:
    return {
        "symbol_id": row.symbol_id,
        "file_path": file_of_symbol(row.symbol_id),
        # Tiers above ranked are not calibrated, so no surface claims them.
        "tier": "ranked" if row.tier in ("ranked", "lead_candidate") else "measured",
        "rank": row.rank,
        "self_share": row.self_share,
        "total_share": row.total_share,
        "share_low": row.share_low,
        "share_high": row.share_high,
        "samples": row.samples,
        "run_samples": row.run_samples,
        "run_id": row.run_id,
        "run_count": row.run_count,
        "workload_name": row.workload_name,
        "workload_class": row.workload_class,
        "value_kind": row.value_kind,
        "ordering_only": row.ordering_only,
        "freshness": row.freshness,
        "evidence_commit_sha": row.evidence_commit_sha,
        "measured_at": row.measured_at,
        "repo_self_share": row.repo_self_share,
        "wait_share": row.wait_share,
        "library_share": row.library_share,
        "run_unlisted": row.run_unlisted,
    }
