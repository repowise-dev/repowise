"""Contracts across the estate, and the calls that reached no provider in it."""

from __future__ import annotations

from collections import Counter
from typing import Any

from repowise.core.report.model import (
    Section,
    WorkspaceReportContext,
    capped,
    check,
    report_sections,
)
from repowise.core.report.sections._estate import missing_graph

_METHOD = (
    "the contract store and extraction diagnostics 'repowise update --workspace' stored: "
    "contracts read per repo, the links matched between repos, and the consumers left unmatched"
)
_REASONS = {
    "no_provider": "no provider in the workspace",
    "unlinked": "a provider exists, no link formed",
}


def dangling_calls(graph: Any, store: Any) -> list[dict[str, Any]]:
    """Consumers (calls, subscriptions, table reads) that matched no provider in
    another service, one row per repo, file and contract id.

    The one place the diagnostics' unmatched consumers are read, so a change to
    their shape is one edit here. A consumer served inside its own service is
    not dangling, whichever list the diagnostics keep it in. The target comes
    from the consumer's stored contract: the workspace repo its host resolved
    to, else the host or base it names; a call to a public host outside the
    workspace is marked ``external``.
    """
    contracts: dict[tuple[str, str, str], Any] = {}
    for c in store.contracts if store is not None else ():
        if c.role == "consumer":  # the first one read gives the row its line
            contracts.setdefault((c.repo, c.file_path, c.contract_id), c)
    rows = []
    seen: set[tuple[str, str, str]] = set()
    for u in graph.diagnostics.unmatched_consumers:
        key = (u.repo, u.file_path, u.contract_id)
        if u.reason == "internal_only" or key in seen:
            continue
        seen.add(key)
        contract = contracts.get(key)
        meta = contract.meta if contract is not None else {}
        rows.append(
            {
                "consumer": u.repo,
                "file": u.file_path,
                "line": contract.line if contract is not None else None,
                "contract_id": u.contract_id,
                "type": u.contract_type,
                "reason": u.reason,
                "target": meta.get("target_repo") or meta.get("host") or meta.get("base_token"),
                "external": bool(meta.get("external")) or u.reason == "external_host",
            }
        )
    return sorted(
        rows,
        key=lambda r: (r["consumer"], r["file"], r["line"] is None, r["line"] or 0, r["contract_id"]),
    )


def _contracts(graph: Any, store: Any) -> dict[str, Any]:
    diag = graph.diagnostics
    providers: Counter[str] = Counter()
    consumers: Counter[str] = Counter()
    for r in diag.repo_breakdown:
        providers.update(r.providers_by_type)
        consumers.update(r.consumers_by_type)
    links = store.contract_links if store is not None else []
    service_links = store.service_links if store is not None else []
    match_types = Counter(link.match_type for link in links)
    linked: Counter[str] = Counter(link.contract_type for link in links)
    pairs = Counter((link.consumer_repo, link.provider_repo, link.contract_type) for link in links)
    details = [
        f"{t}: {providers[t]} provider(s), {consumers[t]} consumer(s), {linked[t]} link(s)"
        for t in sorted(set(providers) | set(consumers))
    ]
    details += capped(
        [f"{c} -> {p}: {n} {t} link(s)" for (c, p, t), n in sorted(pairs.items())]
    )
    if service_links:
        details.append(
            f"{len(service_links)} service-level link(s): the host names a workspace repo "
            "but no route there serves the path"
        )
    details.append(
        f"{len(diag.orphan_providers)} provider(s) no consumer in the workspace reaches "
        "(may be called from outside it)"
    )
    kinds = ", ".join(f"{k} {n}" for k, n in sorted(match_types.items()))
    return check(
        "contracts",
        reason=(
            f"{diag.total_providers} provider(s) and {diag.total_consumers} consumer(s) read; "
            f"{len(links)} cross-repo link(s)" + (f" ({kinds})" if kinds else "")
            + f", {diag.weak_link_count} weak"
        ),
        details=details,
        method=_METHOD,
        basis="measured",
        data={
            "providers_by_type": dict(sorted(providers.items())),
            "consumers_by_type": dict(sorted(consumers.items())),
            "links_by_type": dict(sorted(linked.items())),
            "links_by_match_type": dict(sorted(match_types.items())),
            "service_links": len(service_links),
            "orphan_providers": len(diag.orphan_providers),
        },
    )


def _http_coverage(graph: Any) -> dict[str, Any]:
    diag = graph.diagnostics
    read = sum(r.consumers_by_type.get("http", 0) for r in diag.repo_breakdown)
    unresolved = diag.http_consumers_unresolved
    missed = diag.unrecognised_call_file_count
    data = {"read": read, "unresolved": unresolved, "unrecognised_call_files": missed}
    method = (
        "HTTP calls read as contracts, over those plus calls located but not resolvable and "
        "files that call out through code no extractor reads (one per file)"
    )
    if missed is None:
        return check(
            "http-calls",
            reason=(
                f"{read} read, {unresolved} located but not resolvable; calls no extractor "
                "recognises were not counted, since this artifact predates that count"
            ),
            method=method,
            basis="unresolved",
            data=data,
        )
    seen = read + unresolved + missed
    by_repo = [
        f"{repo}: {n} file(s)"
        for repo, n in sorted((diag.unrecognised_call_files or {}).items())
        if n
    ]
    return check(
        "http-calls",
        reason=(
            f"{read} HTTP call(s) read as contracts, {unresolved} located but not resolvable, "
            f"{missed} file(s) calling out through code no extractor reads"
            if seen
            else "no HTTP call located, and no file makes a recognisable network call"
        ),
        details=capped(by_repo),
        method=method,
        basis="measured",
        data=data,
    )


def _dangling(graph: Any, store: Any) -> dict[str, Any]:
    rows = dangling_calls(graph, store)
    external = [r for r in rows if r["external"]]
    dangling = [r for r in rows if not r["external"]]
    by_reason = Counter(r["reason"] for r in dangling)
    by_type = Counter(r["type"] for r in dangling)
    lines = [
        f"{r['consumer']} {r['file']}" + (f":{r['line']}" if r["line"] else "")
        + f" -> {r['target'] or 'target not resolved'}: {r['contract_id']} "
        f"({_REASONS.get(r['reason'], r['reason'])})"
        for r in dangling
    ]
    details = capped(lines)
    if external:
        details.append(f"{len(external)} call(s) to public hosts outside the workspace not counted")
    types = ", ".join(f"{t} {n}" for t, n in sorted(by_type.items()))
    return check(
        "dangling-calls",
        reason=(
            f"{len(dangling)} consumer(s) matched no provider in another service"
            + (f" ({types})" if types else "")
            + (
                ": " + ", ".join(f"{_REASONS.get(k, k)} {n}" for k, n in sorted(by_reason.items()))
                if by_reason
                else ""
            )
        ),
        details=details,
        method=_METHOD + "; one row per consumer file and contract id",
        basis="measured",
        data={"dangling": dangling, "external": len(external)},
    )


async def collect(ctx: WorkspaceReportContext) -> list[dict[str, Any]]:
    graph = ctx.system_graph()
    if graph is None:
        return missing_graph("contracts", _METHOD)
    store = ctx.contract_store()
    return [_contracts(graph, store), _http_coverage(graph), _dangling(graph, store)]


report_sections.register(
    "estate-contracts",
    Section(
        title="Contracts and dangling calls",
        summary="Contracts read and linked across repos, and each call that reached no provider.",
        collect=collect,
        order=30,
        report="estate",
        scope="workspace",
    ),
)
