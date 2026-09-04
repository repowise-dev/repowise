"""Cross-repo test impact for the MCP tools that already name consumers.

``get_change_risk`` and ``get_blast_radius`` both hand an agent a list of
consumer call sites in other repos. The question that follows is always "so
what do I run?", and the workspace already holds the answer: the contract links
say which consumer symbol sits on the far side, and each consumer repo's index
says which of its tests reach that symbol. This module joins the two and shapes
one small block per consumer row.

Both tools stay usable outside workspace mode: every entry point returns
``None`` or an empty block instead of raising, so a caller omits the block
instead of reporting a failure.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from repowise.server.mcp_server import _state

if TYPE_CHECKING:
    from repowise.core.workspace.test_impact import WorkspaceTestImpactResult
    from repowise.server.mcp_server._budget import OmissionCollector

_log = logging.getLogger("repowise.mcp.test_impact")

#: Tests named inline per consumer row. The rows are numerous and the tail is
#: recoverable through the omission store, so the inline list stays short.
_TESTS_PER_CONSUMER_LIMIT = 5

_BASIS_ORDER = {"measured": 0, "inferred": 1}

__all__ = ["close_test_impact_indexes", "cross_repo_tests", "tests_block_for"]


def _norm(path: str) -> str:
    """The same one comparable spelling ``core.workspace.test_impact`` uses."""
    cleaned = (path or "").replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _lock() -> asyncio.Lock:
    """The one lock guarding the process-held indexes.

    Each cached index holds a single database session, and a session is not
    concurrent-safe, so two overlapping tool calls must not share one.
    """
    lock = _state._test_impact_lock
    if lock is None:
        lock = _state._test_impact_lock = asyncio.Lock()
    return lock


async def _consumer_index(alias: str) -> Any:
    """The open index for consumer *alias*, opening it on first use.

    The primary repo is never a consumer of itself here and holds the largest
    symbol table in the workspace, so only the consumer repos the links name
    are ever opened.
    """
    cache = _state._test_impact_indexes
    if alias in cache:
        return cache[alias]
    registry = _state._registry
    ws_config = getattr(registry, "ws_config", None)
    root = getattr(registry, "workspace_root", None)
    if ws_config is None or root is None:
        return None
    entry = next((e for e in ws_config.repos if e.alias == alias), None)
    if entry is None:
        return None

    from repowise.core.workspace.repo_index import open_repo_index

    try:
        index = await open_repo_index(alias, (root / entry.path).resolve())
    except Exception:
        _log.debug("Could not open the index for '%s'", alias, exc_info=True)
        index = None
    # None is cached too: a repo without an index stays without one for the
    # life of the process, and retrying it on every call costs a stat per call.
    cache[alias] = index
    return index


async def cross_repo_tests(
    alias: str, changed_files: list[str]
) -> WorkspaceTestImpactResult | None:
    """Tests in other repos that guard *changed_files* in repo *alias*.

    ``None`` outside workspace mode, without contract data, or when nothing
    could be joined, so a caller omits the block entirely. Never raises.
    """
    try:
        if not changed_files or _state._registry is None:
            return None
        enricher = _state._cross_repo_enricher
        if enricher is None or not getattr(enricher, "has_contract_data", False):
            return None

        from repowise.core.workspace.contracts import ContractLink
        from repowise.core.workspace.repo_index import WorkspaceIndex
        from repowise.core.workspace.test_impact import analyze_workspace_test_impact

        # Straight off the loaded artifact; contracts.json is never re-read.
        seen: set[int] = set()
        links = []
        for path in changed_files:
            for raw in enricher.get_contract_links_as_provider(alias, path):
                if id(raw) in seen:
                    continue
                seen.add(id(raw))
                links.append(ContractLink.from_dict(raw))
        if not links:
            return None

        consumers = sorted({lk.consumer_repo for lk in links if lk.consumer_repo != alias})
        if not consumers:
            return None

        changed = [{"repo": alias, "path": path} for path in changed_files]
        async with _lock():
            opened = {}
            for consumer in consumers:
                index = await _consumer_index(consumer)
                if index is not None:
                    opened[consumer] = index
            # A copy, so WorkspaceIndex.close() can never empty the cache.
            return await analyze_workspace_test_impact(
                WorkspaceIndex(dict(opened)), links, changed, target_repos=set(consumers)
            )
    except Exception:
        _log.debug("Cross-repo test impact unavailable", exc_info=True)
        return None


def tests_block_for(
    result: WorkspaceTestImpactResult | None,
    consumer_repo: str,
    consumer_file: str,
    contract_id: str,
    collector: OmissionCollector | None,
    label: str,
) -> dict[str, Any]:
    """The ``tests`` block for one consumer row of a tool payload.

    The rows are read as the join produced them: the state is whichever signal
    the join actually reached, and a link it could not follow keeps its reason
    instead of being reported as "no tests".
    """
    block: dict[str, Any] = {
        "state": "none",
        "tests_to_run": [],
        "total": 0,
        "truncated": False,
        "unresolved_reason": None,
        "unresolved_detail": None,
    }
    if result is None:
        return block
    path = _norm(consumer_file)
    rows = [
        rec
        for rec in result.recommendations
        if rec.consumer_repo == consumer_repo
        and rec.consumer_file == path
        and contract_id in rec.contract_ids
    ]
    unresolved = next(
        (
            u
            for u in result.unresolved
            if u.consumer_repo == consumer_repo
            and u.consumer_file == path
            and u.contract_id == contract_id
        ),
        None,
    )
    if rows:
        rows.sort(key=lambda r: _BASIS_ORDER.get(r.basis, 99))
        block["state"] = rows[0].basis
    elif unresolved is not None:
        block["state"] = "unresolved"
    if unresolved is not None:
        block["unresolved_reason"] = unresolved.reason
        block["unresolved_detail"] = unresolved.detail

    tests = [
        {
            "test_file": rec.test_file,
            "test_id": rec.test_id,
            "basis": rec.basis,
            "via": rec.via,
            "confidence": rec.confidence,
        }
        for rec in rows
    ]
    block["total"] = len(tests)
    block["truncated"] = len(tests) > _TESTS_PER_CONSUMER_LIMIT
    if block["truncated"] and collector is not None:
        dropped = len(tests) - _TESTS_PER_CONSUMER_LIMIT
        collector.add(
            f"{label} beyond cap={_TESTS_PER_CONSUMER_LIMIT} ({dropped} dropped)",
            tests[_TESTS_PER_CONSUMER_LIMIT:],
        )
    block["tests_to_run"] = tests[:_TESTS_PER_CONSUMER_LIMIT]
    return block


async def close_test_impact_indexes() -> None:
    """Release every consumer index this process opened. Never raises."""
    cache = _state._test_impact_indexes
    for alias, index in list(cache.items()):
        if index is None:
            continue
        try:
            await index.close()
        except Exception:
            _log.debug("Error closing the index for '%s'", alias, exc_info=True)
    cache.clear()
