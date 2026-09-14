"""Coverage routes: the measured map, and the graph-inferred one behind it.

``test_coverage`` is filled only by an ingested report, and most repositories
have none. Rather than answering "no coverage" the routes fall back to the
graph-inferred test map, tagged ``basis: "inferred"`` so a consumer can never
mistake it for a measurement.

The two bases never share a field. On the inferred basis ``summary``, ``files``
and ``modules`` come back empty and the answer rides in ``inferred``, so a UI
cannot render derived data through the measured code path by accident. Nothing
here derives a percentage from the inferred map: it has no line attribution, so
any ratio built from it would be a coverage figure the data cannot support.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.test_reachability import (
    call_graph_from_db,
    files_reached_by_tests,
    load_test_files,
    tests_reaching_by_tier,
)
from repowise.core.persistence import crud
from repowise.server.deps import get_db_session

from ._router import router


def _coverage_row_to_dict(row: Any, *, include_covered_lines: bool = False) -> dict:
    out: dict[str, Any] = {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
        "total_coverable_lines": row.total_coverable_lines,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "ingested_commit_sha": row.ingested_commit_sha,
    }
    if include_covered_lines:
        try:
            out["covered_lines"] = json.loads(row.covered_lines_json or "[]")
        except Exception:
            out["covered_lines"] = []
    return out


@router.get("/api/repos/{repo_id}/health/coverage")
async def health_coverage(
    repo_id: str,
    file_path: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    module_limit: int = Query(1000, ge=0, le=5000),
    include_inferred: bool = Query(True),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Coverage summary + per-file rows.

    Pass ``file_path`` to fetch a single file's full covered-line set.
    Without ``file_path`` we return the summary + a list of per-file
    rows trimmed by ``limit`` (covered_lines arrays stripped).

    ``limit`` caps ``files``; ``module_limit`` caps ``modules``. Separate
    parameters because they bound unrelated things — one is a page of files, the
    other a rollup as long as the repo's covered-directory count — and one
    ``limit`` governing both meant a caller asking for a single file row was
    told the repo had a single module.

    ``modules_total`` is always the full count, so a trimmed rollup can never be
    read as the whole repo. ``module_limit=0`` returns none of them, for callers
    that want the summary and nothing else.

    ``include_inferred=false`` declines the graph-inferred fallback. It is not a
    third cap: the fallback costs a read of every call edge in the repository
    plus every health metric, which is the right price for the tab and the wrong
    one for the tab *badge*, whose whole point is that it asks for one file row
    and no modules. A declined response omits ``basis`` rather than reporting
    ``"none"`` - the graph was not consulted, which is not the same as the graph
    having nothing to say.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # One narrow read of the whole table, shared by all three blocks below.
    # ``summary`` and ``modules`` are both repo-wide aggregates, so this cannot
    # be scoped by ``file_path`` or trimmed by ``limit`` — and none of the three
    # reads ``covered_lines_json``, which is most of the table's bytes.
    all_rows = await crud.load_coverage_for_repo(
        session, repo_id, include_covered_lines=False
    )
    if not all_rows:
        # No report was ever ingested. The graph can still answer "does a test
        # reach this", so answer that instead of an empty measured shape.
        if not include_inferred:
            return {
                "summary": _empty_summary(),
                "files": [],
                "modules": [],
                "modules_total": 0,
            }
        return await _inferred_coverage(session, repo_id, limit)
    summary = await crud.get_coverage_summary(session, repo_id, rows=all_rows)
    if summary.get("ingested_at") is not None:
        summary = {**summary, "ingested_at": summary["ingested_at"].isoformat()}

    if file_path:
        # The one caller that wants the covered-line set, for one file.
        detail = await crud.load_coverage_for_repo(
            session, repo_id, file_paths=[file_path], include_covered_lines=True
        )
        files = [_coverage_row_to_dict(r, include_covered_lines=True) for r in detail]
    else:
        rows_sorted = sorted(all_rows, key=lambda r: r.line_coverage_pct)
        files = [_coverage_row_to_dict(r) for r in rows_sorted[:limit]]
        # Attach per-file health score so the UI can render a coverage
        # x score matrix without a second request. Scoped to the rows we are
        # actually returning: a repo-wide read hydrates every metric row and
        # pays for the grouped deduction aggregate behind its worst-first
        # ordering, neither of which this join uses.
        metrics = await crud.get_health_metrics(
            session, repo_id, file_paths=[f["file_path"] for f in files]
        )
        metric_by_path = {m.file_path: m for m in metrics}
        for f in files:
            m = metric_by_path.get(f["file_path"])
            if m is not None:
                f["health_score"] = round(m.score, 2)
                f["nloc"] = m.nloc

    # Aggregate by directory for module-level bars (cheap; one pass).
    # Always over the repo-wide read, never over ``files``: this is what the
    # repo's coverage looks like by directory, not what this page of it does.
    modules: dict[str, dict[str, Any]] = {}
    for r in all_rows:
        mod = r.file_path.rsplit("/", 1)[0] if "/" in r.file_path else "(root)"
        bucket = modules.setdefault(mod, {"covered": 0, "total": 0, "files": 0})
        bucket["files"] += 1
        bucket["total"] += r.total_coverable_lines
        bucket["covered"] += round(r.line_coverage_pct / 100.0 * r.total_coverable_lines)
    module_rows = [
        {
            "module": name,
            "files": v["files"],
            "covered_lines": v["covered"],
            "total_lines": v["total"],
            "line_coverage_pct": (
                round(v["covered"] / v["total"] * 100.0, 2) if v["total"] else 0.0
            ),
        }
        for name, v in modules.items()
    ]
    module_rows.sort(key=lambda x: x["line_coverage_pct"])

    # The hybrid gap. A single stored row is enough to select the measured
    # basis for the whole repository, but most files in most repos have no
    # measured row at all (the lcov report never mentioned them) - here that is
    # 2,209 of 3,608 files. Those files are not unindexed and not untested: the
    # graph-inferred map has an answer for them and the all-or-nothing basis
    # rule currently withholds it. Serve the inferred map for exactly the files
    # with no measured row, so a partial ingest stops hiding the files it did
    # not mention. ``basis`` stays ``measured`` and ``inferred`` reports only
    # the non-measured set - a file is never in both, and measured rows never
    # wear inferred percentages (none are derived here).
    if include_inferred and not file_path:
        measured_paths = frozenset(r.file_path for r in all_rows)
        inferred = await _inferred_coverage(
            session, repo_id, limit, excluded_paths=measured_paths
        )
        if inferred.get("basis") == "inferred":
            inferred["inferred"]["measured_file_count"] = len(measured_paths)
            return {
                "basis": "measured",
                "summary": summary,
                "files": files,
                "modules": module_rows[:module_limit] if module_limit else [],
                "modules_total": len(module_rows),
                "inferred": inferred["inferred"],
            }

    return {
        "basis": "measured",
        "summary": summary,
        "files": files,
        "modules": module_rows[:module_limit] if module_limit else [],
        "modules_total": len(module_rows),
    }


def _empty_summary() -> dict[str, Any]:
    """The measured summary's zero shape.

    Sent on the inferred basis so the field keeps one type. It is empty because
    nothing measured this repo, which is a different statement from "measured at
    zero percent" - and the only field that distinguishes them is ``basis``.
    """
    return {
        "file_count": 0,
        "covered_lines": 0,
        "total_lines": 0,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "source_format": None,
        "mapping_partial": None,
        "ingested_at": None,
        "ingested_commit_sha": None,
    }


async def _inferred_coverage(
    session: AsyncSession,
    repo_id: str,
    limit: int,
    *,
    excluded_paths: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """The graph-inferred test map, in the shape the measured one leaves empty.

    Reuses the health engine's forward walk rather than the attributed reverse
    one. This is the batch question - "which files does any test reach" - which
    is one multi-source search over the whole graph, where the reverse walk runs
    a query per level per seed and is built for a change's files. Sharing the
    walk also means this can never disagree with the ``untested_hotspot``
    biomarker, which reads the same function.

    ``excluded_paths`` scopes the answer to the files *not* carrying a measured
    coverage row. On the hybrid basis the measured branch already answers those
    files; this map fills the gap for the rest, so a file is never in both.

    Counts are repo-wide over the scoped set even when ``files`` is trimmed,
    matching the measured branch's ``modules_total``: a truncated page must
    never read as the repo.

    Degrades to ``basis: "none"`` rather than raising. An unindexed graph is the
    honest unknown, and failing the tab to withhold a secondary signal is the
    wrong trade - the same call ``pr_blast._inferred_guarding_tests`` makes.
    """
    try:
        test_files = await load_test_files(session, repo_id)
        if not test_files:
            return {
                "basis": "none",
                "summary": _empty_summary(),
                "files": [],
                "modules": [],
                "modules_total": 0,
            }
        # Health metrics are the file list: already exclusion-filtered and
        # already worst-first, which is the order the ranked list wants, and
        # they carry the score and NLOC the chart plots. Test files are dropped
        # - "is this tested" is not a question about a test. ``excluded_paths``
        # (measured rows) are dropped too: those files are already answered by
        # the measured map.
        metrics = await crud.get_health_metrics(session, repo_id)
        rows = [
            m
            for m in metrics
            if m.file_path not in test_files and m.file_path not in excluded_paths
        ]
        if not rows:
            # No gap: every health row also carries a measured coverage row.
            # Return the empty shape without paying for the graph walk - a
            # fully-measured repo should not read every call edge on the tab
            # just to learn there is nothing to add.
            return {
                "basis": "inferred",
                "summary": _empty_summary(),
                "files": [],
                "modules": [],
                "modules_total": 0,
                "inferred": {
                    "files": [],
                    "files_total": 0,
                    "files_reached": 0,
                    "files_not_reached": 0,
                    "test_file_count": len(test_files),
                },
            }
        view = await call_graph_from_db(session, repo_id)
        reached = files_reached_by_tests(view, test_files)
    except Exception:
        return {
            "basis": "none",
            "summary": _empty_summary(),
            "files": [],
            "modules": [],
            "modules_total": 0,
        }

    reached_count = sum(1 for m in rows if m.file_path in reached)
    files = [
        {
            "file_path": m.file_path,
            "reached": m.file_path in reached,
            "health_score": round(m.score, 2),
            "nloc": m.nloc,
        }
        for m in rows[:limit]
    ]
    return {
        "basis": "inferred",
        "summary": _empty_summary(),
        "files": [],
        "modules": [],
        "modules_total": 0,
        "inferred": {
            "files": files,
            "files_total": len(rows),
            "files_reached": reached_count,
            "files_not_reached": len(rows) - reached_count,
            "test_file_count": len(test_files),
        },
    }


@router.get("/api/repos/{repo_id}/health/tests-reaching")
async def health_tests_reaching(
    repo_id: str,
    file_path: str = Query(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Which test files reach one file, and which tier of the graph found them.

    The attributed reverse walk, seeded with a single file - the shape it was
    built for. ``via`` separates ``call-graph`` (a test's calls run into this
    file) from the weaker ``import-graph`` (a test only imports it), so the file
    page can say which claim it is making.

    ``tests`` is capped at ``MAX_TESTS_PER_TARGET``; ``total`` is what the walk
    found and ``truncated`` says whether the two differ. A file reached by 124
    tests would otherwise report 50 as though that were the number.

    Always inferred; there is no measured form of this question, because a
    coverage report attributes lines to tests and this attributes files. A file
    nothing reaches returns ``basis: "none"`` rather than an empty ``inferred``
    answer, so "no test reaches this" stays distinguishable from "the graph had
    nothing to say".
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    empty = {
        "file_path": file_path,
        "basis": "none",
        "reached": False,
        "tests": [],
        "via": None,
        "total": 0,
        "truncated": False,
    }
    try:
        found = await tests_reaching_by_tier(session, repo_id, [file_path])
    except Exception:
        return empty
    reached = found.get(file_path)
    if reached is None or not reached.tests:
        return empty
    total = reached.total or len(reached.tests)
    return {
        "file_path": file_path,
        "basis": "inferred",
        "reached": True,
        "tests": reached.tests,
        "via": reached.via,
        # The walk's own count, which ``tests`` may be a trimmed alphabetical
        # slice of. Without it a caller renders the cap as the answer.
        "total": total,
        "truncated": total > len(reached.tests),
    }
