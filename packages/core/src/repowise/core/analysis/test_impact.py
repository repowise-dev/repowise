"""Canonical file-level test-impact analysis for PR blast-radius consumers.

The measured per-test coverage map and structural test reachability answer
different questions.  This module keeps both evidence sets, attaches their
basis to every recommendation, and exposes coverage availability separately
from an empty recommendation population.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.exclusion import is_excluded
from repowise.core.persistence.models import Repository

_BASIS_ORDER = {"measured": 0, "inferred": 1}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _freshness(ingested_commit: str | None, indexed_commit: str | None) -> dict[str, Any]:
    if not ingested_commit or not indexed_commit:
        return {
            "status": "unknown",
            "reason": "coverage_or_index_commit_unavailable",
            "ingested_commit": ingested_commit,
            "indexed_commit": indexed_commit,
        }
    return {
        "status": "current" if ingested_commit == indexed_commit else "stale",
        "reason": None
        if ingested_commit == indexed_commit
        else "coverage_commit_differs_from_index",
        "ingested_commit": ingested_commit,
        "indexed_commit": indexed_commit,
    }


def _recommendation_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        -len(row["source_files"]),
        _BASIS_ORDER[row["basis"]],
        row["repository"],
        row["test_id"],
    )


def legacy_guarding_tests(test_impact: dict[str, Any]) -> dict[str, Any]:
    """Compatibility projection for the historical ``guarding_tests`` block.

    Compatibility preserves the historical measured-first contract: measured
    rows win when any exist, otherwise inferred rows are the fallback. Consumers
    that need the additive union and per-row truth read ``recommendations``.
    """
    recommendations = test_impact["recommendations"]
    measured = [row for row in recommendations if row["basis"] == "measured"]
    selected = measured or [row for row in recommendations if row["basis"] == "inferred"]
    basis = selected[0]["basis"] if selected else "none"
    by_file = {
        row["source_file"]: row[f"{basis}_tests"]
        for row in test_impact["files"]
        if basis != "none" and row[f"{basis}_tests"]
    }
    return {
        "map_present": test_impact["coverage"]["map_present"],
        "basis": basis,
        "tests_to_run": [row["test_id"] for row in selected],
        "tests_to_run_with_basis": selected,
        "tests_to_run_total": len(selected),
        "tests_to_run_emitted": len(selected),
        "tests_to_run_truncated": False,
        "by_file": by_file,
        "analysis": test_impact["analysis"],
        "coverage": test_impact["coverage"],
        "inference": test_impact["inference"],
    }


async def analyze_test_impact(
    session: AsyncSession,
    repository_id: str,
    changed_files: list[str],
    *,
    repository_alias: str | None = None,
    exclude_spec: Any = None,
    change_status: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return one typed, untruncated recommendation population.

    The same result is consumed by MCP PR mode and the REST blast-radius route.
    Reads the coverage map and graph reachability for *changed_files*, then
    hands both to :func:`assemble_test_impact`.
    """
    from repowise.core.analysis.test_reachability import tests_reaching_by_tier
    from repowise.core.persistence.crud import get_test_coverage_summary, tests_covering

    changed = _changed_paths(changed_files, exclude_spec)
    fold = {
        "repository_id": repository_id,
        "repository": repository_alias,
        "exclude_spec": exclude_spec,
        "change_status": change_status,
    }
    if not changed:
        return assemble_test_impact([], {}, {}, {}, **fold)

    try:
        summary = await get_test_coverage_summary(session, repository_id)
        coverage_error: str | None = None
    except Exception as exc:  # analysis must degrade without losing graph candidates
        summary = {}
        coverage_error = type(exc).__name__

    try:
        indexed_commit = (
            await session.execute(
                select(Repository.head_commit).where(Repository.id == repository_id)
            )
        ).scalar_one_or_none()
    except Exception:
        indexed_commit = None

    measured: dict[str, list[Mapping[str, Any]]] = {}
    if summary.get("pair_count", 0) > 0:
        for path in changed:
            try:
                measured[path] = await tests_covering(
                    session, repository_id, path, lines=None
                )
            except Exception as exc:
                coverage_error = type(exc).__name__

    inference_error: str | None = None
    try:
        reached_by_file = await tests_reaching_by_tier(session, repository_id, changed)
    except Exception as exc:
        reached_by_file = {}
        inference_error = type(exc).__name__
    inferred = {
        path: {"tests": list(reached.all_tests or reached.tests), "via": reached.via}
        for path, reached in reached_by_file.items()
    }

    return assemble_test_impact(
        changed,
        measured,
        inferred,
        summary,
        indexed_commit=indexed_commit,
        coverage_error=coverage_error,
        inference_error=inference_error,
        **fold,
    )


def _changed_paths(changed_files: Iterable[str], exclude_spec: Any) -> list[str]:
    return sorted(
        {
            path
            for path in changed_files
            if path and not (exclude_spec and is_excluded(path, exclude_spec))
        }
    )


