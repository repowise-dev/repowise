"""MCP Tool: get_context — relationships and triage signals for files / modules / symbols.

Workhorse for "what is this and what touches it" questions. Returns a triage
card by default (title, summary, signatures, hotspot bit, top callers, pointers
to risk / why / symbol). **NOT a source-body tool.** For source, ask for it in
one call — ``include=["skeleton"]`` returns the whole file body-elided and
line-verified — or just Read the file. Reaching for ``get_symbol`` once per
signature in the card is the expensive path and the card does not recommend it.
The split keeps the cached prompt prefix small on multi-turn agent sessions:
``get_context`` stays under ~2k tokens for common targets.

Optional ``include`` parameter widens the response:
  - include=["full_doc"]  -> full wiki markdown content, plus the page's agent
                            digest (questions, identifiers, git signals)
  - include=["callers"]   → who calls this symbol (symbol targets only)
  - include=["callees"]   → what this symbol calls (symbol targets only)
  - include=["references"]→ every live edit site of a symbol (definition,
                            imports, calls, other mentions) and whether
                            the list is complete (symbol targets only)
  - include=["ownership"] → primary owner, bus factor, contributor count; on a
                            component target, declared owners against recent
                            activity, with findings
  - include=["last_change"]→ last commit date, and its author when stored
  - include=["metrics"]   → PageRank, betweenness, percentile ranks
  - include=["community"] → community membership + neighbors
  - include=["decisions"] → decisions governing the target, in three labelled
                            lanes: ``decisions`` (accepted and binding),
                            ``candidates`` (proposed, nobody has agreed),
                            ``history`` (accepted then withdrawn). The last
                            two appear only when non-empty, and are capped.
                            A dismissed record is in none of them.
  - include=["skeleton"]  → body-elided file rendering (signatures + top-PageRank bodies)
  - include=["skeleton+"] → all non-function code kept, every function/method body elided
  - include=["health"]    → code-health scores and biomarkers for the target
  - include=["doc_drift"] → documents that name this file, and their drift
  - include=["symbols"]   → every symbol in a file card, not the ranked top 15
  - include=["changes"]   → recent Change Records touching a file target
  - include=["suspects"]  → changes merged in the last 14 days ranked against the
                            targets as a symptom, each with the signals that fired
  - include=["provenance"]→ a file's commits by source (agent, human-attested,
                            unknown) with coverage and the honest agent share
  - include=["files"]     → on a directory, every file under it (capped, with
                            ``complete`` and ``omitted``) and its tests

An unrecognised key is dropped and named in ``ignored_arguments`` rather than
silently ignored: an unknown key otherwise produces exactly the response the
caller would have got without it, so a typo reads as a real answer (#1496).

This module is the orchestrator; single-target resolution lives in
``targets`` and the budget cap in ``truncation``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import func, select

from repowise.core.distill.budget import estimate_tokens
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GitMetadata
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector, register_post_enforce
from repowise.server.mcp_server._changes import change_cards, is_change_target, suspects_block
from repowise.server.mcp_server._components import component_cards, is_component_target
from repowise.server.mcp_server._episodes import enrich_episode_counts as _enrich_episodes
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    attach_ignored_arguments,
    drop_echoed_target,
    resolve_enum_argument,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import completeness_line as _completeness_line
from repowise.server.mcp_server._meta import served_root
from repowise.server.mcp_server._packages import is_package_target, package_cards
from repowise.server.mcp_server._why_pointer import attach_why_pointer
from repowise.server.mcp_server._workspace_routing import (
    needs_routing,
    route_targets,
    run_grouped,
)
from repowise.server.mcp_server.tool_context.enrichment import attach_doc_references
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target

_log = logging.getLogger("repowise.mcp.context")

# Every value ``include_set`` is tested against downstream, in ``targets.py``.
# ``docs`` and ``freshness`` are always on but remain legal to pass explicitly.
# ``source`` is omitted: get_context serves triage cards and structural metadata,
# not raw source bodies (which are served via include=["skeleton"] or the Read tool).
_INCLUDE_BLOCKS = frozenset(
    {
        "docs",
        "freshness",
        "full_doc",
        "callers",
        "callees",
        "references",
        "ownership",
        "last_change",
        "metrics",
        "community",
        "decisions",
        "skeleton",
        "skeleton+",
        "health",
        "doc_drift",
        "symbols",
        "changes",
        "provenance",
        "suspects",
        "files",
    }
)


@mcp.tool(
    surface_order=20,
    artifact_type="context",
    presentation="context",
    recipes=(
        ToolRecipe(
            "read_file_shape",
            'get_context(targets=["path"], include=["skeleton"])',
            ("get_context",),
        ),
    ),
)
async def get_context(
    targets: list[str],
    include: list[str] | None = None,
    compact: bool = True,
    repo: str | None = None,
    symptom: str | None = None,
) -> dict:
    """Triage card for files / modules / symbols — relationships, not source bytes.

    Returns summary, signatures with lines, hotspot, fix_history and
    decision titles; either risk cue means get_risk. Batch targets in one
    call. Whole file, body-elided and line-verified: include=["skeleton"]
    in ONE call, or Read it; do not call get_symbol per signature. Rename:
    include=["references"] is the whole edit set when ``complete``. Which
    files in a directory lack tests: include=["files"] on it.

    Args:
        targets: paths, "path::Symbol", "component:<name>",
            "change:<id>" (pr-123 or a sha), "package:<name>".
        include: callers | callees | references | skeleton | skeleton+
            | files | symbols (all, not top 15) | decisions | full_doc
            | ownership | last_change | metrics | community | health
            | doc_drift | changes | provenance | suspects.
        compact: False adds structure, imports, docstrings.
        symptom: issue text or stack trace to rank ``suspects`` on.
        repo: usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_context")
    routes = await route_targets(targets) if repo is None else None
    if needs_routing(routes):
        return await run_grouped(
            routes,
            lambda alias, local: get_context(
                local, include=include, compact=compact, repo=alias, symptom=symptom
            ),
        )
    ctx = await _resolve_repo_context(repo)
    collector = OmissionCollector("get_context", repo_root=ctx.path)
    ignored: list[dict[str, Any]] = []
    include_set = _include_set(include, ignored)
    if symptom and "suspects" not in include_set:
        ignored.append(
            {"argument": "symptom", "values": [symptom[:80]], "valid": ['include=["suspects"]']}
        )
    exclude_spec = _get_exclude_spec(ctx.path)

    import time as _time

    _t0 = _time.perf_counter()
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        raw_results = await _resolve_cards(
            session, repository, targets, include_set, compact, ctx, exclude_spec, collector
        )
        # One batched read on the shared session, not one per target inside the
        # gather: savepoints opened concurrently on one session close each other.
        await attach_doc_references(
            session,
            repository,
            {r["target"]: r for r in raw_results if isinstance(r, dict)},
            exclude_spec=exclude_spec,
            collector=collector,
            repo_root=served_root(repository, ctx.path),
        )
        suspects = (
            await suspects_block(session, repository, targets, symptom)
            if "suspects" in include_set
            else None
        )

    results = _per_target_results(targets, raw_results)
    # One integer per target: how many dated episodes are bound here. Absent at zero.
    await asyncio.to_thread(_enrich_episodes, results, ctx.path)

    response: dict[str, Any] = {
        "targets": {r["target"]: r for r in results},
        "_meta": _build_meta(
            timing_ms=(_time.perf_counter() - _t0) * 1000,
            repository=repository,
            targets=targets,
        ),
    }
    if suspects is not None:
        response["suspects"] = suspects
    # include=["decisions"] lists the titles on each card already.
    if "decisions" not in include_set:
        await attach_why_pointer(response, ctx, targets)
    _attach_whole_files(response, results)
    _attach_cross_repo(response, ctx.alias)
    drop_echoed_target(response.get("targets"))
    attach_ignored_arguments(response, ignored)
    collector.attach(response)
    await asyncio.to_thread(_declare_replaced_reads, response, ctx.path)
    return response


