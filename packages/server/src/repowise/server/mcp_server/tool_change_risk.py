"""MCP tool for live commit and range change-risk scoring."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from functools import partial
from typing import Any

import pathspec
import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from repowise.core.analysis.change_health.service import (
    ChangeHealthDeltaService,
    DeltaRequest,
)
from repowise.core.analysis.change_risk import (
    change_risk_payload,
    normalize_extensions,
    score_live_change,
)
from repowise.core.analysis.pr_blast import rank_tests_by_reach
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._budget.contracts import response_budget_shed_order
from repowise.server.mcp_server._change_health import (
    directive as _directive,
)
from repowise.server.mcp_server._change_health import (
    finding_detail as _finding_detail,
)
from repowise.server.mcp_server._change_health import (
    health_delta_block as _health_delta_block,
)
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _is_workspace_mode,
    _resolve_repo_context,
    _unsupported_repo_all,
    attach_ignored_arguments,
    resolve_enum_argument,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta

log = structlog.get_logger(__name__)

#: Cap on the line-precise impacted-test list, matching the get_risk directive's
#: ``tests_to_run`` cap so both surfaces stay glanceable. The tail goes to the
#: omission store, so ``truncated: true`` is recoverable rather than a dead end.
_IMPACTED_TESTS_LIMIT = 10

#: Cap on the per-file prior-fix list, matching ``_IMPACTED_TESTS_LIMIT`` so both
#: per-file blocks in this response stay the same size.
_PRIOR_FIXES_LIMIT = 10

#: Caps on the cross-repo block. It answers "does this commit cross a repo
#: boundary", not "list every consumer" — get_blast_radius is the tool for the
#: full traversal, so both lists stay short and report their own overflow.
_CROSS_REPO_BREAKING_LIMIT = 5
_CROSS_REPO_CONSUMER_LIMIT = 10

# Compatibility projection for direct callers and older tests. The shared
# response contract remains the single source of truth for this order.
_SHED_ORDER = response_budget_shed_order("get_change_risk")

#: Per-field units and calibration. Identical on every call, so it is opt-in.
#: ``diagnostics`` carries the raw model mechanics; ``findings`` lifts the
#: top-findings cap. Both are projections, recoverable by an exact call.
_INCLUDE_BLOCKS = frozenset({"scales", "diagnostics", "findings"})

#: Score mechanics that are identical or near-identical on every call. Moved
#: behind ``include=["diagnostics"]`` so the action-first blocks lead.
_DIAGNOSTIC_FIELDS = (
    "risk_authority",
    "score_measures",
    "score_unit",
    "baseline_sample_size",
    "features",
    "drivers",
)

#: Compact supporting context: the ranked reading, not the model's workings.
_CHANGE_SHAPE_FIELDS = (
    "score",
    "risk_percentile",
    "review_priority",
    "classification",
    "fallback_band",
    "is_fix",
)

@mcp.tool(
    surface_order=60,
    artifact_type="change_risk",
    presentation="change_risk",
    evidence_basis="measured",
)
async def get_change_risk(
    revspec: str | None = None,
    repo: str | None = None,
    extensions: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    baseline: int = 200,
    include: list[str] | None = None,
    finding_id: str | None = None,
) -> dict:
    """Review a commit, ``base..head`` range, or uncommitted work.

    Leads with ``directive`` (what to do) and ``health_delta`` (what this
    change newly made worse across defect, maintainability, and performance).
    Both sides are analysed from their own content, so a finding present at
    head is only reported when the diff explains it; every finding names its
    ``attribution`` basis and confidence.

    Trust ``health_delta.status``: ``partial`` means files were skipped and the
    change is not cleared. ``scope`` counts what was actually compared.

    ``impacted_tests`` keeps measured coverage and inferred candidates distinct.
    ``prior_fixes`` counts past fixes overlapping this diff. ``change_shape``
    ranks the diff's size and spread against recent commits.

    Args:
        revspec: Commit or ``base..head`` range. Omit to review uncommitted
            work, or ``HEAD`` when the tree is clean.
        repo: Repository alias in workspace mode; omit for the default.
        extensions: File suffixes to count, e.g. ``[".py", ".ts"]``.
        exclude_patterns: Gitignore-style paths to omit, e.g. ``["tests/"]``.
        baseline: Recent commits sampled for percentile ranking; 0 disables it.
        include: ``"findings"`` for every change finding, ``"diagnostics"`` for
            raw score mechanics, ``"scales"`` for units. All identical on
            repeat, so ask once.
        finding_id: Expand one ``health_delta`` finding by its id.
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
        return {"error": f"Could not read change {revspec or 'HEAD'!r}: {detail}"}
    except subprocess.TimeoutExpired:
        return {"error": f"git timed out reading change {revspec or 'HEAD'!r}."}
    ignored: list[dict] = []
    include_set = {
        block
        for block in (include or [])
        if resolve_enum_argument(block, _INCLUDE_BLOCKS, argument="include", ignored=ignored)
    }
    payload = change_risk_payload(result, scales="scales" in include_set)
    if "diagnostics" not in include_set:
        diagnostics = {f: payload.pop(f) for f in _DIAGNOSTIC_FIELDS if f in payload}
    else:
        diagnostics = {}
    if result.features.nf == 0:
        payload["warning"] = (
            f"No counted file changes in {payload['ref']!r} "
            "(check the revspec, extensions, or exclusion filters)."
        )
    # Changed lines over the SAME file universe the score counted (its
    # extensions + riskignore + request excludes), so nothing downstream
    # disagrees with the score about which files the change touches. Read once
    # and shared: both blocks below need it and git is the expensive part.
    # The test and fix blocks need the index; the cross-repo block needs only
    # workspace contracts, so an unindexed member still gets one rather than
    # going silently blind. Nothing else pays the git call.
    changed: dict[str, set[int]] = {}
    changed_error: tuple[str, str] | None = None
    if getattr(ctx, "session_factory", None) is not None or _has_contract_data():
        changed, changed_error = await _changed_in_scope(
            str(ctx.path),
            revspec,
            normalize_extensions(tuple(extensions or ())),
            result.riskignore_excludes + result.request_excludes,
            working_tree=result.working_tree,
        )
    collector = OmissionCollector("get_change_risk", repo_root=ctx.path)
    # The delta is the expensive half and needs nothing the enrichments need,
    # so it runs alongside them rather than after.
    delta_task = asyncio.create_task(
        asyncio.to_thread(
            _compare_health,
            str(ctx.path),
            revspec,
            tuple(extensions or ()),
            tuple(result.riskignore_excludes + result.request_excludes),
        )
    )
    try:
        payload["impacted_tests"] = await _impacted_tests_block(
            ctx, changed, changed_error, collector
        )
        prior_fixes = await _prior_fixes_block(ctx, changed)
        if prior_fixes is not None:
            payload["prior_fixes"] = prior_fixes
        cross_repo = _cross_repo_block(getattr(ctx, "alias", ""), sorted(changed))
        if cross_repo is not None:
            payload["cross_repo"] = cross_repo
    except BaseException:
        # Never leave the comparison running for a request that is already over.
        delta_task.cancel()
        raise
    delta = await delta_task
    await _attach_health_references(ctx, delta)
    if finding_id is not None:
        return _drill_down(payload, delta, finding_id, revspec)
    _attach_health(payload, delta, revspec, expand="findings" in include_set)
    payload["change_shape"] = _change_shape(payload, diagnostics)
    # source: live_git marks that the *score* is computed from the working
    # checkout's git. The two blocks above are index-backed, so the freshness
    # fields do apply to them, scoped to the change's files. None (not []) when
    # there are none, so a degraded read keeps the repo-level staleness warning
    # instead of reading as "nothing served, nothing to warn about".
    payload["_meta"] = _build_meta(
        timing_ms=(time.perf_counter() - started) * 1000,
        repository=await _repository(ctx),
        targets=sorted(changed) or None,
        extra={"source": "live_git"},
    )
    attach_ignored_arguments(payload, ignored)
    collector.attach(payload)
    return payload


