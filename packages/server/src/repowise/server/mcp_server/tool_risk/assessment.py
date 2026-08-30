"""Single-target risk scoring for get_risk."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.engine import _has_paired_test_file, _path_basenames
from repowise.core.co_change import parse_partners
from repowise.core.persistence.models import (
    GitMetadata,
    GraphNode,
    Repository,
)
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import (
    filter_dicts_by_key,
    is_excluded,
)

#: A file carrying this many counted bug fixes reads as bug-prone. Same trigger
#: the PR bot uses for prior defects, so the two surfaces agree on "a lot".
_BUG_PRONE_FIXES = 3

#: How many attributed symbols the defect profile names. Enough to say "mostly
#: these", short enough to keep the block inside the per-file token budget.
_TOP_FIX_SYMBOLS = 3

# Relationship rows are deliberately bounded independently.  Their totals are
# computed after repository exclusions and before this presentation cap.
_RELATIONSHIP_LIMIT = 5


def normalize_target_path(target: str, repo_root: str | None = None) -> str:
    """Normalize a caller-supplied file path to the POSIX-relative form stored
    in ``git_metadata.file_path``.

    ``get_risk`` matches ``file_path`` by exact string equality, but callers
    reach it through git tools, shell completion, or editors that hand over a
    backslash form (Windows), a leading ``./``, an absolute path, or a trailing
    separator. Any of those makes the row lookup miss, and ``_assess_one_target``
    then reports the indistinguishable ``no git metadata available`` card
    (hotspot_score=0, primary_owner=None, empty co_change_partners) even though
    the row exists — issue #1279. Normalizing the caller's side closes that gap.
    """
    normalized = target.replace("\\", "/")
    # Make a repo-absolute path (``/abs/repo/src/x.py``) relative to the repo
    # root when we know it. Uses a prefix check on the normalized forms, so a
    # path that is already repo-relative is left untouched.
    if repo_root:
        root_norm = str(Path(repo_root).resolve()).replace("\\", "/")
        try:
            # Resolve against the repo root, not the process cwd: the MCP
            # server's cwd is not the repo, so a relative path that happens
            # to exist there could resolve somewhere unrelated.
            resolved = Path(repo_root, normalized).resolve()
            if str(resolved).startswith(root_norm.rstrip("/") + "/"):
                normalized = str(resolved).replace("\\", "/")[len(root_norm.rstrip("/")) + 1 :]
        except (OSError, ValueError):
            # resolve() can raise ValueError on a malformed Windows path.
            pass
    # Strip a leading cwd-relative prefix and any leading slash left over.
    # A prefix strip, not lstrip: lstrip takes a character set, so it would
    # eat every leading dot (``.github/...`` -> ``github/...``).
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    # Collapse duplicate slashes and any trailing separator.
    parts = [p for p in normalized.split("/") if p]
    return "/".join(parts)


def _derive_change_pattern(categories: dict[str, int]) -> str:
    """Derive a human-readable change pattern from commit category counts."""
    if not categories:
        return "uncategorized"
    total = sum(categories.values())
    if total == 0:
        return "uncategorized"
    dominant = max(categories, key=lambda k: categories[k])
    ratio = categories[dominant] / total
    if ratio >= 0.5:
        labels = {
            "feature": "feature-active",
            "refactor": "primarily refactored",
            "fix": "fix-heavy",
            "dependency": "dependency-churn",
        }
        return labels.get(dominant, dominant)
    return "mixed-activity"


def _compute_trend(meta: Any) -> str:
    """Compute risk velocity from 30d vs 90d commit rates."""
    c30 = meta.commit_count_30d or 0
    c90 = meta.commit_count_90d or 0
    # Baseline: commits in the 60-day window before the last 30 days
    baseline_commits = c90 - c30
    recent_rate = c30 / 30.0
    baseline_rate = baseline_commits / 60.0

    if c90 == 0:
        return "stable"
    if baseline_rate == 0:
        return "increasing" if c30 > 0 else "stable"
    ratio = recent_rate / baseline_rate
    if ratio > 1.5:
        return "increasing"
    elif ratio < 0.5:
        return "decreasing"
    return "stable"


def _classify_risk_type(meta: Any, dep_count: int, team_size: int | None = None) -> str:
    """Classify risk as churn-heavy, bug-prone, high-coupling, or bus-factor-risk.

    *team_size* is the repo's active-contributor count (90d). On a small
    team (≤ SMALL_TEAM_MAX_CONTRIBUTORS) a single-author file is the
    expected operating model, so ``bus-factor-risk`` is reserved for
    hotspot-active files there (issue #361). ``None`` = unknown → keep
    the historical behaviour.

    ``bug-prone`` reads the counted fix history rather than the old keyword
    scan over ``significant_commits``: that scan matched "fix" anywhere in a
    subject, counted doc and test commits, and had no recency at all, while
    ``prior_defect_count`` is the shape-filtered windowed count and
    ``bug_magnet`` is its decayed form. An index that predates the fix-event
    rollup reports 0/None here and simply falls through to the next rule,
    the same way every other backfilled column behaves until the next update.
    """
    from repowise.core.analysis.health.biomarkers.base import SMALL_TEAM_MAX_CONTRIBUTORS

    churn_score = meta.churn_percentile or 0.0
    bus_factor = getattr(meta, "bus_factor", 0) or 0
    total_commits = meta.commit_count_total or 0

    small_team = team_size is not None and team_size <= SMALL_TEAM_MAX_CONTRIBUTORS

    # Bug-prone takes priority: real counted fixes, decayed or plain.
    prior_defects = getattr(meta, "prior_defect_count", 0) or 0
    if getattr(meta, "bug_magnet", False) or prior_defects >= _BUG_PRONE_FIXES:
        return "bug-prone"
    if churn_score >= 0.7:
        return "churn-heavy"
    if (
        bus_factor == 1
        and total_commits > 20
        and (not small_team or bool(getattr(meta, "is_hotspot", False)))
    ):
        return "bus-factor-risk"
    if dep_count >= 5:
        return "high-coupling"
    return "stable"


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
    """Return True if *target* has no test, coverage-backed where the map has data.

    Three signals, in descending order of what they can prove, and the file is a
    gap only when all three stay silent - the same ladder ``pr_blast``
    ``_find_test_gaps`` uses, because the two answered this question differently
    and a reader has no way to tell which one they are looking at.

    1. A per-test coverage row (from ``repowise coverage add``) is
       execution-proof: never a gap.
    2. A test file reaching it in the dependency graph is evidence, not proof,
       but a recorded edge rather than a guess - it catches the suites whose
       tests are named for behaviour rather than for the file under test.
    3. Otherwise the filename pattern (test_<name>, <name>_test, <name>.spec.*)
       - an honest "unknown", never asserted as untested.

    Test files themselves (is_test=True) are never a gap.
    """
    from repowise.core.persistence.crud import covered_source_files

    # Test files don't need tests — skip the check entirely
    node_res = await session.execute(
        select(GraphNode.is_test)
        .where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_id == target,
        )
        .limit(1)
    )
    row = node_res.scalar_one_or_none()
    if row is True:
        return False

    # Coverage proves a test exercises this file: not a gap.
    if await covered_source_files(session, repo_id, {target}):
        return False

    # The graph records a test reaching it: not a gap either. Degrades to "no
    # signal" rather than raising - a failed walk must not become an accusation.
    from repowise.core.analysis.test_reachability import tests_reaching

    try:
        if await tests_reaching(session, repo_id, [target]):
            return False
    except Exception:
        pass

    test_nodes = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_test == True,  # noqa: E712
        )
    )
    test_basenames = _path_basenames(set(test_nodes.scalars()))
    return not _has_paired_test_file(target, test_basenames)


async def _get_security_signals(session: AsyncSession, repo_id: str, target: str) -> list[dict]:
    """Fetch stored security findings for *target* from security_findings table."""
    try:
        rows = await session.execute(
            text(
                "SELECT kind, severity, snippet FROM security_findings "
                "WHERE repository_id = :repo_id AND file_path = :fp "
                "ORDER BY severity DESC, kind"
            ),
            {"repo_id": repo_id, "fp": target},
        )
        return [{"kind": r[0], "severity": r[1], "snippet": r[2]} for r in rows.all()]
    except Exception:
        return []


def _build_co_changes(
    meta: Any, structural_related: Any, exclude_spec: Any
) -> tuple[list[dict], int]:
    """Historical co-change partners and their exact post-filter population.

    Larger lists make MCP responses verbose without adding signal: top-5 captures
    the bulk of the temporal-coupling mass and keeps tool output tight for agents.

    The strength field is emitted as ``weight``, not ``count``: the stored value
    is a recency-decayed sum (``exp(-age_days / tau)`` per shared commit), so it
    is fractional. Named ``count`` it read as "5.52 co-changes" to every agent.
    """
    partners_sorted = parse_partners(meta.co_change_partners_json)
    relation_types = structural_related if isinstance(structural_related, dict) else {}
    related_paths = set(structural_related)
    rows = []
    for partner in partners_sorted:
        path = partner.file_path
        types = sorted(relation_types.get(path, ()))
        row = {
            "file_path": path,
            "weight": partner.weight,
            "last_co_change": partner.last_co_change,
            "relationship_type": "co_change",
            "direction": "undirected",
            "evidence_kind": "historical",
            "provenance": "git_history",
            "has_structural_link": path in related_paths,
            # Compatibility field: unlike the broader structural flag, this is
            # true only for an actual imports edge.
            "has_import_link": "imports" in types if types else path in related_paths,
        }
        if types:
            row["structural_relationship_types"] = types
        if partner.support:
            row["support"] = partner.support
        rows.append(row)
    population = filter_dicts_by_key(rows, "file_path", exclude_spec)
    return population, len(population)


def fix_annotation(meta: Any) -> dict | None:
    """Counted fixes, their age, and the magnet flag, or ``None`` for silence.

    The compact form every fix-history surface shares, so the recency contract
    is enforced once: the ``bug_magnet`` flag rides on the age and is never
    emitted alone. ``bug_magnet`` is a claim about RECENT fix pressure, so with
    no timestamp to anchor it the same word would describe a file fixed four
    times last month and one fixed four times two years ago.

    Read off the ``GitMetadata`` row the caller already loaded: no query.
    """
    count = getattr(meta, "prior_defect_count", 0) or 0
    if count <= 0:
        return None

    out: dict[str, Any] = {"fix_count": count}
    last_fix_at = getattr(meta, "last_fix_at", None)
    if isinstance(last_fix_at, datetime):
        # Rows are stored naive-UTC; compare on the same footing.
        moment = last_fix_at if last_fix_at.tzinfo else last_fix_at.replace(tzinfo=UTC)
        out["last_fix_days_ago"] = max(0, (datetime.now(UTC) - moment).days)
        if getattr(meta, "bug_magnet", False):
            out["bug_magnet"] = True
    return out


def _fix_clause(profile: dict | None) -> str:
    """Lead clause for ``risk_summary``, or empty when there is no fix history.

    Trailing separator included so the caller concatenates without a dangling
    comma on files that have never been fixed. Never renders a count without an
    age: an unanchored count reads as a claim about the distant past.
    """
    if not profile or "last_fix_days_ago" not in profile:
        return ""
    n = profile["fix_count"]
    magnet = " (bug magnet)" if profile.get("bug_magnet") else ""
    return (
        f"{n} bug fix{'es' if n != 1 else ''} in 6mo, "
        f"last {profile['last_fix_days_ago']}d ago{magnet}, "
    )


def _defect_profile(meta: Any) -> dict | None:
    """What this file's counted bug fixes say about it, or ``None`` for silence.

    Built from the fix-event rollup already loaded on the ``GitMetadata`` row,
    so there is no second query. Silent when the file has no counted fixes, so a
    clean file and a repo with no fix history both add nothing to the response
    (the FileSignalsPanel convention).

    Every field is aggregate. No inducing commit is named here or anywhere else:
    file-level SZZ ran at 74.5% precision against the frozen judgments, which is
    fine for counting and too thin to accuse a commit.

    The approximation caveat on ``top_symbols`` lives in ``get_risk``'s docstring
    rather than in each row. It is the same sentence every time, and a constant
    string repeated once per target is exactly the per-file cost the lean-MCP
    work went to some trouble to remove.
    """
    profile = fix_annotation(meta)
    if profile is None:
        return None
    profile["window"] = "6 months"

    symbols = _top_fix_symbols(getattr(meta, "fix_symbol_counts_json", None))
    if symbols:
        profile["top_symbols"] = symbols
    return profile


def _top_fix_symbols(raw: str | None) -> dict[str, int]:
    """Top few ``symbol -> fix count`` pairs, with the redundant path stripped.

    Stored keys are ``path/to/file.py::Name`` and the caller already knows the
    path, so the prefix is pure token cost in an MCP response. The stored map is
    written in descending-count order, so "top few" is a slice.
    """
    if not raw:
        return {}
    try:
        counts = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(counts, dict):
        return {}
    return {str(k).rsplit("::", 1)[-1]: int(v) for k, v in list(counts.items())[:_TOP_FIX_SYMBOLS]}


def _load_commit_categories(meta: Any) -> dict:
    """Parse the persisted commit-category counts, tolerating malformed JSON."""
    categories: dict = {}
    cat_json = getattr(meta, "commit_categories_json", None)
    if cat_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            categories = json.loads(cat_json)
    return categories


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
) -> dict:
    """Assess risk for a single target file.

    Enriches each result with:
    - test_gap: bool — True when no test file matching this file's basename exists.
    - security_signals: list of {kind, severity, snippet} from security_findings.
    """
    repo_id = repository.id
    result_data: dict[str, Any] = {"target": target}

    dependency_population = _dependency_population(target, reverse_deps, node_meta, exclude_spec)
    dependents = dependency_population[:_RELATIONSHIP_LIMIT]
    # If both distances exist, protect one transitive row from a large direct
    # fan-in. Otherwise the totals would say transitive reach exists while the
    # bounded typed field silently showed only direct rows.
    first_transitive = next((row for row in dependency_population if not row["direct"]), None)
    if (
        first_transitive is not None
        and dependents
        and all(row["direct"] for row in dependents)
        and len(dependency_population) > len(dependents)
    ):
        dependents[-1] = first_transitive
    impact_surface = sorted(
        dependency_population,
        key=lambda row: (-row["pagerank"], row["file_path"]),
    )[:3]
    direct_dependents_total = sum(row["direct"] for row in dependency_population)
    transitive_dependents_total = len(dependency_population) - direct_dependents_total
    # The compatibility count uses the same post-exclusion population as the
    # typed rows. ``all_edge_map`` remains in the signature for callers that
    # predate the typed adjacency, but no longer drives the public total.
    dep_count = direct_dependents_total

    result_data.update(
        {
            # ``dependents_count`` is the compatibility field: direct, directed
            # graph dependents only. It no longer absorbs co-change or contract
            # rows. The typed collection includes both direct and two-hop rows.
            "dependents_count": dep_count,
            "dependents": dependents,
            "dependents_total": len(dependency_population),
            "dependents_emitted": len(dependents),
            "dependents_truncated": len(dependents) < len(dependency_population),
            "direct_dependents_total": direct_dependents_total,
            "transitive_dependents_total": transitive_dependents_total,
            "impact_surface": impact_surface,
            "impact_surface_total": len(dependency_population),
            "impact_surface_emitted": len(impact_surface),
            "impact_surface_truncated": len(impact_surface) < len(dependency_population),
            "consumers": [],
            "consumers_total": 0,
            "consumers_emitted": 0,
            "consumers_truncated": False,
            "cross_repo_links": [],
            "cross_repo_links_total": 0,
            "cross_repo_links_emitted": 0,
            "cross_repo_links_truncated": False,
            "relationship_analysis": {
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
            },
        }
    )
    cap_collection(
        result_data,
        "dependents",
        dependency_population,
        _RELATIONSHIP_LIMIT,
        collector if include_graph else None,
        emitted=dependents,
        label=f"{target} :: dependents beyond cap={_RELATIONSHIP_LIMIT}",
        preserve_counts=True,
    )
    cap_collection(
        result_data,
        "impact_surface",
        sorted(dependency_population, key=lambda row: (-row["pagerank"], row["file_path"])),
        3,
        collector if include_graph else None,
        emitted=impact_surface,
        label=f"{target} :: impact_surface beyond cap=3",
        preserve_counts=True,
    )

    # Callers reach get_risk with the file path in many forms — backslashes
    # (Windows), a leading ``./``, a trailing separator, or a repo-absolute
    # path — while git_metadata.file_path (and the graph node/edge ids) are
    # stored POSIX-relative. Exact-string equality against the raw target made
    # a row that exists look absent, and _assess_one_target then reported the
    # indistinguishable "no git metadata available" card (hotspot_score=0,
    # primary_owner=None, empty co_change_partners) — issue #1279. Normalize
    # once and key every file-path lookup on it, but keep the response keyed by
    # what the caller asked for.
    lookup_path = normalize_target_path(target, repo_root=repository.local_path)

    # Git metadata
    res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repo_id,
            GitMetadata.file_path == lookup_path,
        )
    )
    meta = res.scalar_one_or_none()

    if meta is None:
        result_data["hotspot_score"] = 0.0
        result_data["is_hotspot"] = False
        result_data["co_change_partners"] = []
        result_data["co_change_partners_total"] = 0
        result_data["co_change_partners_emitted"] = 0
        result_data["co_change_partners_truncated"] = False
        result_data["relationship_analysis"]["co_change"] = {
            "status": "unavailable",
            "scope": "git_history",
            "reason": "no git metadata is available for the target",
        }
        result_data["primary_owner"] = None
        result_data["owner_pct"] = None
        result_data["trend"] = "unknown"
        result_data["risk_type"] = "high-coupling" if dep_count >= 5 else "unknown"
        result_data["test_gap"] = await _check_test_gap(session, repo_id, lookup_path)
        result_data["security_signals"] = await _get_security_signals(session, repo_id, lookup_path)
        result_data["risk_summary"] = f"{target} — no git metadata available"
        return result_data

    hotspot_score = meta.churn_percentile or 0.0

    co_change_population, co_changes_total = _build_co_changes(
        meta, import_links.get(lookup_path, {}), exclude_spec
    )
    co_changes = co_change_population[:_RELATIONSHIP_LIMIT]

    owner = meta.primary_owner_name or "unknown"
    pct = meta.primary_owner_commit_pct or 0.0

    # --- Risk velocity (trend) ---
    trend = _compute_trend(meta)

    # --- Risk type classification ---
    risk_type = _classify_risk_type(meta, dep_count, team_size)

    # Phase 2: commit classification → change_pattern
    change_pattern = _derive_change_pattern(_load_commit_categories(meta))

    # Phase 2: recent owner & bus factor
    bus_factor = getattr(meta, "bus_factor", 0) or 0

    result_data["hotspot_score"] = hotspot_score
    # Emit the backend's hotspot classification, not a client re-derivation.
    # ``is_hotspot`` lives on the stored GitMetadata row and already applies
    # the absolute activity floors (issue #361); a client that thresholds the
    # score alone can reproduce the churn half but not the floors, so on a
    # quiet repo it would badge files the backend does not consider hotspots.
    result_data["is_hotspot"] = bool(getattr(meta, "is_hotspot", False))
    result_data["co_change_partners"] = co_changes
    result_data["co_change_partners_total"] = co_changes_total
    result_data["co_change_partners_emitted"] = len(co_changes)
    result_data["co_change_partners_truncated"] = len(co_changes) < co_changes_total
    cap_collection(
        result_data,
        "co_change_partners",
        co_change_population,
        _RELATIONSHIP_LIMIT,
        collector,
        label=f"{target} :: co_change_partners beyond cap={_RELATIONSHIP_LIMIT}",
        preserve_counts=True,
    )
    result_data["relationship_analysis"]["co_change"] = {
        "status": "available",
        "scope": "git_history",
        "evidence_kind": "historical",
    }
    result_data["primary_owner"] = owner
    result_data["owner_pct"] = pct
    result_data["recent_owner"] = getattr(meta, "recent_owner_name", None)
    result_data["recent_owner_pct"] = getattr(meta, "recent_owner_commit_pct", None)
    result_data["bus_factor"] = bus_factor
    result_data["contributor_count"] = getattr(meta, "contributor_count", 0) or 0
    result_data["trend"] = trend
    result_data["risk_type"] = risk_type
    result_data["change_pattern"] = change_pattern
    result_data["change_magnitude"] = {
        "lines_added_90d": getattr(meta, "lines_added_90d", 0) or 0,
        "lines_deleted_90d": getattr(meta, "lines_deleted_90d", 0) or 0,
        "avg_commit_size": round(getattr(meta, "avg_commit_size", 0.0) or 0.0, 1),
    }
    # Phase 3: rename tracking & merge commit proxy
    original_path = getattr(meta, "original_path", None)
    if original_path:
        result_data["original_path"] = original_path
    merge_commit_count = getattr(meta, "merge_commit_count_90d", 0) or 0
    if merge_commit_count > 0:
        result_data["merge_commit_count_90d"] = merge_commit_count

    defect_profile = _defect_profile(meta)
    if defect_profile is not None:
        result_data["defect_profile"] = defect_profile

    # C. Test gaps + security signals
    result_data["test_gap"] = await _check_test_gap(session, repo_id, lookup_path)
    result_data["security_signals"] = await _get_security_signals(session, repo_id, lookup_path)

    capped = getattr(meta, "commit_count_capped", False)
    capped_note = " (history truncated — actual count may be higher)" if capped else ""
    result_data["commit_count_capped"] = capped

    bus_note = ""
    if bus_factor == 1 and (meta.commit_count_total or 0) > 20:
        bus_note = f", bus factor risk (sole maintainer: {owner})"

    # Lead with the bug-fix history when there is any. The summary used to open
    # on a churn percentile even where risk_type said "bug-prone", so the first
    # thing an agent read disagreed with the classification beside it. Counted
    # fixes are the better grounded defect signal, so they go first and churn
    # keeps its place as the next clause.
    result_data["risk_summary"] = (
        f"{target} — {_fix_clause(defect_profile)}"
        f"hotspot score {hotspot_score:.0%} ({trend}), "
        f"{dep_count} direct dependents, {risk_type}, {change_pattern}, "
        f"{co_changes_total} co-change partners, owned {pct:.0%} by {owner}"
        f"{bus_note}{capped_note}"
    )

    return result_data