def assemble_test_impact(
    changed_files: Iterable[str],
    measured_by_file: Mapping[str, Sequence[Mapping[str, Any]]],
    inferred_by_file: Mapping[str, Mapping[str, Any]],
    coverage_summary: Mapping[str, Any],
    *,
    repository_id: str,
    repository: str | None = None,
    indexed_commit: str | None = None,
    exclude_spec: Any = None,
    change_status: Mapping[str, str] | None = None,
    coverage_error: str | None = None,
    inference_error: str | None = None,
) -> dict[str, Any]:
    """Fold coverage and reachability evidence into the test-impact result.

    Pure: no I/O. *measured_by_file* maps a changed path to its coverage-map
    rows (``test_id``, optional ``test_file`` and ``source_format``), read only
    when *coverage_summary* reports pairs. *inferred_by_file* maps a changed path
    to ``{"tests": [...], "via": tier}``. *coverage_summary* carries
    ``pair_count``, ``test_count``, ``source_file_count``, ``ingested_at``,
    ``source_format`` and ``ingested_commit_sha``. The two ``*_error`` arguments
    are the exception type names of a failed read, which mark that side degraded.

    *change_status* maps path to ``added``/``modified``/``deleted``/``renamed``.
    A deleted path has no head side to cover, so "no measured tests" there is a
    consequence of the deletion and not a coverage gap. Without it every path is
    treated as present, which is the historical behaviour.
    """
    changed = _changed_paths(changed_files, exclude_spec)
    repository = repository or repository_id
    if not changed:
        return {
            "recommendations": [],
            "recommendations_total": 0,
            "recommendations_emitted": 0,
            "recommendations_truncated": False,
            "recommendations_omitted": 0,
            "recommendations_by_primary_basis": {"measured": 0, "inferred": 0},
            "files": [],
            "files_total": 0,
            "files_without_measured_tests": [],
            "unknown_files": [],
            "deleted_files": [],
            "coverage": {
                "status": "unavailable",
                "reason": "no_changed_files",
                "map_present": False,
                "pair_count": 0,
                "test_count": 0,
                "source_file_count": 0,
                "changed_files_total": 0,
                "changed_files_with_measured_tests": 0,
                "changed_files_without_measured_tests": 0,
                "ingested_at": None,
                "source_format": None,
                "freshness": {
                    "status": "unknown",
                    "reason": "no_changed_files",
                    "ingested_commit": None,
                    "indexed_commit": None,
                },
            },
            "inference": {
                "status": "available",
                "reason": None,
                "changed_files_total": 0,
                "changed_files_with_candidates": 0,
                "candidates_before_dedup": 0,
            },
            "analysis": {
                "status": "partial",
                "stale": False,
                "partial": True,
                "degraded": False,
                "basis_categories": [],
            },
        }
    measured_tests_by_file: dict[str, list[str]] = {path: [] for path in changed}
    inferred_tests_by_file: dict[str, list[str]] = {path: [] for path in changed}
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    test_files: dict[tuple[str, str], set[str]] = defaultdict(set)

    summary = coverage_summary
    map_present = summary.get("pair_count", 0) > 0

    if map_present:
        for path in changed:
            for row in measured_by_file.get(path, ()):
                test_id = str(row["test_id"])
                test_file = row.get("test_file")
                runnable_file = str(test_file or test_id.split("::", 1)[0])
                if exclude_spec and is_excluded(runnable_file, exclude_spec):
                    continue
                measured_tests_by_file[path].append(test_id)
                key = (repository_id, test_id)
                if test_file:
                    test_files[key].add(str(test_file))
                detail = {
                    "basis": "measured",
                    "source_file": path,
                    "via": "coverage-map",
                    "source_format": row.get("source_format"),
                }
                if detail not in evidence[key]:
                    evidence[key].append(detail)

    if inference_error:
        inference_status = "degraded"
        inference_reason: str | None = f"{inference_error}: test_reachability_failed"
    else:
        inference_status = "available"
        inference_reason = None

    inferred_totals_by_file: dict[str, int] = {}
    for path, reached in inferred_by_file.items():
        if path not in inferred_tests_by_file:
            continue
        kept_tests = [
            test_id
            for test_id in reached["tests"]
            if not (exclude_spec and is_excluded(test_id, exclude_spec))
        ]
        inferred_totals_by_file[path] = len(kept_tests)
        for test_id in kept_tests:
            inferred_tests_by_file[path].append(test_id)
            key = (repository_id, test_id)
            test_files[key].add(test_id)
            detail = {
                "basis": "inferred",
                "source_file": path,
                "via": reached["via"],
                "source_format": None,
            }
            if detail not in evidence[key]:
                evidence[key].append(detail)

    recommendations: list[dict[str, Any]] = []
    for (repo_id, test_id), details in evidence.items():
        details.sort(
            key=lambda row: (
                _BASIS_ORDER[row["basis"]],
                row["source_file"],
                row["via"],
            )
        )
        bases = sorted({row["basis"] for row in details}, key=_BASIS_ORDER.__getitem__)
        recommendations.append(
            {
                "test_id": test_id,
                "test_file": sorted(test_files[(repo_id, test_id)])[0]
                if test_files[(repo_id, test_id)]
                else None,
                "repository_id": repo_id,
                "repository": repository,
                "basis": bases[0],
                "bases": bases,
                "source_files": sorted({row["source_file"] for row in details}),
                "evidence": details,
            }
        )
    recommendations.sort(key=_recommendation_sort_key)

    matched_files = sum(bool(tests) for tests in measured_tests_by_file.values())
    coverage_reason: str | None
    if coverage_error:
        coverage_status = "degraded"
        coverage_reason = f"{coverage_error}: coverage_query_failed"
    elif not map_present:
        coverage_status = "unavailable"
        coverage_reason = "no_per_test_coverage_map"
    elif 0 < matched_files < len(changed):
        coverage_status = "partial"
        coverage_reason = "coverage_map_matches_only_part_of_change"
    else:
        coverage_status = "available"
        coverage_reason = "no_matching_changed_files" if changed and matched_files == 0 else None

    freshness = _freshness(summary.get("ingested_commit_sha"), indexed_commit)
    coverage = {
        "status": coverage_status,
        "reason": coverage_reason,
        "map_present": map_present,
        "pair_count": int(summary.get("pair_count", 0) or 0),
        "test_count": int(summary.get("test_count", 0) or 0),
        "source_file_count": int(summary.get("source_file_count", 0) or 0),
        "changed_files_total": len(changed),
        "changed_files_with_measured_tests": matched_files,
        "changed_files_without_measured_tests": len(changed) - matched_files,
        "ingested_at": _iso(summary.get("ingested_at")),
        "source_format": summary.get("source_format"),
        "freshness": freshness,
    }
    inference = {
        "status": inference_status,
        "reason": inference_reason,
        "changed_files_total": len(changed),
        "changed_files_with_candidates": sum(bool(tests) for tests in inferred_tests_by_file.values()),
        "candidates_before_dedup": sum(inferred_totals_by_file.values()),
    }

    statuses = dict(change_status or {})
    files = []
    for path in changed:
        measured_tests = sorted(set(measured_tests_by_file[path]))
        inferred_tests = sorted(set(inferred_tests_by_file[path]))
        deleted = statuses.get(path) == "deleted"
        if measured_tests:
            status = "measured"
        elif inferred_tests:
            status = "inferred"
        elif deleted:
            # The path is gone at head. Nothing covers it because there is
            # nothing to cover, which is an answer rather than a missing map.
            status = "deleted"
        else:
            status = "unknown"
        files.append(
            {
                "source_file": path,
                "status": status,
                "change_status": statuses.get(path),
                "head_present": not deleted,
                "measured_tests": measured_tests,
                "measured_tests_total": len(measured_tests),
                "inferred_tests": inferred_tests,
                "inferred_tests_total": len(inferred_tests),
            }
        )

    stale = freshness["status"] == "stale"
    degraded = coverage_status == "degraded" or inference_status == "degraded"
    partial = coverage_status != "available" or inference_status != "available" or stale
    if degraded:
        analysis_status = "degraded"
    elif partial:
        analysis_status = "partial"
    else:
        analysis_status = "available"

    # A deleted path is excluded from both: it has no head side to be
    # uncovered, so counting it as a gap would overstate the coverage debt.
    no_measured = [
        row["source_file"]
        for row in files
        if not row["measured_tests"] and row["status"] != "deleted"
    ]
    unknown = [row["source_file"] for row in files if row["status"] == "unknown"]
    deleted_paths = [row["source_file"] for row in files if row["status"] == "deleted"]
    total = len(recommendations)
    basis_totals = {
        basis: sum(row["basis"] == basis for row in recommendations)
        for basis in ("measured", "inferred")
    }
    return {
        "recommendations": recommendations,
        "recommendations_total": total,
        "recommendations_emitted": total,
        "recommendations_truncated": False,
        "recommendations_omitted": 0,
        "recommendations_by_primary_basis": basis_totals,
        "files": files,
        "files_total": len(files),
        "files_without_measured_tests": no_measured,
        "unknown_files": unknown,
        "deleted_files": deleted_paths,
        "coverage": coverage,
        "inference": inference,
        "analysis": {
            "status": analysis_status,
            "stale": stale,
            "partial": partial,
            "degraded": degraded,
            "basis_categories": sorted(
                {basis for row in recommendations for basis in row["bases"]},
                key=_BASIS_ORDER.__getitem__,
            ),
        },
    }