#: One service per repository, so its comparison cache and stampede guard
#: outlive a single call. LRU-bounded: a long-lived server can see many
#: worktrees and workspace members over its lifetime.
_DELTA_SERVICES: OrderedDict[str, ChangeHealthDeltaService] = OrderedDict()
_DELTA_SERVICE_CAPACITY = 8
_DELTA_SERVICES_LOCK = threading.Lock()


def _delta_service(repo_path: str) -> ChangeHealthDeltaService:
    with _DELTA_SERVICES_LOCK:
        service = _DELTA_SERVICES.get(repo_path)
        if service is not None:
            _DELTA_SERVICES.move_to_end(repo_path)
            return service
    # Built outside the lock: the fingerprint reads config off disk.
    service = ChangeHealthDeltaService(
        repo_path=repo_path, rules_fingerprint=_rules_fingerprint(repo_path)
    )
    with _DELTA_SERVICES_LOCK:
        existing = _DELTA_SERVICES.get(repo_path)
        if existing is not None:
            _DELTA_SERVICES.move_to_end(repo_path)
            return existing
        _DELTA_SERVICES[repo_path] = service
        while len(_DELTA_SERVICES) > _DELTA_SERVICE_CAPACITY:
            _DELTA_SERVICES.popitem(last=False)
    return service


