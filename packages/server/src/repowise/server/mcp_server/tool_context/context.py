"""MCP Tool: get_context — relationships and triage signals for files / modules / symbols.

Workhorse for "what is this and what touches it" questions. Returns a triage
card by default (title, summary, signatures, hotspot bit, top callers, pointers
to risk / why / symbol). NOT a source-body tool — for raw bytes call
``get_symbol("path::Name")`` instead. The split keeps the cached prompt prefix
small on multi-turn agent sessions: ``get_context`` stays under ~2k tokens for
common targets, while ``get_symbol`` returns bounded bytes for one symbol.

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

This module is the orchestrator; single-target resolution lives in
``targets`` and the budget cap in ``truncation``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from repowise.core.persistence.database import get_session
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._budget import OmissionCollector, truncate_to_budget
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import context_hint as _context_hint
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target

_log = logging.getLogger("repowise.mcp.context")


@mcp.tool()
async def get_context(
    targets: list[str],
    include: list[str] | None = None,
    compact: bool = True,
    repo: str | None = None,
) -> dict:
    """Triage card for files / modules / symbols — relationships, not source bytes.

    Returns title, summary, signatures, hotspot bit, decision_record titles,
    and symbol_ids to pipe into get_symbol (cheaper than Read for bodies).
    Batch targets in one call. File targets above ~80 lines default to a
    skeleton (every signature + top-PageRank bodies, with a verified flag —
    a fraction of Read cost); ``mostly_full`` marks files where a direct
    Read costs little more.

    Args:
        targets: file paths, module paths, or "path::Symbol" ids.
        include: opt-in blocks: full_doc | ownership | last_change | callers
            | callees | metrics | community | decisions | skeleton.
        compact: default True; False adds structure+imports+docstrings.
        repo: usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_context")
    ctx = await _resolve_repo_context(repo)

    # docs + freshness are ALWAYS returned (the tool contract says
    # "defaults are always returned"); ``include`` only adds blocks on top.
    # Freshness is critical for the agent to detect stale index data. The
    # other blocks (ownership/last_change/decisions) are 200-500 bytes each
    # and bloat every subsequent agent turn via cache replay. Callers that
    # want them must pass include explicitly. Building the set this way
    # also fixes include=["skeleton"] silently dropping the file summary
    # and freshness card that every other call shape carries.
    include_set = {"docs", "freshness"} | (set(include) if include else set())

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

    response: dict[str, Any] = {
        "targets": {r["target"]: r for r in results},
        "_meta": _build_meta(
            timing_ms=(_time.perf_counter() - _t0) * 1000,
            hint=_context_hint(targets, compact, include_set),
            repository=repository,
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

    # Enforce the global token cap. Anything dropped is persisted via the
    # collector so a truncated response always carries expandable
    # ``[repowise#<ref>]`` markers instead of silently losing content.
    collector = OmissionCollector("get_context", repo_root=ctx.path)
    return truncate_to_budget(response, collector=collector)
