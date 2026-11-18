"""Single-target risk scoring for get_risk."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.pr_blast import untested_files
from repowise.core.analysis.security_scan import SYMBOL_NAME_KINDS
from repowise.core.co_change import confidence_ratio, parse_partners
from repowise.core.persistence.models import (
    GitMetadata,
    Repository,
)
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import (
    filter_dicts_by_key,
    is_excluded,
)
from repowise.server.mcp_server._meta import served_root
from repowise.server.mcp_server.tool_risk.history import (  # noqa: F401  re-exported
    _BUG_PRONE_FIXES,
    _TOP_FIX_SYMBOLS,
    _churn_clause,
    _classify_risk_type,
    _compute_trend,
    _defect_profile,
    _derive_change_pattern,
    _fix_clause,
    _history_tier_card,
    _load_commit_categories,
    _owner_clause,
    _top_fix_symbols,
    fix_annotation,
    history_fields,
    risk_summary,
)
from repowise.server.repo_paths import missing_path_hint, normalize_target_path

# Relationship rows are deliberately bounded independently.  Their totals are
# computed after repository exclusions and before this presentation cap.
_RELATIONSHIP_LIMIT = 5


async def _get_active_contributor_count(session: AsyncSession, repo_id: str) -> int | None:
    """Repo-wide active-contributor count from persisted git metadata.

    Reuses ``count_active_contributors`` (per-author ``last_commit_ts`` in
    ``top_authors_json``) over all rows. ``None`` = unknown (no rows, or an
    index that predates per-author timestamps).
    """
    from repowise.core.ingestion.git_indexer import count_active_contributors

    try:
        rows = await session.execute(
            select(GitMetadata.top_authors_json).where(GitMetadata.repository_id == repo_id)
        )
        metas = [{"top_authors_json": r[0]} for r in rows.all() if r[0]]
        if not metas:
            return None
        return count_active_contributors(metas)
    except Exception:
        return None


def _reverse_neighbors(reverse_deps: dict[str, Any], node: str) -> dict[str, set[str]]:
    """Normalize one reverse-adjacency entry to ``dependent -> edge types``.

    The set-shaped fallback keeps the small pure helper tests and older callers
    compatible. Production passes the typed mapping built by ``get_risk``.
    """
    raw = reverse_deps.get(node, {})
    if isinstance(raw, dict):
        return {
            str(dependent): {str(edge_type) for edge_type in edge_types}
            for dependent, edge_types in raw.items()
        }
    return {str(dependent): {"dependency"} for dependent in raw}


def _dependency_population(
    target: str,
    reverse_deps: dict[str, Any],
    node_meta: dict[str, Any],
    exclude_spec: Any = None,
) -> list[dict]:
    """Directed structural dependents within two hops, one row per node.

    Graph edges are stored ``source depends on target``. Walking the reverse
    adjacency therefore discovers only nodes that depend on *target*. Each row
    retains direct/transitive distance and the typed structural path; it makes
    no runtime-breakage claim.
    """
    discovered: dict[str, dict] = {}
    frontier = {target}
    paths: dict[str, list[dict]] = {target: []}
    for distance in (1, 2):
        next_frontier: set[str] = set()
        for node in sorted(frontier):
            for dependent, edge_types in sorted(_reverse_neighbors(reverse_deps, node).items()):
                if (
                    dependent == target
                    or dependent in discovered
                    or is_excluded(dependent, exclude_spec)
                ):
                    continue
                edge = {
                    "source": dependent,
                    "target": node,
                    "relationship_types": sorted(edge_types),
                }
                path = [edge, *paths.get(node, [])]
                meta = node_meta.get(dependent)
                row = {
                    "node_id": dependent,
                    "file_path": dependent,
                    "target": target,
                    "direction": "dependent_to_dependency",
                    "evidence_kind": "structural",
                    "claim": "structural_reach",
                    "distance": distance,
                    "direct": distance == 1,
                    "relationship_types": sorted(edge_types)
                    if distance == 1
                    else ["transitive_dependency"],
                    "path": path,
                    "pagerank": meta.pagerank if meta else 0.0,
                    "is_entry_point": meta.is_entry_point if meta else False,
                }
                if distance > 1:
                    row["via"] = node
                discovered[dependent] = row
                paths[dependent] = path
                next_frontier.add(dependent)
        frontier = next_frontier

    population = filter_dicts_by_key(list(discovered.values()), "file_path", exclude_spec)
    population.sort(key=lambda row: (row["distance"], row["file_path"]))
    return population


def _compute_impact_surface(
    target: str,
    reverse_deps: dict[str, Any],
    node_meta: dict[str, Any],
    exclude_spec: Any = None,
) -> list[dict]:
    """Legacy top-three structural reach view, derived from typed dependents."""
    ranked = _dependency_population(target, reverse_deps, node_meta, exclude_spec)
    # Path breaks the tie, or the answer is not the same twice. ``visited`` is
    # a set, so ``ranked`` starts in hash order, and a stable sort keeps that
    # order wherever pagerank ties — which it does constantly, since most
    # dependents sit at 0.0. Two identical get_risk calls were returning
    # different "top 3 most critical modules" (measured: tests/test_progress.py
    # vs examples/fullscreen.py in the same slot, same tree, minutes apart).
    ranked.sort(key=lambda x: (-x["pagerank"], x["file_path"]))
    return ranked[:3]


async def _check_test_gap(session: AsyncSession, repo_id: str, target: str) -> bool:
    """Whether nothing can be shown to test *target* (``pr_blast.untested_files``)."""
    return bool(await untested_files(session, repo_id, [target]))


async def _get_security_signals(session: AsyncSession, repo_id: str, target: str) -> list[dict]:
    """Fetch stored security findings for *target* from security_findings table.

    Symbol-name rows stay stored for the dashboard; next to real findings they
    only read as alarms, so an agent never sees them.
    """
    try:
        rows = await session.execute(
            text(
                "SELECT kind, severity, snippet FROM security_findings "
                "WHERE repository_id = :repo_id AND file_path = :fp "
                # Ranked, not alphabetical: text order puts ``high`` last.
                "ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'med' THEN 1 ELSE 2 END, kind"
            ),
            {"repo_id": repo_id, "fp": target},
        )
        return [
            {"kind": r[0], "severity": r[1], "snippet": r[2]}
            for r in rows.all()
            if r[0] not in SYMBOL_NAME_KINDS
        ]
    except Exception:
        return []


def _build_co_changes(
    meta: Any, structural_related: Any, exclude_spec: Any
) -> tuple[list[dict], int]:
    """Historical co-change partners and their exact post-filter population.

    Larger lists make MCP responses verbose without adding signal: top-5 captures
    the bulk of the temporal-coupling mass and keeps tool output tight for agents.

    Row order stays by recency-decayed ``weight``. Each row carries ``file_path``,
    ``support`` (shared commit count, when known), ``conf_ab`` (share of target's
    commits that touched partner, when known), and ``has_import_link``.
    """
    partners_sorted = parse_partners(meta.co_change_partners_json)
    relation_types = structural_related if isinstance(structural_related, dict) else {}
    related_paths = set(structural_related)
    rows = []
    for partner in partners_sorted:
        path = partner.file_path
        types = sorted(relation_types.get(path, ()))
        conf_ab = confidence_ratio(partner.support, partner.self_commits)
        row: dict[str, Any] = {
            "file_path": path,
            "has_import_link": "imports" in types if types else path in related_paths,
        }
        if partner.support:
            row["support"] = partner.support
        if conf_ab is not None:
            row["conf_ab"] = conf_ab
        rows.append(row)
    population = filter_dicts_by_key(rows, "file_path", exclude_spec)
    return population, len(population)


async def _unresolved_reason(
    target: str, lookup_path: str, repo_root: str | None
) -> dict[str, Any]:
    """Why *target* names nothing this tool can score, in the caller's terms.

    ``not_indexed`` and ``no_such_path`` are spelled as
    ``get_health._unresolved_targets`` spells them. The other two are not
    borrowed: ``get_health``'s ``no_such_module`` means "no module of that
    name", and it resolves a directory to ``not_indexed`` — both would
    prescribe a fix that cannot help here.

    ``no_such_path`` also carries what :func:`missing_path_hint` finds (a
    rename, or another branch holding the path), off the thread so the
    blocking git calls do not stall the loop.
    """
    if target.startswith("module:"):
        return {"unresolved_reason": "unsupported_target_kind"}
    try:
        on_disk = Path(repo_root) / lookup_path if repo_root else Path(lookup_path)
        if on_disk.is_dir():
            return {"unresolved_reason": "directory"}
        if on_disk.exists():
            return {"unresolved_reason": "not_indexed"}
    except (OSError, ValueError):
        pass
    hint = await asyncio.to_thread(missing_path_hint, repo_root, lookup_path)
    return {"unresolved_reason": "no_such_path", **hint}


def _bounded_dependents(population: list[dict]) -> list[dict]:
    """The first rows of *population*, keeping one transitive row when any exists.

    Without it a large direct fan-in would hide every transitive row while the
    totals say transitive reach exists.
    """
    dependents = population[:_RELATIONSHIP_LIMIT]
    first_transitive = next((row for row in population if not row["direct"]), None)
    if (
        first_transitive is not None
        and dependents
        and all(row["direct"] for row in dependents)
        and len(population) > len(dependents)
    ):
        dependents[-1] = first_transitive
    return dependents


def _relationship_analysis() -> dict[str, Any]:
    """Fresh ``relationship_analysis`` block; workspace enrichment fills the rest."""
    return {
        "dependencies": {
            "status": "available",
            "scope": "repository_graph",
            "evidence_kind": "structural",
            "max_depth": 2,
            "runtime_breakage_claim": False,
        },
        "consumers": {
            "status": "unavailable",
            "scope": "workspace_contract_links",
            "reason": "workspace contract analysis is unavailable",
        },
        "cross_repo": {
            "status": "unavailable",
            "scope": "workspace_file_relationships",
            "reason": "workspace relationship analysis is unavailable",
        },
    }


def _dependency_card(
    target: str,
    lookup_path: str,
    reverse_deps: dict[str, dict[str, set[str]]],
    node_meta: dict[str, Any],
    exclude_spec: Any,
    collector: OmissionCollector | None,
) -> tuple[dict[str, Any], int]:
    """The card's structural-dependents fields and its direct dependents count."""
    population = _dependency_population(lookup_path, reverse_deps, node_meta, exclude_spec)
    dependents = _bounded_dependents(population)
    ranked = sorted(population, key=lambda row: (-row["pagerank"], row["file_path"]))
    impact_surface = ranked[:3]
    direct_total = sum(row["direct"] for row in population)
    # ``dependents_count`` is the compatibility field: direct graph dependents
    # over the same post-exclusion population as the typed rows.
    card: dict[str, Any] = {
        "target": target,
        "dependents_count": direct_total,
        "dependents": dependents,
        "dependents_total": len(population),
        "dependents_emitted": len(dependents),
        "dependents_truncated": len(dependents) < len(population),
        "direct_dependents_total": direct_total,
        "transitive_dependents_total": len(population) - direct_total,
        "impact_surface": impact_surface,
        "impact_surface_total": len(population),
        "impact_surface_emitted": len(impact_surface),
        "impact_surface_truncated": len(impact_surface) < len(population),
        "consumers": [],
        "consumers_total": 0,
        "consumers_emitted": 0,
        "consumers_truncated": False,
        "cross_repo_links": [],
        "cross_repo_links_total": 0,
        "cross_repo_links_emitted": 0,
        "cross_repo_links_truncated": False,
        "relationship_analysis": _relationship_analysis(),
    }
    cap_collection(
        card,
        "dependents",
        population,
        _RELATIONSHIP_LIMIT,
        collector,
        emitted=dependents,
        label=f"{target} :: dependents beyond cap={_RELATIONSHIP_LIMIT}",
        preserve_counts=True,
    )
    cap_collection(
        card,
        "impact_surface",
        ranked,
        3,
        collector,
        emitted=impact_surface,
        label=f"{target} :: impact_surface beyond cap=3",
        preserve_counts=True,
    )
    return card, direct_total


