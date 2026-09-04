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
    from collections.abc import Mapping, Sequence

    from repowise.core.workspace.test_impact import WorkspaceTestImpactResult
    from repowise.server.mcp_server._budget import OmissionCollector

_log = logging.getLogger("repowise.mcp.test_impact")

#: Tests named inline per consumer row. The rows are numerous and the tail is
#: recoverable through the omission store, so the inline list stays short.
_TESTS_PER_CONSUMER_LIMIT = 5

_BASIS_ORDER = {"measured": 0, "inferred": 1}

__all__ = [
    "close_test_impact_indexes",
    "cross_repo_tests",
    "cross_repo_tests_for",
    "tests_block_for",
]


def _norm(path: str) -> str:
    """The same one comparable spelling ``core.workspace.test_impact`` uses."""
    cleaned = (path or "").replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _lock() -> asyncio.Lock:
    """The one lock guarding the process-held indexes.

    Each cached index holds a single database session, and a session is not
    concurrent-safe, so two overlapping tool calls must not share one. The lock
    is stored with the loop it was made on, because a lock bound to a loop that
    has gone away can never be acquired again.
    """
    loop = asyncio.get_running_loop()
    held = _state._test_impact_lock
    if held is None or held[0] is not loop:
        held = (loop, asyncio.Lock())
        _state._test_impact_lock = held
    return held[1]


def _empty_result(reason: str, detail: str | None = None) -> WorkspaceTestImpactResult:
    """An answer that says why it is empty, so silence is never unexplained."""
    from repowise.core.workspace.test_impact import WorkspaceTestImpactResult

    summary: dict[str, Any] = {"reason": reason}
    if detail is not None:
        summary["detail"] = detail
    return WorkspaceTestImpactResult(summary=summary)


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
    # Only a real index is cached. Re-checking a repo without one costs a
    # single stat, and it lets a repo indexed later start answering without a
    # restart of this process.
    if index is not None:
        cache[alias] = index
    return index


async def cross_repo_tests_for(
    changed_by_repo: Mapping[str, Sequence[str]],
) -> WorkspaceTestImpactResult | None:
    """One join over every provider repo and file a tool call touches.

    ``None`` only outside workspace mode or without contract data, so a caller
    can omit its block entirely. Every other empty answer names its reason in
    ``summary``: ``no_matching_links`` when the contract map knows no consumer
    of those files, ``lookup_failed`` when the join itself broke. Never raises.
    """
    if _state._registry is None:
        return None
    enricher = _state._cross_repo_enricher
    if enricher is None or not getattr(enricher, "has_contract_data", False):
        return None
    try:
        from repowise.core.workspace.contracts import ContractLink
        from repowise.core.workspace.repo_index import WorkspaceIndex
        from repowise.core.workspace.test_impact import analyze_workspace_test_impact

        # Straight off the loaded artifact; contracts.json is never re-read.
        seen: set[int] = set()
        links = []
        changed: list[dict[str, str]] = []
        for alias, paths in sorted(changed_by_repo.items()):
            for path in sorted(set(paths)):
                changed.append({"repo": alias, "path": path})
                for raw in enricher.get_contract_links_as_provider(alias, path):
                    if id(raw) in seen:
                        continue
                    seen.add(id(raw))
                    links.append(ContractLink.from_dict(raw))

        consumers = sorted(
            {lk.consumer_repo for lk in links if lk.consumer_repo != lk.provider_repo}
        )
        if not changed or not consumers:
            return _empty_result("no_matching_links")

        async with _lock():
            opened = {}
            for consumer in consumers:
                index = await _consumer_index(consumer)
                if index is not None:
                    opened[consumer] = index
            # A copy, so WorkspaceIndex.close() can never empty the cache. The
            # per-pair cap is off because this caller caps its own inline list
            # and banks the tail, so it needs the true total.
            return await analyze_workspace_test_impact(
                WorkspaceIndex(dict(opened)),
                links,
                changed,
                target_repos=set(consumers),
                max_tests_per_pair=None,
            )
    except Exception as exc:
        _log.debug("Cross-repo test impact unavailable", exc_info=True)
        return _empty_result("lookup_failed", type(exc).__name__)


async def cross_repo_tests(
    alias: str, changed_files: list[str]
) -> WorkspaceTestImpactResult | None:
    """Tests in other repos that guard *changed_files* in repo *alias*."""
    if not changed_files:
        return None
    return await cross_repo_tests_for({alias: changed_files})


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
    # A join that broke says so on every row it would have answered, so no row
    # reads as "nothing guards this".
    if result.summary.get("reason") == "lookup_failed":
        block["state"] = "unresolved"
        block["unresolved_reason"] = "lookup_failed"
        block["unresolved_detail"] = result.summary.get("detail")
        return block
    path = _norm(consumer_file)
    rows = [
        rec
        for rec in result.recommendations
        if rec.consumer_repo == consumer_repo
        and path in rec.consumer_files
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
        # The reason belongs to the unresolved state alone: a row that did find
        # tests through some other link is answered, not undetermined.
        block["state"] = "unresolved"
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
    """Release every consumer index this process opened. Never raises.

    Closing runs under the lock so no join is mid-query on a session being torn
    down, and the lock is dropped with the indexes it guarded so the next call
    binds a fresh one to whatever loop is running then.
    """
    async with _lock():
        cache = _state._test_impact_indexes
        for alias, index in list(cache.items()):
            if index is None:
                continue
            try:
                await index.close()
            except Exception:
                _log.debug("Error closing the index for '%s'", alias, exc_info=True)
        cache.clear()
    _state._test_impact_lock = None
