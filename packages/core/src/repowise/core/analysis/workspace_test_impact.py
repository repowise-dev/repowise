"""Cross-repository test impact analysis for workspaces.

Joins the workspace contract map (provider -> consumer call sites) with
per-repo test reachability (test -> reached files) to answer:

  "Given a change in provider repo X, which tests in consumer repos Y, Z
  should I run?"

This is the missing pillar in the workspace intelligence matrix:
- Contracts ✓ (provider↔consumer endpoints)
- Blast radius ✓ (structural dependents)
- Breaking changes ✓ (contract diffs)
- Test impact ← this module

The analysis flow:
  1. Input: changed files in provider repo(s)
  2. Find contracts provided by those files (from contracts.json)
  3. Get contract links → consumer repos + consumer call sites (files)
  4. For each consumer repo: load its test reachability index
  5. Map consumer call site file → test files via tests_reaching_by_tier
  6. Aggregate, deduplicate, rank by (basis_priority, repo, confidence)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.analysis.test_reachability import (
    DEFAULT_CALL_DEPTH,
    DEFAULT_MAX_DEPTH,
    MAX_TESTS_PER_TARGET,
    ReachedBy,
    tests_reaching_by_tier,
)
from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import get_repository_by_path
from repowise.core.workspace.config import WorkspaceConfig
from repowise.core.workspace.contracts import ContractLink, load_contract_store

_BASIS_ORDER = {"measured": 0, "inferred": 1}

_TIER_PRIORITY = {"call-graph": 0, "import-graph": 1}


@dataclass(frozen=True)
class WorkspaceTestRecommendation:
    """A test recommendation from a consumer repo for a provider change."""

    test_id: str
    test_file: str
    consumer_repo: str
    consumer_repo_alias: str
    provider_repo: str
    provider_file: str
    contract_id: str
    contract_type: str
    basis: str  # "measured" | "inferred"
    via: str  # "coverage-map" | "call-graph" | "import-graph"
    confidence: float
    source_files: list[str]  # provider files this test ultimately reaches
    evidence: list[dict[str, Any]]


@dataclass
class WorkspaceTestImpactResult:
    """Complete workspace test impact analysis result."""

    workspace: bool = True
    recommendations: list[WorkspaceTestRecommendation] | None = None
    recommendations_total: int = 0
    recommendations_by_basis: dict[str, int] | None = None
    recommendations_by_repo: dict[str, int] | None = None
    recommendations_by_consumer_repo: dict[str, int] | None = None
    files_analyzed: list[dict[str, Any]] | None = None
    summary: dict[str, Any] | None = None

    def __post_init__(self):
        if self.recommendations is None:
            self.recommendations = []
        if self.recommendations_by_basis is None:
            self.recommendations_by_basis = {"measured": 0, "inferred": 0}
        if self.recommendations_by_repo is None:
            self.recommendations_by_repo = {}
        if self.recommendations_by_consumer_repo is None:
            self.recommendations_by_consumer_repo = {}
        if self.files_analyzed is None:
            self.files_analyzed = []
        if self.summary is None:
            self.summary = {}


async def _get_repo_session_factory(workspace_root: Path, repo_alias: str) -> Any | None:
    """Get the session factory for a workspace repo by alias."""
    ws_config = WorkspaceConfig.load(workspace_root)
    entry = next((e for e in ws_config.repos if e.alias == repo_alias), None)
    if not entry:
        return None
    repo_path = (workspace_root / entry.path).resolve()
    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.exists():
        return None
    from repowise.core.persistence.database import resolve_db_url
    engine = create_engine(resolve_db_url(repo_path))
    return create_session_factory(engine)


async def _analyze_consumer_repo(
    session_factory: Any,
    consumer_repo_alias: str,
    consumer_file_paths: list[str],
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, ReachedBy]:
    """Run test reachability analysis for a consumer repo.

    Returns mapping: consumer_file_path -> ReachedBy (tests reaching it).
    """
    async with get_session(session_factory) as session:
        repo_row = await get_repository_by_path(session, consumer_repo_alias)
        if repo_row is None:
            return {}
        try:
            return await tests_reaching_by_tier(
                session,
                repo_row.id,
                consumer_file_paths,
                call_depth=call_depth,
                import_depth=import_depth,
            )
        except Exception:
            return {}


async def _get_measured_coverage_for_consumer(
    session_factory: Any,
    consumer_repo_alias: str,
    consumer_file_paths: list[str],
) -> dict[str, list[dict]]:
    """Get measured per-test coverage for consumer files.

    Returns mapping: consumer_file_path -> list of {test_id, test_file, source_format}.
    """
    from repowise.core.persistence.crud import get_test_coverage_summary, tests_covering

    async with get_session(session_factory) as session:
        repo_row = await get_repository_by_path(session, consumer_repo_alias)
        if repo_row is None:
            return {}
        try:
            summary = await get_test_coverage_summary(session, repo_row.id)
            if summary.get("pair_count", 0) == 0:
                return {}
        except Exception:
            return {}

        result: dict[str, list[dict]] = defaultdict(list)
        for path in consumer_file_paths:
            try:
                rows = await tests_covering(session, repo_row.id, path, lines=None)
            except Exception:
                continue
            for row in rows:
                test_id = str(row["test_id"])
                test_file = row.get("test_file")
                runnable_file = str(test_file or test_id.split("::", 1)[0])
                result[path].append(
                    {
                        "test_id": test_id,
                        "test_file": runnable_file,
                        "source_format": row.get("source_format"),
                    }
                )
        return dict(result)


def _build_provider_to_consumer_map(
    contract_links: list[ContractLink],
    provider_repos: set[str] | None = None,
) -> dict[str, list[ContractLink]]:
    """Build mapping: provider_repo -> list of ContractLink where provider is in provider_repos.

    If provider_repos is None, include all links.
    """
    result: dict[str, list[ContractLink]] = defaultdict(list)
    for link in contract_links:
        if provider_repos is None or link.provider_repo in provider_repos:
            result[link.provider_repo].append(link)
    return result


def _build_consumer_call_sites(
    links: list[ContractLink],
) -> dict[str, dict[str, list[ContractLink]]]:
    """Build mapping: consumer_repo -> consumer_file -> list of ContractLink.

    This groups all links by the consumer repo and file, so we can query
    test reachability per consumer file once.
    """
    result: dict[str, dict[str, list[ContractLink]]] = defaultdict(lambda: defaultdict(list))
    for link in links:
        result[link.consumer_repo][link.consumer_file].append(link)
    return dict(result)


def _recommendation_sort_key(rec: WorkspaceTestRecommendation) -> tuple:
    """Sort key: more source files first, then basis priority, then repo, then test_id."""
    return (
        -len(rec.source_files),
        _BASIS_ORDER.get(rec.basis, 99),
        _TIER_PRIORITY.get(rec.via, 99),
        rec.consumer_repo,
        rec.test_id,
    )


async def analyze_workspace_test_impact(
    workspace_root: Path | str,
    changed_files: list[dict[str, str]],  # [{"repo": "alias", "path": "file.py"}, ...]
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
    include_measured: bool = True,
    include_inferred: bool = True,
    min_confidence: float = 0.0,
    target_repos: list[str] | None = None,
) -> WorkspaceTestImpactResult:
    """Analyze test impact across workspace for given provider changes.

    Args:
        workspace_root: Path to workspace root (where .repowise-workspace.yaml lives)
        changed_files: List of {"repo": provider_alias, "path": file_path} dicts
        call_depth: Call graph walk depth (default 3)
        import_depth: Import graph fallback depth (default 1)
        include_measured: Include coverage-backed recommendations
        include_inferred: Include graph-inferred recommendations
        min_confidence: Minimum contract link confidence to consider
        target_repos: Optional list of consumer repo aliases to limit to

    Returns:
        WorkspaceTestImpactResult with recommendations and summary
    """
    workspace_root = Path(workspace_root)

    # Load contract store
    contract_store = load_contract_store(workspace_root)
    if contract_store is None:
        return WorkspaceTestImpactResult(
            workspace=True,
            summary={"error": "No contract store found. Run `repowise update --workspace` first."},
        )

    # Filter links by confidence and target repos
    links = [
        lk
        for lk in contract_store.contract_links
        if lk.confidence >= min_confidence
        and (target_repos is None or lk.consumer_repo in target_repos)
    ]

    if not links:
        return WorkspaceTestImpactResult(
            workspace=True,
            summary={"error": "No contract links match the criteria"},
        )

    # Group changed files by provider repo
    changed_by_provider: dict[str, set[str]] = defaultdict(set)
    for cf in changed_files:
        repo = cf.get("repo")
        path = cf.get("path")
        if repo and path:
            changed_by_provider[repo].add(path)

    # Build provider_repo -> links map
    provider_to_links = _build_provider_to_consumer_map(
        links, provider_repos=set(changed_by_provider.keys())
    )

    # For each provider repo with changes, find affected consumer call sites
    consumer_call_sites: dict[str, dict[str, list[ContractLink]]] = {}
    for provider_repo, provider_links in provider_to_links.items():
        provider_changed = changed_by_provider.get(provider_repo, set())
        # Filter links to only those whose provider file is in changed set
        relevant_links = [
            lk for lk in provider_links if lk.provider_file in provider_changed
        ]
        if relevant_links:
            consumer_call_sites.update(_build_consumer_call_sites(relevant_links))

    if not consumer_call_sites:
        return WorkspaceTestImpactResult(
            workspace=True,
            summary={
                "error": "No contract links connect changed provider files to consumers",
                "changed_files": {k: list(v) for k, v in changed_by_provider.items()},
            },
        )

    # For each consumer repo, run test reachability analysis
    all_recommendations: list[WorkspaceTestRecommendation] = []
    files_analyzed: list[dict[str, Any]] = []

    # Process consumer repos in parallel
    async def process_consumer_repo(
        consumer_repo_alias: str,
        consumer_files: dict[str, list[ContractLink]],
    ) -> list[WorkspaceTestRecommendation]:
        session_factory = await _get_repo_session_factory(workspace_root, consumer_repo_alias)
        if session_factory is None:
            return []

        consumer_file_paths = list(consumer_files.keys())

        # Run measured + inferred in parallel
        measured_task = None
        if include_measured:
            measured_task = asyncio.create_task(
                _get_measured_coverage_for_consumer(
                    session_factory, consumer_repo_alias, consumer_file_paths
                )
            )

        inferred_task = asyncio.create_task(
            _analyze_consumer_repo(
                session_factory,
                consumer_repo_alias,
                consumer_file_paths,
                call_depth=call_depth,
                import_depth=import_depth,
            )
        )

        measured_coverage = {}
        if measured_task:
            measured_coverage = await measured_task
        inferred_reachability = await inferred_task

        # Build recommendations for this consumer repo
        recommendations: list[WorkspaceTestRecommendation] = []
        for consumer_file, links_for_file in consumer_files.items():
            # Measured coverage for this consumer file
            measured_tests = measured_coverage.get(consumer_file, [])
            for mt in measured_tests:
                # Find the contract link(s) this test relates to
                for link in links_for_file:
                    rec = WorkspaceTestRecommendation(
                        test_id=mt["test_id"],
                        test_file=mt["test_file"],
                        consumer_repo=consumer_repo_alias,
                        consumer_repo_alias=consumer_repo_alias,
                        provider_repo=link.provider_repo,
                        provider_file=link.provider_file,
                        contract_id=link.contract_id,
                        contract_type=link.contract_type,
                        basis="measured",
                        via="coverage-map",
                        confidence=link.confidence,
                        source_files=[link.provider_file],
                        evidence=[
                            {
                                "basis": "measured",
                                "source_file": link.provider_file,
                                "via": "coverage-map",
                                "source_format": mt.get("source_format"),
                            }
                        ],
                    )
                    recommendations.append(rec)

            # Inferred reachability for this consumer file
            reached = inferred_reachability.get(consumer_file)
            if reached:
                for link in links_for_file:
                    for test_id in reached.tests:
                        rec = WorkspaceTestRecommendation(
                            test_id=test_id,
                            test_file=test_id.split("::", 1)[0],
                            consumer_repo=consumer_repo_alias,
                            consumer_repo_alias=consumer_repo_alias,
                            provider_repo=link.provider_repo,
                            provider_file=link.provider_file,
                            contract_id=link.contract_id,
                            contract_type=link.contract_type,
                            basis="inferred",
                            via=reached.via,
                            confidence=round(link.confidence * 0.9, 3),  # slightly lower for inferred
                            source_files=[link.provider_file],
                            evidence=[
                                {
                                    "basis": "inferred",
                                    "source_file": link.provider_file,
                                    "via": reached.via,
                                }
                            ],
                        )
                        recommendations.append(rec)

            files_analyzed.append(
                {
                    "consumer_repo": consumer_repo_alias,
                    "consumer_file": consumer_file,
                    "provider_repos": list(set(lk.provider_repo for lk in links_for_file)),
                    "contract_ids": list(set(lk.contract_id for lk in links_for_file)),
                    "measured_tests_count": len(measured_tests),
                    "inferred_tests_count": len(reached.tests) if reached else 0,
                    "via": reached.via if reached else None,
                }
            )

        return recommendations

    # Run all consumer repos in parallel
    consumer_tasks = [
        process_consumer_repo(consumer_repo, files)
        for consumer_repo, files in consumer_call_sites.items()
    ]
    consumer_results = await asyncio.gather(*consumer_tasks)

    for recs in consumer_results:
        all_recommendations.extend(recs)

    # Deduplicate by (test_id, consumer_repo, provider_repo, contract_id)
    # Keep the highest-priority evidence
    seen: dict[tuple, WorkspaceTestRecommendation] = {}
    for rec in all_recommendations:
        key = (rec.test_id, rec.consumer_repo, rec.provider_repo, rec.contract_id)
        if key not in seen:
            seen[key] = rec
        else:
            # Merge evidence, prefer higher priority basis
            existing = seen[key]
            if _BASIS_ORDER.get(rec.basis, 99) < _BASIS_ORDER.get(existing.basis, 99):
                # New one has higher priority basis, replace
                seen[key] = rec
            else:
                # Merge evidence
                existing.evidence.extend(rec.evidence)

    deduplicated = list(seen.values())
    deduplicated.sort(key=_recommendation_sort_key)

    # Apply MAX_TESTS_PER_TARGET cap per (consumer_repo, provider_repo) pair
    # This mirrors the single-repo behavior
    capped: list[WorkspaceTestRecommendation] = []
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for rec in deduplicated:
        pair = (rec.consumer_repo, rec.provider_repo)
        if counts[pair] < MAX_TESTS_PER_TARGET:
            capped.append(rec)
            counts[pair] += 1

    # Build summary
    by_basis: dict[str, int] = defaultdict(int)
    by_repo: dict[str, int] = defaultdict(int)
    by_consumer: dict[str, int] = defaultdict(int)
    for rec in capped:
        by_basis[rec.basis] += 1
        by_repo[rec.provider_repo] += 1
        by_consumer[rec.consumer_repo] += 1

    result = WorkspaceTestImpactResult(
        workspace=True,
        recommendations=capped,
        recommendations_total=len(capped),
        recommendations_by_basis=dict(by_basis),
        recommendations_by_repo=dict(by_repo),
        recommendations_by_consumer_repo=dict(by_consumer),
        files_analyzed=files_analyzed,
        summary={
            "changed_provider_files": {k: list(v) for k, v in changed_by_provider.items()},
            "consumer_repos_analyzed": list(consumer_call_sites.keys()),
            "total_contract_links": len(links),
            "relevant_contract_links": sum(len(v) for v in provider_to_links.values()),
        },
    )

    return result


def workspace_test_impact_to_dict(result: WorkspaceTestImpactResult) -> dict[str, Any]:
    """Serialize WorkspaceTestImpactResult to dict for API/MCP responses."""
    return {
        "workspace": result.workspace,
        "recommendations": [
            {
                "test_id": r.test_id,
                "test_file": r.test_file,
                "consumer_repo": r.consumer_repo,
                "consumer_repo_alias": r.consumer_repo_alias,
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
            for r in (result.recommendations or [])
        ],
        "recommendations_total": result.recommendations_total,
        "recommendations_by_basis": result.recommendations_by_basis or {},
        "recommendations_by_repo": result.recommendations_by_repo or {},
        "recommendations_by_consumer_repo": result.recommendations_by_consumer_repo or {},
        "files_analyzed": result.files_analyzed or [],
        "summary": result.summary or {},
    }