def _declare_replaced_reads(response: dict[str, Any], repo_root: Any) -> None:
    """Credit each file card with what one Read of its file costs. Never raises."""
    from repowise.server.mcp_server._savings import declare_replaced
    from repowise.server.mcp_server._savings.counterfactual import context_replaced_tokens
    from repowise.server.mcp_server._savings.reads import file_read_tokens

    try:
        tokens = context_replaced_tokens(response, lambda p: file_read_tokens(repo_root, p))
    except Exception:  # savings accounting never breaks a tool call
        _log.debug("get_context savings declaration failed", exc_info=True)
        return
    declare_replaced(response, tokens)


def _include_set(include: list[str] | None, ignored: list[dict[str, Any]]) -> set[str]:
    """``docs`` and ``freshness`` plus every known ``include`` key.

    The two defaults are always returned (freshness is how an agent spots a
    stale index); every other block costs bytes on each cached turn, so
    ``include`` only adds. An unknown key is recorded in *ignored* so a typo
    is not read as an answer.
    """
    known = [
        k
        for k in (include or [])
        if resolve_enum_argument(k, _INCLUDE_BLOCKS, argument="include", ignored=ignored)
    ]
    return {"docs", "freshness"} | set(known)


async def _resolve_cards(
    session: Any,
    repository: Any,
    targets: list[str],
    include_set: set[str],
    compact: bool,
    ctx: Any,
    exclude_spec: Any,
    collector: OmissionCollector,
) -> list[Any]:
    """One card or exception per target, in target order.

    Component, change and package targets resolve first, in turn, so they never
    share the session with the gather. ``return_exceptions=True`` keeps one
    target's failure from sinking the batch.
    """
    as_of_ts = (
        await session.execute(
            select(func.max(GitMetadata.last_commit_at)).where(
                GitMetadata.repository_id == repository.id
            )
        )
    ).scalar()
    cards = await _component_cards(
        session,
        repository,
        [t for t in targets if is_component_target(t)],
        ctx,
        exclude_spec,
        ownership="ownership" in include_set,
    )
    cards |= await _change_cards(
        session, repository, [t for t in targets if is_change_target(t)], collector
    )
    cards |= await _package_cards(
        session, repository, [t for t in targets if is_package_target(t)], ctx, collector
    )
    gathered = await asyncio.gather(
        *[
            _resolve_one_target(
                session,
                repository,
                t,
                include_set,
                compact,
                exclude_spec=exclude_spec,
                repo_root=ctx.path,
                collector=collector,
                as_of_ts=as_of_ts,
            )
            for t in targets
            if t not in cards
        ],
        return_exceptions=True,
    )
    rest = iter(gathered)
    return [cards[t] if t in cards else next(rest) for t in targets]


