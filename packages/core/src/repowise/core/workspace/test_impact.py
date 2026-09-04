"""Cross-repository test impact: which consumer tests guard a provider change.

Joins the workspace contract map (provider call site -> consumer call site)
with each consumer repo's test reachability, to answer "given a change in
provider repo X, which tests in consumer repos Y and Z should I run?".

The join enters the consumer's call graph at the contract's
``consumer_symbol_id`` rather than at the consumer file, so a test that reaches
an unrelated symbol in the same file is not recommended. A link that cannot be
followed is reported as an unresolved row with a reason, never dropped: an
empty answer always says which state produced it.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.analysis.test_reachability import (
    DEFAULT_CALL_DEPTH,
    DEFAULT_MAX_DEPTH,
    MAX_TESTS_PER_TARGET,
    tests_reaching_by_tier,
)
from repowise.core.persistence.crud.analysis.coverage_map import tests_covering_files
from repowise.core.workspace.config import WorkspaceConfig
from repowise.core.workspace.contracts import ContractLink, load_contract_store
from repowise.core.workspace.repo_index import WorkspaceIndex, open_workspace_index

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from repowise.core.workspace.repo_index import RepoIndex

_BASIS_ORDER = {"measured": 0, "inferred": 1}

_TIER_PRIORITY = {"call-graph": 0, "import-graph": 1}

__all__ = [
    "MAX_TESTS_PER_TARGET",
    "UnresolvedLink",
    "WorkspaceTestImpactResult",
    "WorkspaceTestRecommendation",
    "analyze_workspace_test_impact",
    "workspace_test_impact_from_root",
    "workspace_test_impact_to_dict",
]


@dataclass(frozen=True)
class WorkspaceTestRecommendation:
    """A test in a consumer repo that guards a changed provider file."""

    test_id: str
    test_file: str
    consumer_repo: str
    consumer_file: str
    consumer_symbol_id: str | None
    provider_repo: str
    provider_file: str
    contract_id: str
    contract_type: str
    basis: str  # "measured" | "inferred"
    via: str  # "coverage-map" | "call-graph" | "import-graph"
    confidence: float
    source_files: list[str]
    evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class UnresolvedLink:
    """A contract link the join could not follow, and why."""

    consumer_repo: str
    consumer_file: str
    consumer_symbol_id: str | None
    provider_repo: str
    provider_file: str
    contract_id: str
    contract_type: str
    reason: str  # "no_index" | "unbound" | "symbol_missing" | "lookup_failed"
    detail: str | None = None


@dataclass
class WorkspaceTestImpactResult:
    """Recommendations, the links that could not be followed, and the counts."""

    workspace: bool = True
    recommendations: list[WorkspaceTestRecommendation] = field(default_factory=list)
    recommendations_total: int = 0
    recommendations_emitted: int = 0
    recommendations_truncated: bool = False
    recommendations_omitted: int = 0
    recommendations_by_basis: dict[str, int] = field(
        default_factory=lambda: {"measured": 0, "inferred": 0}
    )
    recommendations_by_repo: dict[str, int] = field(default_factory=dict)
    recommendations_by_consumer_repo: dict[str, int] = field(default_factory=dict)
    unresolved: list[UnresolvedLink] = field(default_factory=list)
    files_analyzed: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _norm(path: str) -> str:
    """One comparable spelling of a repo-relative path."""
    cleaned = (path or "").replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _internal_call(link: ContractLink) -> bool:
    """Mirror of ``contracts._same_repo_same_service``, which takes Contracts.

    A provider and consumer in the same repo and the same service boundary are
    one program calling itself, not a cross-repo contract.
    """
    return link.provider_repo == link.consumer_repo and (
        link.provider_service == link.consumer_service
    )


def _recommendation_sort_key(rec: WorkspaceTestRecommendation) -> tuple:
    """More source files first, then measured over inferred, then repo and id."""
    return (
        -len(rec.source_files),
        _BASIS_ORDER.get(rec.basis, 99),
        _TIER_PRIORITY.get(rec.via, 99),
        rec.consumer_repo,
        rec.test_id,
    )


def _unresolved_for(link: ContractLink, reason: str, detail: str | None = None) -> UnresolvedLink:
    return UnresolvedLink(
        consumer_repo=link.consumer_repo,
        consumer_file=_norm(link.consumer_file),
        consumer_symbol_id=link.consumer_symbol_id,
        provider_repo=link.provider_repo,
        provider_file=_norm(link.provider_file),
        contract_id=link.contract_id,
        contract_type=link.contract_type,
        reason=reason,
        detail=detail,
    )


@dataclass
class _ConsumerOutcome:
    recommendations: list[WorkspaceTestRecommendation] = field(default_factory=list)
    unresolved: list[UnresolvedLink] = field(default_factory=list)
    files_analyzed: list[dict[str, Any]] = field(default_factory=list)


def _file_row(
    alias: str,
    consumer_file: str,
    links: Sequence[ContractLink],
    *,
    state: str,
    measured_count: int = 0,
    inferred_count: int = 0,
    inferred_total: int = 0,
    via: str | None = None,
) -> dict[str, Any]:
    return {
        "consumer_repo": alias,
        "consumer_file": consumer_file,
        "state": state,
        "measured_tests_count": measured_count,
        "inferred_tests_count": inferred_count,
        "inferred_tests_total": inferred_total,
        "via": via,
        "provider_repos": sorted({lk.provider_repo for lk in links}),
        "contract_ids": sorted({lk.contract_id for lk in links}),
        "consumer_symbol_ids": sorted(
            {lk.consumer_symbol_id for lk in links if lk.consumer_symbol_id}
        ),
    }


async def _analyze_consumer(
    alias: str,
    links: Sequence[ContractLink],
    repo_index: RepoIndex | None,
    *,
    call_depth: int,
    import_depth: int,
    include_measured: bool,
    include_inferred: bool,
) -> _ConsumerOutcome:
    """Resolve one consumer repo, sequentially on the one session it holds."""
    out = _ConsumerOutcome()
    by_file: dict[str, list[ContractLink]] = defaultdict(list)
    for link in links:
        by_file[_norm(link.consumer_file)].append(link)

    if repo_index is None:
        out.unresolved = [_unresolved_for(lk, "no_index") for lk in links]
        out.files_analyzed = [
            _file_row(alias, path, group, state="unresolved") for path, group in by_file.items()
        ]
        return out

    bound: dict[str, list[ContractLink]] = defaultdict(list)
    seeds: dict[str, set[str]] = defaultdict(set)
    for path, group in by_file.items():
        known = {sym.symbol_id for sym in repo_index.symbols_for_file(path)}
        for link in group:
            if link.consumer_symbol_id is None:
                out.unresolved.append(_unresolved_for(link, "unbound"))
            elif link.consumer_symbol_id not in known:
                # The index moved on since the link was built.
                out.unresolved.append(_unresolved_for(link, "symbol_missing"))
            else:
                bound[path].append(link)
                seeds[path].add(link.consumer_symbol_id)

    files = sorted(bound)
    measured: dict[str, list[dict[str, Any]]] = {}
    reached: dict[str, Any] = {}
    if files:
        session = repo_index.session
        try:
            if include_measured:
                rows = await tests_covering_files(session, repo_index.repo_id, set(files))
                for path, covering in rows.items():
                    measured[path] = [
                        {
                            "test_id": str(row["test_id"]),
                            "test_file": str(
                                row.get("test_file") or str(row["test_id"]).split("::", 1)[0]
                            ),
                            "source_format": row.get("source_format"),
                        }
                        for row in covering
                    ]
            if include_inferred:
                reached = await tests_reaching_by_tier(
                    session,
                    repo_index.repo_id,
                    files,
                    call_depth=call_depth,
                    import_depth=import_depth,
                    symbol_seeds={path: seeds[path] for path in files},
                )
        except Exception as exc:
            return _ConsumerOutcome(
                unresolved=[
                    _unresolved_for(lk, "lookup_failed", type(exc).__name__) for lk in links
                ],
                files_analyzed=[
                    _file_row(alias, path, group, state="unresolved")
                    for path, group in by_file.items()
                ],
            )

    for path, group in by_file.items():
        links_for_file = bound.get(path, [])
        measured_tests = measured.get(path, [])
        for entry in measured_tests:
            for link in links_for_file:
                out.recommendations.append(
                    WorkspaceTestRecommendation(
                        test_id=entry["test_id"],
                        test_file=entry["test_file"],
                        consumer_repo=alias,
                        consumer_file=path,
                        consumer_symbol_id=link.consumer_symbol_id,
                        provider_repo=link.provider_repo,
                        provider_file=_norm(link.provider_file),
                        contract_id=link.contract_id,
                        contract_type=link.contract_type,
                        basis="measured",
                        via="coverage-map",
                        confidence=link.confidence,
                        source_files=[_norm(link.provider_file)],
                        evidence=[
                            {
                                "basis": "measured",
                                "source_file": _norm(link.provider_file),
                                "via": "coverage-map",
                                "source_format": entry.get("source_format"),
                            }
                        ],
                    )
                )

        hit = reached.get(path)
        # The walk trims its own list per target; the join caps per consumer
        # and provider pair below and reports the cut, so start from all of them.
        reached_tests = list(hit.all_tests or hit.tests) if hit else []
        if reached_tests:
            for link in links_for_file:
                for test_id in reached_tests:
                    out.recommendations.append(
                        WorkspaceTestRecommendation(
                            test_id=test_id,
                            test_file=test_id.split("::", 1)[0],
                            consumer_repo=alias,
                            consumer_file=path,
                            consumer_symbol_id=link.consumer_symbol_id,
                            provider_repo=link.provider_repo,
                            provider_file=_norm(link.provider_file),
                            contract_id=link.contract_id,
                            contract_type=link.contract_type,
                            basis="inferred",
                            via=hit.via,
                            confidence=link.confidence,
                            source_files=[_norm(link.provider_file)],
                            evidence=[
                                {
                                    "basis": "inferred",
                                    "source_file": _norm(link.provider_file),
                                    "via": hit.via,
                                }
                            ],
                        )
                    )

        if measured_tests:
            state = "measured"
        elif reached_tests:
            state = "inferred"
        elif links_for_file:
            state = "none"
        else:
            state = "unresolved"
        out.files_analyzed.append(
            _file_row(
                alias,
                path,
                group,
                state=state,
                measured_count=len(measured_tests),
                inferred_count=len(reached_tests),
                inferred_total=hit.total if hit else 0,
                via=hit.via if hit else None,
            )
        )
    return out


async def analyze_workspace_test_impact(
    index: WorkspaceIndex,
    links: Sequence[ContractLink],
    changed_files: Sequence[Mapping[str, str]],
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
    include_measured: bool = True,
    include_inferred: bool = True,
    min_confidence: float = 0.0,
    target_repos: Collection[str] | None = None,
) -> WorkspaceTestImpactResult:
    """Tests in consumer repos that guard *changed_files* in provider repos.

    *index* and *links* are supplied by the caller, so a request handler that
    already holds both opens nothing here.
    """
    changed_by_provider: dict[str, set[str]] = defaultdict(set)
    for entry in changed_files:
        repo = entry.get("repo")
        path = entry.get("path")
        if repo and path:
            changed_by_provider[repo].add(_norm(path))

    summary: dict[str, Any] = {
        "changed_provider_files": {
            repo: sorted(paths) for repo, paths in sorted(changed_by_provider.items())
        },
        "changed_files_without_contracts": {},
        "consumer_repos_analyzed": [],
        "consumer_repos_without_index": [],
        "total_contract_links": len(links),
        "relevant_contract_links": 0,
        "states": {"measured": 0, "inferred": 0, "none": 0, "unresolved": 0},
    }
    if not changed_by_provider:
        summary["reason"] = "no_changed_files"
        return WorkspaceTestImpactResult(summary=summary)

    kept = [
        link
        for link in links
        if link.confidence >= min_confidence
        and (target_repos is None or link.consumer_repo in target_repos)
        and _norm(link.provider_file) in changed_by_provider.get(link.provider_repo, set())
        and not _internal_call(link)
    ]
    summary["relevant_contract_links"] = len(kept)

    named: dict[str, set[str]] = defaultdict(set)
    for link in kept:
        named[link.provider_repo].add(_norm(link.provider_file))
    summary["changed_files_without_contracts"] = {
        repo: sorted(paths - named.get(repo, set()))
        for repo, paths in sorted(changed_by_provider.items())
        if paths - named.get(repo, set())
    }

    if not kept:
        summary["reason"] = "no_matching_links"
        return WorkspaceTestImpactResult(summary=summary)

    by_consumer: dict[str, list[ContractLink]] = defaultdict(list)
    for link in kept:
        by_consumer[link.consumer_repo].append(link)

    aliases = sorted(by_consumer)
    # Consumers run in parallel because each holds its own session; everything
    # inside one consumer is sequential, since a session is not concurrent-safe.
    outcomes = await asyncio.gather(
        *(
            _analyze_consumer(
                alias,
                by_consumer[alias],
                index.get(alias),
                call_depth=call_depth,
                import_depth=import_depth,
                include_measured=include_measured,
                include_inferred=include_inferred,
            )
            for alias in aliases
        )
    )

    raw: list[WorkspaceTestRecommendation] = []
    unresolved: list[UnresolvedLink] = []
    files_analyzed: list[dict[str, Any]] = []
    for outcome in outcomes:
        raw.extend(outcome.recommendations)
        unresolved.extend(outcome.unresolved)
        files_analyzed.extend(outcome.files_analyzed)

    seen: dict[tuple, WorkspaceTestRecommendation] = {}
    for rec in raw:
        key = (rec.test_id, rec.consumer_repo, rec.provider_repo, rec.contract_id)
        existing = seen.get(key)
        if existing is not None and _BASIS_ORDER.get(rec.basis, 99) >= _BASIS_ORDER.get(
            existing.basis, 99
        ):
            existing.evidence.extend(rec.evidence)
        else:
            seen[key] = rec

    deduplicated = sorted(seen.values(), key=_recommendation_sort_key)

    capped: list[WorkspaceTestRecommendation] = []
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for rec in deduplicated:
        pair = (rec.consumer_repo, rec.provider_repo)
        if counts[pair] < MAX_TESTS_PER_TARGET:
            capped.append(rec)
            counts[pair] += 1

    by_basis: dict[str, int] = defaultdict(int)
    by_repo: dict[str, int] = defaultdict(int)
    by_consumer_repo: dict[str, int] = defaultdict(int)
    for rec in capped:
        by_basis[rec.basis] += 1
        by_repo[rec.provider_repo] += 1
        by_consumer_repo[rec.consumer_repo] += 1

    states = summary["states"]
    for row in files_analyzed:
        states[row["state"]] = states.get(row["state"], 0) + 1
    summary["consumer_repos_analyzed"] = [a for a in aliases if index.get(a) is not None]
    summary["consumer_repos_without_index"] = [a for a in aliases if index.get(a) is None]

    return WorkspaceTestImpactResult(
        recommendations=capped,
        recommendations_total=len(deduplicated),
        recommendations_emitted=len(capped),
        recommendations_truncated=len(capped) < len(deduplicated),
        recommendations_omitted=len(deduplicated) - len(capped),
        recommendations_by_basis=dict(by_basis),
        recommendations_by_repo=dict(by_repo),
        recommendations_by_consumer_repo=dict(by_consumer_repo),
        unresolved=unresolved,
        files_analyzed=files_analyzed,
        summary=summary,
    )


async def workspace_test_impact_from_root(
    workspace_root: Path | str,
    changed_files: Sequence[Mapping[str, str]],
    **options: Any,
) -> WorkspaceTestImpactResult:
    """:func:`analyze_workspace_test_impact` over an on-disk workspace."""
    root = Path(workspace_root)
    store = load_contract_store(root)
    if store is None:
        return WorkspaceTestImpactResult(
            summary={
                "reason": "no_contract_store",
                "hint": "Run `repowise update --workspace` to build the contract map.",
            }
        )
    ws_config = WorkspaceConfig.load(root)
    index = await open_workspace_index(ws_config, root)
    try:
        return await analyze_workspace_test_impact(
            index, store.contract_links, changed_files, **options
        )
    finally:
        await index.close()


def workspace_test_impact_to_dict(result: WorkspaceTestImpactResult) -> dict[str, Any]:
    """Serialize the result for the CLI, the API and MCP."""
    return {
        "workspace": result.workspace,
        "recommendations": [
            {
                "test_id": r.test_id,
                "test_file": r.test_file,
                "consumer_repo": r.consumer_repo,
                "consumer_file": r.consumer_file,
                "consumer_symbol_id": r.consumer_symbol_id,
                "provider_repo": r.provider_repo,
                "provider_file": r.provider_file,
                "contract_id": r.contract_id,
                "contract_type": r.contract_type,
                "basis": r.basis,
                "via": r.via,
                "confidence": r.confidence,
                "source_files": r.source_files,
                "evidence": r.evidence,
            }
            for r in result.recommendations
        ],
        "recommendations_total": result.recommendations_total,
        "recommendations_emitted": result.recommendations_emitted,
        "recommendations_truncated": result.recommendations_truncated,
        "recommendations_omitted": result.recommendations_omitted,
        "recommendations_by_basis": result.recommendations_by_basis,
        "recommendations_by_repo": result.recommendations_by_repo,
        "recommendations_by_consumer_repo": result.recommendations_by_consumer_repo,
        "unresolved": [
            {
                "consumer_repo": u.consumer_repo,
                "consumer_file": u.consumer_file,
                "consumer_symbol_id": u.consumer_symbol_id,
                "provider_repo": u.provider_repo,
                "provider_file": u.provider_file,
                "contract_id": u.contract_id,
                "contract_type": u.contract_type,
                "reason": u.reason,
                "detail": u.detail,
            }
            for u in result.unresolved
        ],
        "files_analyzed": result.files_analyzed,
        "summary": result.summary,
    }