async def _load_git_metadata(
    session: AsyncSession, repo_id: str, lookup_path: str
) -> GitMetadata | None:
    """The target's ``GitMetadata`` row, keyed on the normalized POSIX path."""
    res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.file_path == lookup_path,
        )
    )
    return res.scalar_one_or_none()


async def _no_metadata_card(
    card: dict[str, Any],
    session: AsyncSession,
    repo_id: str,
    target: str,
    lookup_path: str,
    dep_count: int,
) -> dict[str, Any]:
    """Finish the card of a graph node with no git row (a new file)."""
    card.update(
        {
            "hotspot_score": 0.0,
            "is_hotspot": False,
            "co_change_partners": [],
            "co_change_partners_total": 0,
            "co_change_partners_emitted": 0,
            "co_change_partners_truncated": False,
        }
    )
    card["relationship_analysis"]["co_change"] = {
        "status": "unavailable",
        "scope": "git_history",
        "reason": "no git metadata is available for the target",
    }
    card.update(
        {
            "primary_owner": None,
            "owner_pct": None,
            "trend": "unknown",
            "risk_type": "high-coupling" if dep_count >= 5 else "unknown",
        }
    )
    card["test_gap"] = await _check_test_gap(session, repo_id, lookup_path)
    card["security_signals"] = await _get_security_signals(session, repo_id, lookup_path)
    card["risk_summary"] = f"{target} — no git metadata available"
    return card


