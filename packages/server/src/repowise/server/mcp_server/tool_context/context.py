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

This module is the orchestrator; single-target resolution lives in
``targets`` and the budget cap in ``truncation``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from repowise.core.persistence.database import get_session
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import context_hint as _context_hint
from repowise.server.mcp_server.tool_context.targets import _resolve_one_target
from repowise.server.mcp_server.tool_context.truncation import _truncate_to_budget


@mcp.tool()
async def get_context(
    targets: list[str],
    include: list[str] | None = None,
    compact: bool = True,
    repo: str | None = None,
) -> dict:
    """Triage card for files / modules / symbols — relationships, not source bytes.

    Returns a compact card the agent can use to decide its next move: title,
    summary, signatures, hotspot bit, top callers, and pointers (decision_record
    titles, symbol_ids) into the deeper tools. For the actual source body of a
    symbol, call ``get_symbol("path/to/file.py::Name")`` — it is cheaper than
    Read and returns bounded bytes with exact line numbers.

    Batch multiple targets in one call. In workspace mode responses are
    auto-enriched with cross-repo co-change partners and API contract links.

    Include options (everything outside defaults is opt-in):
      - "full_doc":   full wiki markdown content for the target
      - "ownership":  primary owner, bus factor, contributor count
      - "last_change":last commit date and author
      - "callers":    who calls this symbol (symbol targets only)
      - "callees":    what this symbol calls (symbol targets only)
      - "metrics":    PageRank, betweenness centrality, percentile ranks
      - "community":  architectural community membership + neighbors
      - "decisions":  full decision records (default returns titles only)

    Example: get_context(["src/auth/service.py", "src/auth/middleware.py"])
    Example: get_context(["src/auth/service.py::verify_token"], include=["callers"])

    Args:
        targets: file paths, module paths, or qualified symbol IDs.
        include: list of optional data blocks (defaults are always returned).
        compact: default True (signatures only). False adds structure+imports+docstrings.
        repo: usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("get_context")
    ctx = await _resolve_repo_context(repo)

    # Default to docs + freshness when include is omitted. Freshness is
    # critical for the agent to detect stale index data.  The other blocks
    # (ownership/last_change/decisions) are 200–500 bytes each and bloat
    # every subsequent agent turn via cache replay. Callers that want them
    # must pass include explicitly.
    include_set = set(include) if include else {"docs", "freshness"}

    import time as _time

    _t0 = _time.perf_counter()
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)

        results = await asyncio.gather(
            *[
                _resolve_one_target(
                    session,
                    repository,
                    t,
                    include_set,
                    compact,
                )
                for t in targets
            ]
        )

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

    # Enforce the global token cap. See ``_truncate_to_budget`` for strategy.
    return _truncate_to_budget(response)
