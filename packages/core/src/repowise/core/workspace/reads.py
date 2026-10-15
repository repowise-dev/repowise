"""Workspace read views over the stored cross-repo artifacts.

Pure functions over the parsed ``contracts.json``, ``cross_repo_edges.json``,
``system_graph.json``, ``breaking_changes.json`` and ``conformance.json``
payloads. They hold every filter, rollup and projection the workspace API
serves, so any server answering those routes calls these rather than keeping
its own copy. Each returns the wire shape as plain dicts.

A link's ``contract_id`` names its provider's contract and
``consumer_contract_id`` its consumer's when that differs, so the consumer side
of a link is always read as ``consumer_contract_id or contract_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from repowise.core.workspace.cross_repo import MAX_EDGES, MAX_EDGES_PER_REPO_PAIR

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "breaking_changes_view",
    "co_change_structure",
    "conformance_for_repo",
    "conformance_view",
    "contract_detail",
    "contract_row",
    "link_row",
    "list_co_changes",
    "list_contracts",
    "unmatched_reason",
]


def contract_row(c: dict) -> dict[str, Any]:
    """One raw ``contracts.json`` row in its wire shape.

    ``schema`` is dropped on purpose: it is the one field a list cannot afford,
    and :func:`contract_detail` carries it separately.
    """
    return {
        "contract_id": c.get("contract_id", ""),
        "contract_type": c.get("contract_type", ""),
        "role": c.get("role", ""),
        "repo": c.get("repo", ""),
        "file_path": c.get("file_path", ""),
        "symbol_name": c.get("symbol_name", ""),
        "confidence": c.get("confidence", 0.0),
        "service": c.get("service"),
        "line": c.get("line"),
        "symbol_id": c.get("symbol_id"),
        "meta": c.get("meta") or {},
    }


def link_row(lk: dict) -> dict[str, Any]:
    """One raw ``contract_links`` row in its wire shape."""
    return {
        "contract_id": lk.get("contract_id", ""),
        "contract_type": lk.get("contract_type", ""),
        "match_type": lk.get("match_type", "exact"),
        "confidence": lk.get("confidence", 0.0),
        "provider_repo": lk.get("provider_repo", ""),
        "provider_file": lk.get("provider_file", ""),
        "provider_symbol": lk.get("provider_symbol", ""),
        "consumer_repo": lk.get("consumer_repo", ""),
        "consumer_file": lk.get("consumer_file", ""),
        "consumer_symbol": lk.get("consumer_symbol", ""),
        "provider_service": lk.get("provider_service"),
        "consumer_service": lk.get("consumer_service"),
        "provider_symbol_id": lk.get("provider_symbol_id"),
        "consumer_symbol_id": lk.get("consumer_symbol_id"),
        "consumer_contract_id": lk.get("consumer_contract_id"),
    }


def _consumer_id(lk: dict) -> Any:
    return lk.get("consumer_contract_id") or lk.get("contract_id")


def _contract_haystack(c: dict) -> str:
    return " ".join(
        str(c.get(k) or "") for k in ("contract_id", "file_path", "symbol_name", "repo", "service")
    ).lower()


def _link_haystack(lk: dict) -> str:
    return " ".join(
        str(lk.get(k) or "")
        for k in (
            "contract_id",
            "consumer_contract_id",
            "provider_repo",
            "provider_file",
            "provider_symbol",
            "consumer_repo",
            "consumer_file",
            "consumer_symbol",
        )
    ).lower()


def _matches_terms(haystack: str, terms: list[str]) -> bool:
    return all(t in haystack for t in terms)


def list_contracts(
    contracts: Sequence[dict],
    links: Sequence[dict],
    *,
    contract_type: str | None = None,
    repo: str | None = None,
    role: str | None = None,
    q: str | None = None,
    linked: bool | None = None,
    include_links: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """The contracts list: filtered, counted by type, one page of contracts.

    ``q`` is case-insensitive and every whitespace-separated term must match.
    ``linked`` keeps only contracts on a matched link (true) or on none (false).
    ``include_links=False`` omits the link rows but still counts them. Only
    contracts are paged; links are typically small enough to send whole.
    """
    all_links = links
    contracts = list(contracts)
    links = list(links)

    if contract_type:
        contracts = [c for c in contracts if c.get("contract_type") == contract_type]
        links = [lk for lk in links if lk.get("contract_type") == contract_type]
    if repo:
        contracts = [c for c in contracts if c.get("repo") == repo]
        links = [
            lk for lk in links if lk.get("provider_repo") == repo or lk.get("consumer_repo") == repo
        ]
    if role:
        contracts = [c for c in contracts if c.get("role") == role]
    if linked is not None:
        # Keyed on the side each contract plays, from the unfiltered links, so a
        # type or repo filter above cannot turn a linked contract into an unused one.
        on_link = set()
        for lk in all_links:
            on_link.add(
                ("provider", lk.get("provider_repo"), lk.get("provider_file"), lk.get("contract_id"))
            )
            on_link.add(
                ("consumer", lk.get("consumer_repo"), lk.get("consumer_file"), _consumer_id(lk))
            )
        contracts = [
            c
            for c in contracts
            if (
                (c.get("role"), c.get("repo"), c.get("file_path"), c.get("contract_id"))
                in on_link
            )
            == linked
        ]
    terms = (q or "").lower().split()
    if terms:
        contracts = [c for c in contracts if _matches_terms(_contract_haystack(c), terms)]
        links = [lk for lk in links if _matches_terms(_link_haystack(lk), terms)]

    by_type: dict[str, int] = {}
    for c in contracts:
        ct = c.get("contract_type", "unknown")
        by_type[ct] = by_type.get(ct, 0) + 1

    return {
        "contracts": [contract_row(c) for c in contracts[offset : offset + limit]],
        "links": [link_row(lk) for lk in links] if include_links else [],
        "total_contracts": len(contracts),
        "total_links": len(links),
        "by_type": by_type,
    }


def unmatched_reason(
    diagnostics: dict | None, repo: str, file_path: str, contract_id: str
) -> str | None:
    """Why one consumer matched nothing, from the system graph's diagnostics.

    The reasons live in ``system_graph.json``, not ``contracts.json``, keyed by
    the same ``(repo, file_path, contract_id)`` triple. None when no graph is
    built or the consumer did match.
    """
    for u in (diagnostics or {}).get("unmatched_consumers", []):
        if (
            u.get("repo") == repo
            and u.get("file_path") == file_path
            and u.get("contract_id") == contract_id
        ):
            return u.get("reason")
    return None


def contract_detail(
    contracts: Iterable[dict],
    links: Iterable[dict],
    diagnostics: dict | None,
    *,
    repo: str,
    file_path: str,
    contract_id: str,
) -> dict[str, Any] | None:
    """One contract with its schema, its links and its unmatched reason, or None.

    The triple is the shareable identity, not a primary key: one file may call
    the same endpoint from two lines, and the first row wins. The links are
    filtered by the same triple, so they are identical whichever row wins.
    """
    match = next(
        (
            c
            for c in contracts
            if c.get("repo") == repo
            and c.get("file_path") == file_path
            and c.get("contract_id") == contract_id
        ),
        None,
    )
    if match is None:
        return None

    role = match.get("role", "")
    if role == "consumer":
        own = [
            lk
            for lk in links
            if _consumer_id(lk) == contract_id
            and lk.get("consumer_repo") == repo
            and lk.get("consumer_file") == file_path
        ]
    else:
        own = [
            lk
            for lk in links
            if lk.get("contract_id") == contract_id
            and lk.get("provider_repo") == repo
            and lk.get("provider_file") == file_path
        ]

    reason = None
    if role == "consumer" and not own:
        reason = unmatched_reason(diagnostics, repo, file_path, contract_id)

    return {
        "contract": contract_row(match),
        "contract_schema": match.get("schema"),
        "links": [link_row(lk) for lk in own],
        "unmatched_reason": reason,
    }


def list_co_changes(
    co_changes: Sequence[dict],
    total_mined: int | None = None,
    *,
    repo: str | None = None,
    min_strength: float = 0.0,
    limit: int = 50,
) -> dict[str, Any]:
    """Co-changing file pairs, filtered, strongest first, with the miner's caps.

    *total_mined* is how many pairs cleared the miner's thresholds before its
    caps trimmed the stored list; ``truncated_by`` names the cap that did.
    """
    pairs = list(co_changes)
    if total_mined is None:
        total_mined = len(pairs)
    # Judged before any query filter. The miner walks pairs strongest first, so
    # a full global budget means the workspace-wide cap stopped it (the
    # per-pair cap may also have applied).
    if total_mined <= len(pairs):
        truncated_by = None
    elif len(pairs) >= MAX_EDGES:
        truncated_by = "total"
    else:
        truncated_by = "per_repo_pair"

    if repo:
        pairs = [cc for cc in pairs if cc.get("source_repo") == repo or cc.get("target_repo") == repo]
    if min_strength > 0:
        pairs = [cc for cc in pairs if cc.get("strength", 0) >= min_strength]
    pairs.sort(key=lambda cc: -cc.get("strength", 0))

    return {
        "co_changes": [
            {
                "source_repo": cc.get("source_repo", ""),
                "source_file": cc.get("source_file", ""),
                "target_repo": cc.get("target_repo", ""),
                "target_file": cc.get("target_file", ""),
                "strength": cc.get("strength", 0.0),
                "frequency": cc.get("frequency", 0),
                "last_date": cc.get("last_date", ""),
                # Bounded sample, absent on pre-#483 overlays. Passed through
                # as stored: the miner already capped and deduped it.
                "evidence": cc.get("evidence"),
            }
            for cc in pairs[:limit]
        ],
        "total": len(pairs),
        "total_mined": total_mined,
        # The constants name the rules the miner applied; the overlay does not
        # store them.
        "per_repo_pair_cap": MAX_EDGES_PER_REPO_PAIR,
        "total_cap": MAX_EDGES,
        "truncated_by": truncated_by,
    }


def co_change_structure(
    links: Sequence[dict],
    *,
    source_repo: str,
    source_file: str,
    target_repo: str,
    target_file: str,
) -> dict[str, Any]:
    """Declared structure behind one co-changing pair.

    The contract links between the two files, and a count by type of the links
    between their repositories through any files.
    """

    def touching(repo: str, path: str) -> list[dict]:
        # Provider ends first, then consumer ends, each in artifact order.
        return [
            *(lk for lk in links if lk.get("provider_repo") == repo and lk.get("provider_file") == path),
            *(lk for lk in links if lk.get("consumer_repo") == repo and lk.get("consumer_file") == path),
        ]

    src_links = touching(source_repo, source_file)
    pair = {(source_repo, source_file), (target_repo, target_file)}
    pair_links = [
        lk
        for lk in src_links
        if {
            (lk.get("provider_repo"), lk.get("provider_file")),
            (lk.get("consumer_repo"), lk.get("consumer_file")),
        }
        == pair
    ]

    repos = {source_repo, target_repo}
    by_type: dict[str, int] = {}
    for lk in links:
        if {lk.get("provider_repo"), lk.get("consumer_repo")} == repos:
            ct = lk.get("contract_type", "unknown")
            by_type[ct] = by_type.get(ct, 0) + 1

    return {
        "pair_links": [link_row(lk) for lk in pair_links],
        "repo_links_total": sum(by_type.values()),
        "repo_links_by_type": by_type,
        "source_file_links": len(src_links),
        "target_file_links": len(touching(target_repo, target_file)),
    }


def breaking_changes_view(
    report: dict, *, repo: str | None = None, severity: str | None = None
) -> dict[str, Any]:
    """The breaking-change report, narrowed to a provider repo and/or severity.

    Rollups are recomputed when a filter narrowed the set, so the answer stays
    self-consistent; otherwise the stored report passes through unchanged.
    """
    if not (repo or severity):
        return dict(report)

    changes = list(report.get("changes", []))
    if repo:
        changes = [c for c in changes if c.get("provider_repo") == repo]
    if severity:
        changes = [c for c in changes if c.get("severity") == severity]
    consumers = [ic for c in changes for ic in c.get("impacted_consumers", [])]
    return {
        "version": report.get("version", 1),
        "generated_at": report.get("generated_at") or None,
        "changes": changes,
        "total": len(changes),
        "breaking_count": sum(1 for c in changes if c.get("severity") == "breaking"),
        "warning_count": sum(1 for c in changes if c.get("severity") == "warning"),
        "impacted_repos": sorted({ic.get("repo", "") for ic in consumers}),
        "impacted_services": sorted({ic.get("node_id", "") for ic in consumers}),
        "total_impacted_consumers": len(consumers),
    }


def _node_repo(node_id: str) -> str:
    """Repo alias for a system-graph node id (``repo`` or ``repo::service``)."""
    return node_id.split("::", 1)[0]


def conformance_for_repo(report: dict | None, repo: str) -> dict[str, list]:
    """Violations and cycles that involve *repo*.

    A violation involves the repo when either endpoint lives in it; a cycle
    when any participating service does.
    """
    if not report:
        return {"violations": [], "cycles": []}
    violations = [
        v
        for v in report.get("violations", [])
        if _node_repo(v.get("source", "")) == repo or _node_repo(v.get("target", "")) == repo
    ]
    cycles = [
        c
        for c in report.get("cycles", [])
        if any(_node_repo(n) == repo for n in c.get("nodes", []))
    ]
    return {"violations": violations, "cycles": cycles}


def conformance_view(report: dict, *, repo: str | None = None) -> dict[str, Any]:
    """The conformance report, narrowed to findings that involve *repo*."""
    # A report written before cycle totals were recorded has no total_cycles;
    # the listed count beats a default of zero beside a non-empty list.
    total_cycles = report.get("total_cycles", len(report.get("cycles", [])))
    if not repo:
        return {**report, "total_cycles": total_cycles}

    scoped = conformance_for_repo(report, repo)
    violations = scoped["violations"]
    cycles = scoped["cycles"]
    return {
        "version": report.get("version", 1),
        "generated_at": report.get("generated_at") or None,
        "rules_evaluated": report.get("rules_evaluated", 0),
        "violations": violations,
        "cycles": cycles,
        "violation_count": len(violations),
        "cycle_count": len(cycles),
        # The unscoped total: how many cycles the workspace has does not change
        # because the view was narrowed.
        "total_cycles": total_cycles,
        "violating_repos": sorted(
            {_node_repo(v.get("source", "")) for v in violations}
            | {_node_repo(v.get("target", "")) for v in violations}
        ),
    }