def _per_target_results(targets: list[str], raw_results: list[Any]) -> list[dict[str, Any]]:
    """Cards with each failed target turned into an error entry carrying ``target``."""
    results: list[dict[str, Any]] = []
    for t, r in zip(targets, raw_results, strict=True):
        if isinstance(r, BaseException) and not isinstance(r, Exception):
            # CancelledError / KeyboardInterrupt / SystemExit must propagate, or a
            # cancelled request would run to completion.
            raise r
        if isinstance(r, BaseException):
            _log.exception("get_context: resolving target %r failed", t, exc_info=r)
            results.append(
                {
                    "target": t,
                    "error": f"Internal error resolving '{t}': {type(r).__name__}",
                }
            )
        else:
            results.append(r)
    return results


def _served_whole(card: Any) -> bool:
    """Whether *card* carries its file's whole source.

    A "raw" skeleton is the file's own source untouched; the other modes elide
    bodies. A raw text the budget later cut no longer measures its full size.
    """
    skeleton = card.get("skeleton") if isinstance(card, dict) else None
    if not isinstance(skeleton, dict) or skeleton.get("mode") != "raw":
        return False
    text = skeleton.get("text")
    return isinstance(text, str) and estimate_tokens(text) == skeleton.get("full_tokens")


def _attach_whole_files(response: dict[str, Any], results: list[dict[str, Any]]) -> None:
    """Mark the response complete for files served whole."""
    whole_files = sum(1 for r in results if _served_whole(r))
    if whole_files:
        response["_meta"]["complete"] = _completeness_line(files=whole_files)