def _apply_co_changes(
    card: dict[str, Any],
    meta: Any,
    target: str,
    structural_related: Any,
    exclude_spec: Any,
    collector: OmissionCollector | None,
) -> int:
    """Add the hotspot flags and co-change partners; return the partner total."""
    population, total = _build_co_changes(meta, structural_related, exclude_spec)
    co_changes = population[:_RELATIONSHIP_LIMIT]
    card["hotspot_score"] = meta.churn_percentile or 0.0
    # The stored flag already applies the absolute activity floors, which a
    # client thresholding the score alone cannot reproduce.
    card["is_hotspot"] = bool(getattr(meta, "is_hotspot", False))
    card["co_change_partners"] = co_changes
    card["co_change_partners_total"] = total
    card["co_change_partners_emitted"] = len(co_changes)
    card["co_change_partners_truncated"] = len(co_changes) < total
    cap_collection(
        card,
        "co_change_partners",
        population,
        _RELATIONSHIP_LIMIT,
        collector,
        label=f"{target} :: co_change_partners beyond cap={_RELATIONSHIP_LIMIT}",
        preserve_counts=True,
    )
    card["relationship_analysis"]["co_change"] = {
        "status": "available",
        "scope": "git_history",
        "evidence_kind": "historical",
    }
    return total