def _rules_fingerprint(repo_path: str) -> str:
    """Identity of the effective health rules; empty when they cannot be read."""
    try:
        from repowise.core.repo_config import config_fingerprint

        return config_fingerprint(repo_path)
    except Exception:
        return ""


def _compare_health(
    repo_path: str,
    revspec: str | None,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
) -> Any:
    """Run the comparison, degrading to an explicit unavailable state."""
    from repowise.core.analysis.change_health.models import ChangeHealthDelta

    try:
        return _delta_service(repo_path).compare(
            DeltaRequest(repo_path, revspec, extensions, exclude_patterns)
        )
    except Exception as exc:
        log.warning("change_health_comparison_failed", revspec=revspec, error=str(exc))
        return ChangeHealthDelta(
            status="unavailable",
            explanation=f"Health comparison failed: {exc}",
            base=None,
            head=None,
            comparison_basis="not_compared",
            fingerprint=None,
        )


async def _attach_health_references(ctx: Any, delta: Any) -> None:
    """Point a change finding at its stored twin, when it has one exactly.

    Only an exact match earns the canonical reference: same file, marker,
    symbol, and span. Anything looser would hand an agent a pointer to a
    different finding, and most change findings have no stored twin at all
    because uncommitted and historical work is never persisted.
    """
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import HealthFinding

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None or not delta.findings:
        return
    paths = sorted({f.path for f in delta.findings})
    try:
        async with get_session(session_factory) as session:
            repository = await _get_repo(session)
            rows = (
                await session.execute(
                    select(HealthFinding).where(
                        HealthFinding.repository_id == repository.id,
                        HealthFinding.file_path.in_(paths),
                    )
                )
            ).scalars().all()
    except SQLAlchemyError:
        return
    if not rows:
        return
    from repowise.server.mcp_server.tool_health import _health_finding_id

    alias = getattr(ctx, "alias", None) or repository.name
    stored = {
        (r.file_path, r.biomarker_type, r.function_name or "", r.line_start, r.line_end): r
        for r in rows
    }
    for finding in delta.findings:
        row = stored.get(
            (
                finding.path,
                finding.biomarker_type,
                finding.symbol or "",
                finding.line_start,
                finding.line_end,
            )
        )
        if row is None:
            continue
        finding.health_reference = {
            "tool": "get_health",
            "arguments": {"finding_id": _health_finding_id(row, alias)},
        }


def _attach_health(payload: dict, delta: Any, revspec: str | None, *, expand: bool) -> None:
    """Put the directive first and the compact delta second."""
    block = _health_delta_block(delta, revspec=revspec)
    if expand:
        from repowise.server.mcp_server._change_health import finding_row

        block["top_findings"] = [finding_row(f, revspec) for f in delta.findings]
        block["findings_emitted"] = len(delta.findings)
        block.pop("findings_reduced_reason", None)
        block.pop("all_findings_via", None)
    ordered = {
        "directive": _directive(delta, payload.get("impacted_tests")),
        "health_delta": block,
    }
    for key, value in payload.items():
        ordered[key] = value
    payload.clear()
    payload.update(ordered)


def _change_shape(payload: dict, diagnostics: dict) -> dict[str, Any]:
    """The ranked diff-shape reading, kept compact and clearly supporting."""
    shape = {f: payload[f] for f in _CHANGE_SHAPE_FIELDS if f in payload}
    shape["measures"] = "diff size and spread, not danger"
    if diagnostics:
        shape["diagnostics_via"] = "get_change_risk(include=['diagnostics'])"
    return shape