def _settle_whole_files(result: dict[str, Any]) -> None:
    """Restate the whole-file claim on what the response budget left."""
    meta = result.get("_meta")
    if not isinstance(meta, dict) or "complete" not in meta:
        return
    targets = result.get("targets")
    cards = targets.values() if isinstance(targets, dict) else ()
    whole_files = sum(1 for c in cards if _served_whole(c))
    if whole_files:
        meta["complete"] = _completeness_line(files=whole_files)
    else:
        meta.pop("complete")


register_post_enforce("get_context", _settle_whole_files)


def _attach_cross_repo(response: dict[str, Any], alias: Any) -> None:
    """Workspace mode: co-change partners and contract links in other repos."""
    from repowise.server.mcp_server._helpers import _is_workspace_mode

    enricher = _state._cross_repo_enricher
    if not (enricher is not None and enricher.has_data and _is_workspace_mode()):
        return
    for target_key, target_data in response["targets"].items():
        cross_repo: dict[str, Any] = {}
        partners = enricher.get_cross_repo_partners(alias, target_key)
        if partners:
            cross_repo["co_changes_with"] = [
                {"repo": p["repo"], "file": p["file"], "strength": p["strength"]}
                for p in partners[:5]
            ]
        if enricher.has_contract_data:
            contracts = _contract_links(enricher, alias, target_key)
            if contracts:
                cross_repo["contracts"] = contracts
        if cross_repo:
            target_data["cross_repo"] = cross_repo


def _contract_links(enricher: Any, alias: Any, target_key: str) -> dict[str, Any]:
    """Consumers and providers of the contracts *target_key* takes part in."""
    provider_links = enricher.get_contract_links_as_provider(alias, target_key)
    consumer_links = enricher.get_contract_links_as_consumer(alias, target_key)
    contracts: dict[str, Any] = {}
    if provider_links:
        contracts["consumers"] = [
            {
                "consumer_repo": lk["consumer_repo"],
                "contract_id": lk["contract_id"],
                "type": lk["contract_type"],
            }
            for lk in provider_links[:5]
        ]
    if consumer_links:
        contracts["providers"] = [
            {
                "provider_repo": lk["provider_repo"],
                "contract_id": lk["contract_id"],
                "type": lk["contract_type"],
            }
            for lk in consumer_links[:5]
        ]
    return contracts


async def _component_cards(
    session: Any,
    repository: Any,
    targets: list[str],
    ctx: Any,
    exclude_spec: Any,
    *,
    ownership: bool = False,
) -> dict[str, Any]:
    """Cards for the batch's component targets; a failure becomes each one's error."""
    if not targets:
        return {}
    try:
        return await component_cards(
            session,
            repository,
            targets,
            repo_root=ctx.path,
            alias=ctx.alias,
            exclude_spec=exclude_spec,
            ownership=ownership,
        )
    except Exception as exc:
        return {t: exc for t in targets}


async def _package_cards(
    session: Any, repository: Any, targets: list[str], ctx: Any, collector: OmissionCollector
) -> dict[str, Any]:
    """Cards for the batch's ``package:`` targets; a failure becomes each one's error."""
    if not targets:
        return {}
    try:
        return await package_cards(
            session, repository, targets, repo_root=ctx.path, collector=collector
        )
    except Exception as exc:
        return {t: exc for t in targets}


async def _change_cards(
    session: Any, repository: Any, targets: list[str], collector: OmissionCollector
) -> dict[str, Any]:
    """Cards for the batch's ``change:`` targets; a failure becomes each one's error."""
    if not targets:
        return {}
    try:
        return await change_cards(session, repository, targets, collector)
    except Exception as exc:
        return {t: exc for t in targets}