async def _assess_one_target(
    session: AsyncSession,
    repository: Repository,
    target: str,
    all_edge_map: dict[str, int],
    import_links: dict[str, dict[str, set[str]]],
    reverse_deps: dict[str, dict[str, set[str]]],
    node_meta: dict[str, Any],
    exclude_spec: Any = None,
    team_size: int | None = None,
    collector: OmissionCollector | None = None,
    include_graph: bool = False,
    as_of_ts: datetime | None = None,
    repo_root: str | None = None,
) -> dict:
    """Assess risk for a single target file.

    Enriches each result with:
    - test_gap: bool, True when nothing shows a test reaches this file
      (``pr_blast.untested_files``: coverage, the reach walk, or a test named for it).
    - security_signals: list of {kind, severity, snippet} from security_findings.
    """
    repo_id = repository.id
    repo_root = served_root(repository, repo_root)
    # Callers pass paths in many forms (backslashes, ``./``, repo-absolute);
    # stored rows are POSIX-relative. Every lookup keys on the normalized path,
    # the response on what the caller asked for.
    lookup_path = normalize_target_path(target, repo_root=repo_root)
    # ``all_edge_map`` stays in the signature for older callers; the typed
    # adjacency drives the counts.
    result_data, dep_count = _dependency_card(
        target,
        lookup_path,
        reverse_deps,
        node_meta,
        exclude_spec,
        collector if include_graph else None,
    )
    meta = await _load_git_metadata(session, repo_id, lookup_path)

    if meta is None and lookup_path not in node_meta and "::" not in target:
        # Nothing measured this target, so every count would be a structural
        # zero. A graph node without a git row (a new file) and a
        # ``path::Symbol`` id are real targets and fall through.
        return {
            "target": target,
            "resolved": False,
            **await _unresolved_reason(target, lookup_path, repo_root),
            "risk_summary": (
                f"{target} — not resolved to an indexed file; no risk signal was computed"
            ),
        }

    if getattr(meta, "history_only", False) is True:
        security = await _get_security_signals(session, repo_id, lookup_path)
        return _history_tier_card(target, meta, security)

    if meta is None:
        return await _no_metadata_card(
            result_data, session, repo_id, target, lookup_path, dep_count
        )

    co_changes_total = _apply_co_changes(
        result_data, meta, target, import_links.get(lookup_path, {}), exclude_spec, collector
    )
    result_data.update(history_fields(meta, dep_count, team_size))
    defect_profile = _defect_profile(meta, now=as_of_ts)
    if defect_profile is not None:
        result_data["defect_profile"] = defect_profile
    result_data["test_gap"] = await _check_test_gap(session, repo_id, lookup_path)
    result_data["security_signals"] = await _get_security_signals(session, repo_id, lookup_path)
    result_data["commit_count_capped"] = getattr(meta, "commit_count_capped", False)
    result_data["risk_summary"] = risk_summary(
        target, meta, result_data, defect_profile, dep_count, co_changes_total
    )
    return result_data
