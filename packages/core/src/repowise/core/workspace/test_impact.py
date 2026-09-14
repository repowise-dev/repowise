"""Cross-repository test impact: which consumer tests guard a provider change.

Joins the workspace contract map (provider call site -> consumer call site)
with each consumer repo's test reachability, to answer "given a change in
provider repo X, which tests in consumer repos Y and Z should I run?".

The join enters the consumer's call graph at the contract's
``consumer_symbol_id`` instead of at the consumer file, so a test that reaches
an unrelated symbol in the same file is not recommended. A link that cannot be
followed is reported as an unresolved row with a reason, never dropped: an
empty answer always says which state produced it.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field, replace
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

# The call walk enters at the bound symbol; the import fallback only knows files.
_ENTRY_BY_TIER = {"call-graph": "symbol", "import-graph": "file"}

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
    # Plural: merging folds every call site the one test guards into this row,
    # and only bound links reach a row, so no symbol id here is ever None.
    consumer_files: list[str]
    consumer_symbol_ids: list[str]
    provider_repo: str
    contract_ids: list[str]
    contract_types: list[str]
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


def _signal_rank(rec: WorkspaceTestRecommendation) -> tuple[int, int]:
    return (_BASIS_ORDER.get(rec.basis, 99), _TIER_PRIORITY.get(rec.via, 99))


def _merge_recommendations(
    first: WorkspaceTestRecommendation, second: WorkspaceTestRecommendation
) -> WorkspaceTestRecommendation:
    """Fold two rows for the same test and repo pair into one.

    The row keeps the strongest signal but every contract and provider file it
    guards, so a reader sees the whole reason the test is on the list.
    """
    strongest, weaker = (
        (first, second) if _signal_rank(first) <= _signal_rank(second) else (second, first)
    )
    return replace(
        strongest,
        consumer_files=sorted(set(first.consumer_files) | set(second.consumer_files)),
        consumer_symbol_ids=sorted(
            set(first.consumer_symbol_ids) | set(second.consumer_symbol_ids)
        ),
        contract_ids=sorted(set(first.contract_ids) | set(second.contract_ids)),
        contract_types=sorted(set(first.contract_types) | set(second.contract_types)),
        confidence=max(first.confidence, second.confidence),
        source_files=sorted(set(first.source_files) | set(second.source_files)),
        evidence=[*strongest.evidence, *weaker.evidence],
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
    via: str | None = None,
) -> dict[str, Any]:
    return {
        "consumer_repo": alias,
        "consumer_file": consumer_file,
        "state": state,
        "measured_tests_count": measured_count,
        "inferred_tests_count": inferred_count,
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
    # A pass that fails takes down only its own signal; whatever the other pass
    # found, and the links already classified, still reach the caller.
    failures: list[str] = []
    if files:
        session = repo_index.session
        if include_measured:
            try:
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
            except Exception as exc:
                failures.append(f"measured: {type(exc).__name__}")
        if include_inferred:
            try:
                # Two walks so each link is credited only with what reaches its
                # own symbol: the call tier is keyed by symbol id, and the import
                # tier, which only knows files, runs for the files no symbol answered.
                symbol_ids = sorted({sid for path in files for sid in seeds[path]})
                by_symbol = await tests_reaching_by_tier(
                    session,
                    repo_index.repo_id,
                    symbol_ids,
                    call_depth=call_depth,
                    import_depth=0,
                    symbol_seeds={sid: {sid} for sid in symbol_ids},
                )
                unanswered = [
                    path for path in files if not any(sid in by_symbol for sid in seeds[path])
                ]
                by_file_reached = await tests_reaching_by_tier(
                    session,
                    repo_index.repo_id,
                    unanswered,
                    call_depth=0,
                    import_depth=import_depth,
                )
                reached = {**by_file_reached, **by_symbol}
            except Exception as exc:
                failures.append(f"inferred: {type(exc).__name__}")

    if failures:
        detail = "; ".join(failures)
        for group in bound.values():
            out.unresolved.extend(_unresolved_for(lk, "lookup_failed", detail) for lk in group)

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
                        consumer_files=[path],
                        consumer_symbol_ids=[link.consumer_symbol_id],
                        provider_repo=link.provider_repo,
                        contract_ids=[link.contract_id],
                        contract_types=[link.contract_type],
                        basis="measured",
                        via="coverage-map",
                        confidence=link.confidence,
                        source_files=[_norm(link.provider_file)],
                        evidence=[
                            {
                                "basis": "measured",
                                "source_file": _norm(link.provider_file),
                                "via": "coverage-map",
                                "contract_id": link.contract_id,
                                # Coverage rows are recorded per file, not per symbol.
                                "entry": "file",
                                "source_format": entry.get("source_format"),
                            }
                        ],
                    )
                )

        file_tests: set[str] = set()
        file_via: str | None = None
        for link in links_for_file:
            # A link's own symbol answers first; the file-level import tier
            # only speaks for files no symbol answered.
            hit = reached.get(link.consumer_symbol_id or "") or reached.get(path)
            if hit is None:
                continue
            # The walk trims its own list per target; the join caps per consumer
            # and provider pair below and reports the cut, so start from all of them.
            reached_tests = list(hit.all_tests or hit.tests)
            file_tests.update(reached_tests)
            if file_via is None or _TIER_PRIORITY.get(hit.via, 99) < _TIER_PRIORITY.get(file_via, 99):
                file_via = hit.via
            for test_id in reached_tests:
                out.recommendations.append(
                    WorkspaceTestRecommendation(
                        test_id=test_id,
                        test_file=test_id.split("::", 1)[0],
                        consumer_repo=alias,
                        consumer_files=[path],
                        consumer_symbol_ids=[link.consumer_symbol_id],
                        provider_repo=link.provider_repo,
                        contract_ids=[link.contract_id],
                        contract_types=[link.contract_type],
                        basis="inferred",
                        via=hit.via,
                        confidence=link.confidence,
                        source_files=[_norm(link.provider_file)],
                        evidence=[
                            {
                                "basis": "inferred",
                                "source_file": _norm(link.provider_file),
                                "via": hit.via,
                                "contract_id": link.contract_id,
                                "entry": _ENTRY_BY_TIER.get(hit.via, "file"),
                            }
                        ],
                    )
                )

        if measured_tests:
            state = "measured"
        elif file_tests:
            state = "inferred"
        elif links_for_file and not failures:
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
                inferred_count=len(file_tests),
                via=file_via,
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
    max_tests_per_pair: int | None = MAX_TESTS_PER_TARGET,
) -> WorkspaceTestImpactResult:
    """Tests in consumer repos that guard *changed_files* in provider repos.

    *index* and *links* are supplied by the caller, so a request handler that
    already holds both opens nothing here. ``max_tests_per_pair=None`` turns
    the per-pair cap off, for a caller that caps and banks the tail itself.
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
        # A caller that turned a pass off should see that in the answer.
        "passes": {"measured": include_measured, "inferred": include_inferred},
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
        key = (rec.test_id, rec.consumer_repo, rec.provider_repo)
        existing = seen.get(key)
        seen[key] = _merge_recommendations(existing, rec) if existing is not None else rec

    deduplicated = sorted(seen.values(), key=_recommendation_sort_key)

    if max_tests_per_pair is None:
        capped = deduplicated
    else:
        capped = []
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for rec in deduplicated:
            pair = (rec.consumer_repo, rec.provider_repo)
            if counts[pair] < max_tests_per_pair:
                capped.append(rec)
                counts[pair] += 1

    by_basis: dict[str, int] = {"measured": 0, "inferred": 0}
    by_repo: dict[str, int] = defaultdict(int)
    by_consumer_repo: dict[str, int] = defaultdict(int)
    for rec in capped:
        by_basis[rec.basis] = by_basis.get(rec.basis, 0) + 1
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
        recommendations_by_basis=by_basis,
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
    changed_repos = {entry.get("repo") for entry in changed_files if entry.get("repo")}
    # Only consumers of a changed repo can survive the join, and the primary
    # repo holds the largest symbol table while never being a consumer here.
    consumers = {
        link.consumer_repo
        for link in store.contract_links
        if link.provider_repo in changed_repos
    }
    index = await open_workspace_index(ws_config, root, aliases=consumers)
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
                "consumer_files": r.consumer_files,
                "consumer_symbol_ids": r.consumer_symbol_ids,
                "provider_repo": r.provider_repo,
                "contract_ids": r.contract_ids,
                "contract_types": r.contract_types,
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