def _drill_down(payload: dict, delta: Any, finding_id: str, revspec: str | None) -> dict:
    """Expand one ephemeral change finding, or say why it is not there."""
    match = next(
        (f for f in delta.findings if f.change_finding_id == finding_id), None
    )
    if match is None:
        return {
            "error": f"No change finding {finding_id!r} in {payload.get('ref', 'this change')}.",
            "hint": "Ids are scoped to one comparison; re-run without finding_id to list them.",
            "available": [f.change_finding_id for f in delta.findings[:10]],
        }
    return {
        "finding": _finding_detail(match, revspec),
        "ref": payload.get("ref"),
        "health_delta_status": delta.status,
    }


async def _repository(ctx: Any) -> Any | None:
    """The indexed repository row, or ``None`` without an index.

    Only ``_meta`` needs it: the per-file blocks below resolve their own repo id
    inside the session they query in.
    """
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None:
        return None
    try:
        async with get_session(session_factory) as session:
            return await _get_repo(session)
    except (LookupError, SQLAlchemyError):
        return None


def _normalize_revspec(revspec: str | None) -> str:
    """Mirror ``score_live_change``'s three-dot handling for ``changed_lines``.

    ``changed_lines`` verifies each side of a ``base..head`` range as a ref, so a
    three-dot ``base...head`` (whose head parses as ``.head``) would fail its
    ref check. Strip the extra dot to the two-dot form the scorer already uses.
    """
    if revspec is None:
        return "HEAD"
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


def _has_contract_data() -> bool:
    """Whether workspace contracts are loaded, so the cross-repo block can speak."""
    from repowise.server.mcp_server import _state

    if not _is_workspace_mode():
        return False
    enricher = _state._cross_repo_enricher
    return bool(enricher is not None and getattr(enricher, "has_contract_data", False))


def _cross_repo_block(alias: str, changed_files: list[str]) -> dict[str, Any] | None:
    """What this commit does to consumers in other repos, or ``None``.

    A commit that changes a published signature is the same class of fact as
    its fix history, so it belongs beside it. Two sources, both from the last
    ``repowise update --workspace``: the contract links whose provider file this
    commit touched (who calls it), and the breaking-change report entries
    attributed to those same files (what broke). ``None`` outside workspace
    mode, without artifacts, or when the commit touches no published file, so
    the block never appears just to say nothing. Never raises.
    """
    try:
        from repowise.server.mcp_server import _state

        if not changed_files or not _is_workspace_mode():
            return None
        enricher = _state._cross_repo_enricher
        if enricher is None or not getattr(enricher, "has_contract_data", False):
            return None

        touched = set(changed_files)
        consumers: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for path in changed_files:
            for link in enricher.get_contract_links_as_provider(alias, path):
                if link.get("consumer_repo") == alias:
                    continue
                # Keyed by contract too: collapsing loses a contract_id.
                key = (
                    link.get("consumer_repo") or "",
                    link.get("consumer_file") or "",
                    link.get("contract_id") or "",
                    path,
                )
                if key in seen:
                    continue
                seen.add(key)
                consumers.append(
                    {
                        "provider_file": path,
                        "repo": link.get("consumer_repo"),
                        "file": link.get("consumer_file"),
                        "contract_id": link.get("contract_id"),
                        "contract_type": link.get("contract_type"),
                        "match_type": link.get("match_type"),
                        **(
                            {"provider_symbol_id": psid}
                            if (psid := link.get("provider_symbol_id"))
                            else {}
                        ),
                        **({"symbol_id": sid} if (sid := link.get("consumer_symbol_id")) else {}),
                    }
                )

        breaking: list[dict[str, Any]] = []
        breaking_total = 0
        has_breaking_report = bool(getattr(enricher, "has_breaking_changes", False))
        if has_breaking_report:
            for change in enricher.get_breaking_changes_for_repo(alias):
                if change.get("provider_file") not in touched:
                    continue
                cross = [c for c in change.get("impacted_consumers", []) if c.get("repo") != alias]
                if not cross:
                    continue
                breaking_total += 1
                if len(breaking) >= _CROSS_REPO_BREAKING_LIMIT:
                    continue
                breaking.append(
                    {
                        "contract_id": change.get("contract_id"),
                        "type": change.get("contract_type"),
                        "kind": change.get("kind"),
                        "severity": change.get("severity"),
                        "detail": change.get("detail"),
                        "provider_file": change.get("provider_file"),
                        "impacted_repos": sorted({c.get("repo") or "" for c in cross}),
                        **(
                            {"provider_symbol_id": psid}
                            if (psid := change.get("provider_symbol_id"))
                            else {}
                        ),
                    }
                )

        if not consumers and not breaking:
            return None
        report = enricher.get_breaking_changes() or {}
        # A removed endpoint has no current link, so its repos survive only on
        # the breaking side.
        repos = sorted(
            {c["repo"] for c in consumers if c.get("repo")}
            | {r for b in breaking for r in b["impacted_repos"] if r}
        )
        summary = (
            f"{len(consumers)} consumer link(s) in {len(repos)} other repo(s) touch the "
            f"files this change edits"
        )
        if breaking_total:
            summary += f"; {breaking_total} of the changed contracts broke them."
        elif has_breaking_report:
            summary += "; the last workspace update found no break in them."
        else:
            summary += "; no breaking-change report has been built for them."
        return {
            "consumers": consumers[:_CROSS_REPO_CONSUMER_LIMIT],
            "consumers_truncated": max(0, len(consumers) - _CROSS_REPO_CONSUMER_LIMIT),
            "consumer_repos": repos,
            "breaking_changes": breaking,
            "breaking_changes_truncated": breaking_total - len(breaking),
            # False = no detection pass ran, so the empty list is silence.
            "breaking_changes_available": has_breaking_report,
            # Stamps the breaking half only; the consumer list comes from the
            # contract artifact, which carries no exposed stamp.
            "breaking_changes_as_of": report.get("generated_at") or None,
            "summary": summary,
        }
    except Exception:
        return None


