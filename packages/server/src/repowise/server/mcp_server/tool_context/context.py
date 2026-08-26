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
  - include=["full_doc"]  → full wiki markdown content
  - include=["callers"]   → who calls this symbol (symbol targets only)
  - include=["callees"]   → what this symbol calls (symbol targets only)
  - include=["ownership"] → primary owner, bus factor, contributor count
  - include=["last_change"]→ last commit date and author
  - include=["metrics"]   → PageRank, betweenness, percentile ranks
  - include=["community"] → community membership + neighbors
  - include=["decisions"] → full decision records (default returns titles only)
  - include=["skeleton"]  → body-elided file rendering (signatures + top-PageRank bodies)
  - include=["health"]    → code-health scores and biomarkers for the target

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

from repowise.core.persistence.database import get_session
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._episodes import enrich_episode_counts as _enrich_episodes
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
    attach_ignored_arguments,
    resolve_enum_argument,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import context_hint as _context_hint
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target

_log = logging.getLogger("repowise.mcp.context")

# Every value ``include_set`` is tested against downstream, in ``targets.py``.
# ``docs`` and ``freshness`` are always on but remain legal to pass explicitly.
# ``source`` is tested in ``_meta.context_hint`` and left out deliberately: both
# branches there return None, so accepting it would promise a block that does
# nothing.
_INCLUDE_BLOCKS = frozenset(
    {
        "docs",
        "freshness",
        "full_doc",
        "callers",
        "callees",
        "ownership",
        "last_change",
        "metrics",
        "community",
        "decisions",
        "skeleton",
        "health",
    }
)


@mcp.tool(
    surface_order=20,
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
) -> dict:
    """Triage card for files / modules / symbols — relationships, not source bytes.

    Returns title, summary, signatures with line numbers, hotspot bit, and
    decision_record titles. fix_history appears only on files with counted bug
    fixes (count, age, bug_magnet); hotspot is churn. Either one is a cue to
    call get_risk. episodes counts the dated records bound to a target — what
    happened here and why — and appears only when there is at least one;
    get_why serves the bodies. A symbol target is counted as its file, and a
    module aggregates everything beneath it.
    Batch targets in one call. No source bytes by default: pass
    include=["skeleton"] for the whole file body-elided and line-verified in
    ONE call, or Read it. Do not call get_symbol per signature.

    Default responses fit 24,000 serialized chars; nonempty ``include`` uses
    32,000. Reductions carry counts and ``_meta.omitted`` recovery refs;
    ``_meta.recovery_unavailable`` names a storage failure.
    Include-gated blocks are projections, not omissions.

    Args:
        targets: file paths, module paths, or "path::Symbol" ids.
        include: opt-in blocks: full_doc | ownership | last_change | callers
            | callees | metrics | community | decisions | skeleton | health.
            An unrecognised key is named in ignored_arguments.
        compact: default True; False adds structure+imports+docstrings.
        repo: usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_context")
    ctx = await _resolve_repo_context(repo)
    collector = OmissionCollector("get_context", repo_root=ctx.path)

    # docs + freshness are ALWAYS returned (the tool contract says
    # "defaults are always returned"); ``include`` only adds blocks on top.
    # Freshness is critical for the agent to detect stale index data. The
    # other blocks (ownership/last_change/decisions) are 200-500 bytes each
    # and bloat every subsequent agent turn via cache replay. Callers that
    # want them must pass include explicitly. Building the set this way
    # also fixes include=["skeleton"] silently dropping the file summary
    # and freshness card that every other call shape carries.
    # An unknown key adds no block, so without this the response is identical
    # to one that never asked — and for callers/callees a genuine empty list is
    # a *present* key, which makes the typo the quieter of the two (#1496).
    ignored: list[dict[str, Any]] = []
    known = [
        k
        for k in (include or [])
        if resolve_enum_argument(k, _INCLUDE_BLOCKS, argument="include", ignored=ignored)
    ]
    include_set = {"docs", "freshness"} | set(known)

    exclude_spec = _get_exclude_spec(ctx.path)

    import time as _time

    _t0 = _time.perf_counter()
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)

        # return_exceptions=True isolates a single target's failure: one
        # target raising (e.g. a malformed lookup) must not sink the whole
        # batch. Any exception is converted below into a per-target error
        # entry that carries "target", so the comprehension keyed on target
        # below never KeyErrors.
        raw_results = await asyncio.gather(
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
                )
                for t in targets
            ],
            return_exceptions=True,
        )

    results: list[dict[str, Any]] = []
    for t, r in zip(targets, raw_results, strict=True):
        if isinstance(r, BaseException) and not isinstance(r, Exception):
            # CancelledError / KeyboardInterrupt / SystemExit must propagate:
            # converting them to per-target errors would break cooperative
            # cancellation (a cancelled request would run to completion).
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

    # One integer per target: how many dated episodes are bound here. A number
    # invites a follow-up get_why; a paragraph would spend the budget of every
    # caller that only wanted the triage card. Absent rather than zero.
    await asyncio.to_thread(_enrich_episodes, results, ctx.path)

    response: dict[str, Any] = {
        "targets": {r["target"]: r for r in results},
        "_meta": _build_meta(
            timing_ms=(_time.perf_counter() - _t0) * 1000,
            hint=_context_hint(targets, compact, include_set),
            repository=repository,
            targets=targets,
        ),
    }

    # Cross-repo enrichment (Phase 3 + 4)
    from repowise.server.mcp_server._helpers import _is_workspace_mode

    enricher = _state._cross_repo_enricher
    if enricher is not None and enricher.has_data and _is_workspace_mode():
        for target_key, target_data in response["targets"].items():
            cross_repo: dict[str, Any] = {}

            partners = enricher.get_cross_repo_partners(ctx.alias, target_key)
            if partners:
                cross_repo["co_changes_with"] = [
                    {"repo": p["repo"], "file": p["file"], "strength": p["strength"]}
                    for p in partners[:5]
                ]

            # Contract links (Phase 4)
            if enricher.has_contract_data:
                provider_links = enricher.get_contract_links_as_provider(ctx.alias, target_key)
                consumer_links = enricher.get_contract_links_as_consumer(ctx.alias, target_key)
                if provider_links or consumer_links:
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
                    cross_repo["contracts"] = contracts

            if cross_repo:
                target_data["cross_repo"] = cross_repo

    attach_ignored_arguments(response, ignored)
    collector.attach(response)
    return response
