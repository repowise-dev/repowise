"""MCP tool for live commit and range change-risk scoring."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

import pathspec
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from repowise.core.analysis.change_risk import (
    change_risk_payload,
    normalize_extensions,
    score_live_change,
)
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

#: Cap on the line-precise impacted-test list, matching the get_risk directive's
#: ``tests_to_run`` cap so both surfaces stay glanceable. ``total`` and
#: ``truncated`` report the overflow rather than silently dropping it.
_IMPACTED_TESTS_LIMIT = 10

#: Cap on the per-file prior-fix list, matching ``_IMPACTED_TESTS_LIMIT`` so both
#: per-file blocks in this response stay the same size.
_PRIOR_FIXES_LIMIT = 10


@mcp.tool()
async def get_change_risk(
    revspec: str = "HEAD",
    repo: str | None = None,
    extensions: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    baseline: int = 200,
) -> dict:
    """Score a live commit or ``base..head`` range from its diff shape.

    Use this for a pre-merge score of a commit or PR range. It is distinct from
    ``get_risk``, which assesses indexed files and PR blast radius. ``extensions``
    restricts counted suffixes; ``exclude_patterns`` omits gitignore-style paths.
    Both filters also apply to the baseline used for the repository percentile.

    Prefer ``risk_percentile`` as the indicator of change risk: it ranks this
    change against sampled recent commits in the same repository. Summarize it
    with ``review_priority`` and ``classification``. ``score``, ``probability``,
    and ``level`` are secondary corpus-calibrated context, the fallback only
    when ``risk_percentile`` is unavailable.

    ``impacted_tests`` names the tests the per-test coverage map proves execute
    the change's changed *lines* (line-precise, narrower than get_risk's
    file-level ``tests_to_run``), with ``missing_tests`` buckets for changed
    lines no test covers. Its ``status`` is ``no_map`` (unknown, run the full
    suite), never "untested", when no map is ingested.

    ``prior_fixes`` appears only when the changed files carry counted bug fixes:
    per-file counts, never a commit name.

    Args:
        revspec: Commit or ``base..head`` range to score. Defaults to ``HEAD``.
        repo: Repository alias in workspace mode; omit for the default repository.
        extensions: File suffixes to count, for example ``[".py", ".ts"]``.
        exclude_patterns: Gitignore-style paths to omit, for example ``["tests/", "*.md"]``.
        baseline: Recent commits to sample for percentile ranking; 0 disables it.
    """
    if repo == "all":
        return _unsupported_repo_all("get_change_risk")
    ctx = await _resolve_repo_context(repo)
    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(
            score_live_change,
            ctx.path,
            revspec,
            extensions=tuple(extensions or ()),
            exclude_patterns=tuple(exclude_patterns or ()),
            baseline=baseline,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or str(exc)
        return {"error": f"Could not read change {revspec!r}: {detail}"}
    except subprocess.TimeoutExpired:
        return {"error": f"git timed out reading change {revspec!r}."}
    payload = change_risk_payload(result)
    if result.features.nf == 0:
        payload["warning"] = (
            f"No counted file changes in {revspec!r} "
            "(check the revspec, extensions, or exclusion filters)."
        )
    # Changed lines over the SAME file universe the score counted (its
    # extensions + riskignore + request excludes), so nothing downstream
    # disagrees with the score about which files the change touches. Read once
    # and shared: both blocks below need it and git is the expensive part.
    # Skipped entirely without an index, because neither block can say anything
    # then and the git call is the expensive half of this response.
    changed: dict[str, set[int]] = {}
    changed_error: tuple[str, str] | None = None
    if getattr(ctx, "session_factory", None) is not None:
        changed, changed_error = await _changed_in_scope(
            str(ctx.path),
            revspec,
            normalize_extensions(tuple(extensions or ())),
            result.riskignore_excludes + result.request_excludes,
        )
    payload["impacted_tests"] = await _impacted_tests_block(ctx, changed, changed_error)
    prior_fixes = await _prior_fixes_block(ctx, changed)
    if prior_fixes is not None:
        payload["prior_fixes"] = prior_fixes
    # source: live_git marks that this response is computed from the working
    # checkout's git, not the index, so index freshness does not apply to it.
    payload["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - started) * 1000,
        extra={"source": "live_git"},
    )
    return payload


def _normalize_revspec(revspec: str) -> str:
    """Mirror ``score_live_change``'s three-dot handling for ``changed_lines``.

    ``changed_lines`` verifies each side of a ``base..head`` range as a ref, so a
    three-dot ``base...head`` (whose head parses as ``.head``) would fail its
    ref check. Strip the extra dot to the two-dot form the scorer already uses.
    """
    if ".." in revspec:
        base, _, head = revspec.partition("..")
        head = head.lstrip(".") or "HEAD"
        return f"{base}..{head}"
    return revspec


def _filter_changed(
    changed: dict[str, set[int]],
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
) -> dict[str, set[int]]:
    """Restrict changed-line files to the score's counted universe.

    ``changed_lines`` applies no suffix/exclude filtering, so without this a
    change scored ``nf == 0`` under an extension filter could still surface
    impacted tests for the filtered-out files. Uses the same ``endswith`` +
    gitwildmatch rules as the numstat accumulator.
    """
    spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns)
    out: dict[str, set[int]] = {}
    for path, lines in changed.items():
        if extensions and not path.endswith(extensions):
            continue
        if spec.match_file(path):
            continue
        out[path] = lines
    return out


def _empty_impacted(status: str, summary: str) -> dict[str, Any]:
    """Uniform impacted-tests block for the degraded (no tests to name) paths."""
    return {
        "status": status,
        "map_present": False,
        "tests": [],
        "total": 0,
        "truncated": False,
        "missing_tests": {
            "untested_changes": [],
            "stale_test_candidates": [],
            "covered": [],
            "no_coverage_data": [],
        },
        "summary": summary,
    }


def _serialize_missing(report: Any) -> dict[str, Any]:
    """Render the detect_missing_tests dataclass buckets as JSON-ready dicts."""
    return {
        "untested_changes": [
            {
                "source_file": u.source_file,
                "uncovered_lines": u.uncovered_lines,
                "changed_line_count": u.changed_line_count,
            }
            for u in report.untested_changes
        ],
        "stale_test_candidates": [
            {
                "source_file": s.source_file,
                "covering_test_files": s.covering_test_files,
                "covering_test_ids_without_file": s.covering_test_ids_without_file,
            }
            for s in report.stale_test_candidates
        ],
        "covered": list(report.covered),
        # ``no_data`` is "file not in the map" = unknown, never "untested".
        "no_coverage_data": list(report.no_data),
    }


async def _changed_in_scope(
    repo_path: str,
    revspec: str,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
) -> tuple[dict[str, set[int]], tuple[str, str] | None]:
    """The change's changed lines, restricted to the score's counted universe.

    Returns ``(changed, error)`` where *error* is a ``(status, summary)`` pair
    for the degraded paths, so the callers below can report the same reason
    without each re-reading git. Shared because ``changed_lines`` shells out and
    two blocks want the same answer.
    """
    from repowise.core.analysis.changed_lines import changed_lines

    try:
        changed, _label = await asyncio.to_thread(
            changed_lines, repo_path, _normalize_revspec(revspec)
        )
    except ValueError as exc:
        return {}, ("unknown", f"Could not read changed lines: {exc}")
    except (subprocess.SubprocessError, OSError):
        return {}, ("unknown", "Could not read changed lines from git.")

    changed = _filter_changed(changed, extensions, exclude_patterns)
    if not changed:
        return {}, ("no_source_line_changes", "No changed source lines to map to tests.")
    return changed, None


async def _prior_fixes_block(ctx: Any, changed: dict[str, set[int]]) -> dict[str, Any] | None:
    """Past bug fixes that landed on the lines this change touches, or ``None``.

    Aggregate only. No inducing commit is named: file-level SZZ measured 74.5%
    precision against the frozen judgments, enough to count fixes and not enough
    to accuse the commit that caused one.

    ``overlapping_lines`` is the honest weak signal and is labelled as such. A
    fix's stored ranges are line numbers on ITS parent commit while the change's
    are line numbers now, so any commit in between shifts them; the count says
    "this neighbourhood has been patched before", never "this exact line". The
    per-file fix count beside it carries no such caveat.

    Silent (``None``) when the index has no fix events for these files at all,
    so a repo without the feature grows no noise block.
    """
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import FixEvent

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None or not changed:
        return None

    try:
        async with get_session(session_factory) as session:
            repo_id = (await _get_repo(session)).id
            res = await session.execute(
                select(FixEvent).where(
                    FixEvent.repository_id == repo_id,
                    FixEvent.file_path.in_(list(changed)),
                    FixEvent.shape_kind == "code_fix",
                )
            )
            events = list(res.scalars().all())
    except LookupError:
        return None
    except SQLAlchemyError:
        # A pre-fix-events index has no table to read; that is silence, not an
        # error the caller should have to handle.
        return None

    if not events:
        return None

    per_file: dict[str, dict[str, Any]] = {}
    for event in events:
        entry = per_file.setdefault(
            event.file_path, {"file_path": event.file_path, "fix_count": 0, "overlapping_lines": 0}
        )
        entry["fix_count"] += 1
        entry["overlapping_lines"] += _overlap_count(
            changed[event.file_path], event.old_ranges_json
        )
        committed_at = event.committed_at
        if isinstance(committed_at, datetime):
            moment = committed_at if committed_at.tzinfo else committed_at.replace(tzinfo=UTC)
            days = max(0, (datetime.now(UTC) - moment).days)
            entry["last_fix_days_ago"] = min(entry.get("last_fix_days_ago", days), days)

    files = sorted(
        per_file.values(),
        key=lambda f: (-f["overlapping_lines"], -f["fix_count"], f["file_path"]),
    )
    # Distinct commits, not rows. There is one row per (fix_sha, file_path), so
    # summing per-file counts would report one commit that fixed three of the
    # changed files as "3 past bug fixes". The per-file counts are per-file and
    # stay as they are.
    total = len({event.fix_sha for event in events})
    return {
        "files": files[:_PRIOR_FIXES_LIMIT],
        "truncated": len(files) > _PRIOR_FIXES_LIMIT,
        "total_fixes": total,
        "files_with_fixes": len(files),
        "line_overlap": "approximate",
        "summary": (
            f"{total} past bug-fix commit(s) touched {len(files)} of the changed file(s). "
            "Line overlap is approximate (past ranges are numbered on their own "
            "parent commit); the per-file counts are not."
        ),
    }


def _overlap_count(changed_lines_now: set[int], old_ranges_json: str) -> int:
    """How many of the change's lines fall inside a past fix's replaced ranges."""
    try:
        ranges = json.loads(old_ranges_json or "[]")
    except (TypeError, ValueError):
        return 0
    if not isinstance(ranges, list):
        return 0
    hits = 0
    for span in ranges:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            continue
        try:
            lo, hi = int(span[0]), int(span[1])
        except (TypeError, ValueError):
            # Same defensiveness as the json.loads above: a malformed range must
            # not take down the whole get_change_risk call.
            continue
        hits += sum(1 for line in changed_lines_now if lo <= line <= hi)
    return hits


async def _impacted_tests_block(
    ctx: Any,
    changed: dict[str, set[int]],
    changed_error: tuple[str, str] | None,
) -> dict[str, Any]:
    """Line-precise impacted tests + honest missing-test buckets for the change.

    Built on the same core functions the CLI (``repowise impacted-tests``) and
    get_risk's guarding-tests path use - ``changed_lines`` -> ``tests_covering``
    / ``detect_missing_tests`` - so the answer is coverage-grounded. The CLI's
    filename-pattern guess is deliberately omitted: an agent cannot tell a guess
    from real coverage, and ``no_coverage_data`` already reports those files
    honestly as "unknown, run the suite". Degrades to a ``status`` string rather
    than raising, so it never fails the surrounding score.
    """
    from repowise.core.analysis.missing_test_signal import detect_missing_tests
    from repowise.core.persistence.crud import tests_covering
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None:
        return _empty_impacted(
            "no_index", "No index; run `repowise init` to enable impacted tests."
        )
    if changed_error is not None:
        return _empty_impacted(*changed_error)

    try:
        async with get_session(session_factory) as session:
            repo_id = (await _get_repo(session)).id
            report = await detect_missing_tests(session, repo_id, changed)
            if report.map_empty:
                return _empty_impacted(
                    "no_map",
                    "No per-test coverage map ingested; run the full suite. Build the map "
                    "with `coverage run --contexts=test` then `repowise coverage add`.",
                )
            all_ids: set[str] = set()
            for source_file, lines in changed.items():
                for row in await tests_covering(session, repo_id, source_file, lines=lines):
                    all_ids.add(row["test_id"])
    except LookupError:
        return _empty_impacted("no_index", "No indexed repository; run `repowise init`.")

    tests = sorted(all_ids)
    total = len(tests)
    return {
        "status": "map_present",
        "map_present": True,
        "tests": tests[:_IMPACTED_TESTS_LIMIT],
        "total": total,
        "truncated": total > _IMPACTED_TESTS_LIMIT,
        "missing_tests": _serialize_missing(report),
        "summary": (
            f"{total} test(s) cover the changed lines"
            + (f"; showing first {_IMPACTED_TESTS_LIMIT}" if total > _IMPACTED_TESTS_LIMIT else "")
            + "."
        ),
    }