def _empty_impacted(status: str, summary: str) -> dict[str, Any]:
    """Uniform impacted-tests block for the degraded (no tests to name) paths."""
    return {
        "status": status,
        "map_present": False,
        "tests_to_run": [],
        "total": 0,
        "truncated": False,
        "line_coverage": {
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
    revspec: str | None,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    *,
    working_tree: bool = False,
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
            partial(
                changed_lines,
                repo_path,
                _normalize_revspec(revspec),
                working_tree=working_tree,
            )
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

    # Share of the change's own churn, so the fix counts below say where in this
    # change the risk sits rather than only that some touched file has a past.
    total_changed = sum(len(lines) for lines in changed.values())
    per_file: dict[str, dict[str, Any]] = {}
    for event in events:
        entry = per_file.setdefault(
            event.file_path,
            {
                "file_path": event.file_path,
                "fix_count": 0,
                "overlapping_lines": 0,
                "changed_lines": len(changed[event.file_path]),
                "share_of_change": round(len(changed[event.file_path]) / total_changed, 3)
                if total_changed
                else 0.0,
            },
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
    block = {
        "files": files[:_PRIOR_FIXES_LIMIT],
        "truncated": len(files) > _PRIOR_FIXES_LIMIT,
        "total_fixes": total,
        "files_with_fixes": len(files),
        "changed_lines_in_fixed_files": sum(f["changed_lines"] for f in per_file.values()),
        "line_overlap": "approximate",
        "summary": (
            f"{total} past bug-fix commit(s) touched {len(files)} of the changed file(s). "
            "Line overlap is approximate (past ranges are numbered on their own "
            "parent commit); the per-file counts are not."
        ),
    }
    # Only over the files actually returned, so the sentence never names a path
    # the reader cannot find anywhere else in the block.
    concentration = _concentration(files[:_PRIOR_FIXES_LIMIT])
    if concentration is not None:
        block["concentration"] = concentration
    return block


#: A file has to carry this much of the change's lines before the response will
#: say the risk sits there. Below it the change is spread out and naming one
#: file would be a stronger claim than the numbers support.
_CONCENTRATION_SHARE = 0.5


def _concentration(files: list[dict[str, Any]]) -> str | None:
    """Name the fix-carrying file that holds most of this change, if one does.

    The score itself is whole-change, so this is the only place the response
    says *where* the risk sits: the file with both the past and the churn.
    """
    if not files:
        return None
    # Negated path so ties break toward the first file the sorted list shows,
    # matching the ascending file_path tiebreak the block is sorted by.
    top = min(files, key=lambda f: (-f["share_of_change"], -f["fix_count"], f["file_path"]))
    if top["share_of_change"] < _CONCENTRATION_SHARE:
        return None
    return (
        f"{top['file_path']} carries {top['share_of_change']:.0%} of the changed lines "
        f"and {top['fix_count']} past bug fix(es)."
    )


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


def _cap_tests(tests: list[str], collector: OmissionCollector, label: str) -> list[str]:
    """First _IMPACTED_TESTS_LIMIT ids; the tail goes to the omission store."""
    if len(tests) > _IMPACTED_TESTS_LIMIT:
        collector.add(
            f"impacted_tests.tests_to_run ({label}) beyond cap={_IMPACTED_TESTS_LIMIT} "
            f"({len(tests) - _IMPACTED_TESTS_LIMIT} dropped)",
            tests[_IMPACTED_TESTS_LIMIT:],
        )
    return tests[:_IMPACTED_TESTS_LIMIT]


async def _inferred_impacted(
    session: Any, repo_id: str, changed_files: list[str], collector: OmissionCollector
) -> dict[str, Any]:
    """Graph-inferred candidates for a repo with no coverage map.

    "Run the full suite" is correct but useless on the repositories that have
    no coverage report, which is most of them. The dependency graph can narrow
    it: a test file that reaches a changed file is worth running first. That is
    a candidate list and is labelled one - ``basis`` is ``"inferred"`` and
    ``map_present`` stays False, so nothing here can be read as the line-precise
    measured answer.

    Deliberately file-level and line-blind. Reaching carries no line
    attribution, so ``line_coverage`` stays empty rather than being filled from
    a signal that cannot speak to lines - the distinction this whole block
    exists to keep.
    """
    from repowise.core.analysis.test_reachability import tests_reaching

    hint = (
        "Inferred from the dependency graph, not measured. For the line-precise "
        "answer build the map with `coverage run --contexts=test` then "
        "`repowise coverage add`."
    )
    try:
        reaching = await tests_reaching(session, repo_id, changed_files)
    except Exception:
        reaching = {}
    tests = rank_tests_by_reach(reaching)
    if not tests:
        return _empty_impacted(
            "no_map",
            "No per-test coverage map ingested and no test reaches the changed files "
            "in the graph; run the full suite. " + hint,
        )
    total = len(tests)
    block = _empty_impacted("inferred", "")
    block.update(
        {
            "basis": "inferred",
            "tests_to_run": _cap_tests(tests, collector, "inferred"),
            "total": total,
            "truncated": total > _IMPACTED_TESTS_LIMIT,
            "summary": (
                f"{total} test file(s) reach the changed files in the graph"
                + (
                    f"; showing first {_IMPACTED_TESTS_LIMIT}"
                    if total > _IMPACTED_TESTS_LIMIT
                    else ""
                )
                + ". "
                + hint
            ),
        }
    )
    return block


async def _impacted_tests_block(
    ctx: Any,
    changed: dict[str, set[int]],
    changed_error: tuple[str, str] | None,
    collector: OmissionCollector,
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
                return await _inferred_impacted(session, repo_id, sorted(changed), collector)
            by_file: dict[str, list[str]] = {}
            for source_file, lines in changed.items():
                rows = await tests_covering(session, repo_id, source_file, lines=lines)
                ids = sorted({row["test_id"] for row in rows})
                if ids:
                    by_file[source_file] = ids
    except LookupError:
        return _empty_impacted("no_index", "No indexed repository; run `repowise init`.")
    except SQLAlchemyError:
        return _empty_impacted("unknown", "Could not read the coverage map.")

    tests = rank_tests_by_reach(by_file)
    total = len(tests)
    return {
        "status": "map_present",
        "basis": "measured",
        "map_present": True,
        "tests_to_run": _cap_tests(tests, collector, "measured"),
        "total": total,
        "truncated": total > _IMPACTED_TESTS_LIMIT,
        "line_coverage": _serialize_missing(report),
        "summary": (
            f"{total} test(s) cover the changed lines"
            + (f"; showing first {_IMPACTED_TESTS_LIMIT}" if total > _IMPACTED_TESTS_LIMIT else "")
            + "."
        ),
    }
