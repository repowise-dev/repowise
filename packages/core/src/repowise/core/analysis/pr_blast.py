"""PR blast radius analyzer.

Given a set of changed files, computes:
  - Direct risk per file (hotspot * centrality)
  - Transitive affected files (graph ancestors up to max_depth)
  - Co-change warnings (historical co-change partners NOT in the PR)
  - Recommended reviewers (top owners of affected files)
  - Test gaps (affected files without a corresponding test file)
  - Overall risk score (0-10)

Reuses existing data: graph_nodes/graph_edges (SQL), git_metadata, and the
co_change_partners_json field stored in git_metadata rows.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GitMetadata, GraphNode


class PRBlastRadiusAnalyzer:
    """Compute blast radius for a proposed PR given its changed files."""

    def __init__(self, session: AsyncSession, repo_id: str) -> None:
        self._session = session
        self._repo_id = repo_id

    async def analyze_files(
        self,
        changed_files: list[str],
        max_depth: int = 3,
    ) -> dict:
        """Return full blast-radius analysis for the given changed files.

        Parameters
        ----------
        changed_files:
            Relative file paths that are modified in the PR.
        max_depth:
            Maximum BFS depth for transitive ancestor lookup.
        """
        changed_set = set(changed_files)

        # 1. Per-file direct risk
        direct_risks = await self._score_files(changed_files)

        # 2. Transitive affected files
        transitive_affected = await self._transitive_affected(changed_files, max_depth)
        all_affected_paths = list(changed_set | {e["path"] for e in transitive_affected})

        # 3. Co-change warnings
        cochange_warnings = await self._cochange_warnings(changed_files, changed_set)

        # 4. Recommended reviewers (over all affected files)
        recommended_reviewers = await self._recommend_reviewers(all_affected_paths)

        # 5. Test gaps
        test_gaps = await self._find_test_gaps(all_affected_paths)

        # 6. Overall risk score (0-10)
        overall_risk_score = self._compute_overall_risk(direct_risks, transitive_affected)

        return {
            "direct_risks": direct_risks,
            "transitive_affected": transitive_affected,
            "cochange_warnings": cochange_warnings,
            "recommended_reviewers": recommended_reviewers,
            "test_gaps": test_gaps,
            "overall_risk_score": overall_risk_score,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _score_files(self, paths: list[str]) -> list[dict]:
        """Return direct risk records for each changed file."""
        if not paths:
            return []

        # Fetch git_metadata for all paths in one query
        res = await self._session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == self._repo_id,
                GitMetadata.file_path.in_(paths),
            )
        )
        meta_by_path: dict[str, Any] = {m.file_path: m for m in res.scalars().all()}

        # Fetch graph node pagerank (used as centrality proxy)
        node_res = await self._session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == self._repo_id,
                GraphNode.node_id.in_(paths),
            )
        )
        node_by_path: dict[str, Any] = {n.node_id: n for n in node_res.scalars().all()}

        results = []
        for path in paths:
            meta = meta_by_path.get(path)
            node = node_by_path.get(path)
            temporal = float(getattr(meta, "temporal_hotspot_score", 0.0) or 0.0)
            centrality = float(getattr(node, "pagerank", 0.0) or 0.0)
            risk_score = self._score_file(temporal, centrality)
            results.append(
                {
                    "path": path,
                    "risk_score": round(risk_score, 4),
                    "temporal_hotspot": round(temporal, 4),
                    "centrality": round(centrality, 6),
                }
            )

        results.sort(key=lambda x: -x["risk_score"])
        return results

    @staticmethod
    def _score_file(temporal_hotspot_score: float, centrality: float) -> float:
        """Compute file-level risk: centrality * (1 + temporal_hotspot_score)."""
        return centrality * (1.0 + temporal_hotspot_score)

    async def _transitive_affected(
        self, changed_files: list[str], max_depth: int
    ) -> list[dict]:
        """BFS over reverse graph edges (source_node_id -> target_node_id direction).

        We want files that *import* the changed files (i.e. are affected when a
        changed file changes).  In graph_edges, an edge means
        ``source imports target``, so we look for rows where
        ``target_node_id IN (frontier)`` and collect the ``source_node_id``
        values — those are the files that depend on our changed set.
        """
        visited: dict[str, int] = {}  # path -> depth at which it was first reached
        frontier = list(set(changed_files))

        for depth in range(1, max_depth + 1):
            if not frontier:
                break
            # SQLite / SQLAlchemy compatible IN query via text()
            placeholders = ",".join(f":p{i}" for i in range(len(frontier)))
            params: dict[str, Any] = {"repo_id": self._repo_id}
            params.update({f"p{i}": v for i, v in enumerate(frontier)})
            rows = await self._session.execute(
                text(
                    f"SELECT DISTINCT source_node_id FROM graph_edges "
                    f"WHERE repository_id = :repo_id "
                    f"AND target_node_id IN ({placeholders})"
                ),
                params,
            )
            next_frontier = []
            for (src,) in rows:
                if src not in visited and src not in set(changed_files):
                    visited[src] = depth
                    next_frontier.append(src)
            frontier = next_frontier

        return [{"path": p, "depth": d} for p, d in sorted(visited.items(), key=lambda x: x[1])]

    async def _cochange_warnings(
        self, changed_files: list[str], changed_set: set[str]
    ) -> list[dict]:
        """Return co-change partners of changed files that are NOT in the PR."""
        if not changed_files:
            return []

        res = await self._session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == self._repo_id,
                GitMetadata.file_path.in_(changed_files),
            )
        )

        warnings = []
        for meta in res.scalars().all():
            partners = json.loads(meta.co_change_partners_json or "[]")
            for partner in partners:
                partner_path = partner.get("file_path") or partner.get("path") or ""
                score = float(partner.get("co_change_count") or partner.get("count") or 0)
                if partner_path and partner_path not in changed_set:
                    warnings.append(
                        {
                            "changed": meta.file_path,
                            "missing_partner": partner_path,
                            "score": score,
                        }
                    )

        warnings.sort(key=lambda x: -x["score"])
        return warnings

    async def _recommend_reviewers(self, affected_files: list[str]) -> list[dict]:
        """Aggregate top owners of affected files; return top 5."""
        if not affected_files:
            return []

        res = await self._session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == self._repo_id,
                GitMetadata.file_path.in_(affected_files),
            )
        )

        owner_files: dict[str, list[float]] = defaultdict(list)
        for meta in res.scalars().all():
            email = meta.primary_owner_email or ""
            pct = float(meta.primary_owner_commit_pct or 0.0)
            if email:
                owner_files[email].append(pct)

        reviewers = [
            {
                "email": email,
                "files": len(pcts),
                "ownership_pct": round(sum(pcts) / len(pcts), 3) if pcts else 0.0,
            }
            for email, pcts in owner_files.items()
        ]
        reviewers.sort(key=lambda x: (-x["files"], -x["ownership_pct"]))
        return reviewers[:5]

    async def _find_test_gaps(self, affected_files: list[str]) -> list[str]:
        """Return files that lack a test, coverage-backed where the map has data.

        A file with >=1 per-test coverage row (from ``repowise coverage add``) is
        coverage-*proven* to be exercised by some test, so it is never a gap -
        this supersedes the filename guess for files the map can speak to. Where
        the per-test map has no data for a file, fall back to the filename
        pattern (test_<name>, <name>_test, <name>.spec.*) - an honest "unknown",
        never asserted as untested. Test files themselves are excluded; they
        don't need their own tests.
        """
        if not affected_files:
            return []

        from repowise.core.persistence.crud import covered_source_files

        # Coverage-proven-tested files: absent from gaps regardless of naming.
        covered = await covered_source_files(self._session, self._repo_id, set(affected_files))

        node_res = await self._session.execute(
            select(GraphNode.node_id, GraphNode.is_test).where(
                GraphNode.repository_id == self._repo_id,
                GraphNode.node_id.in_(affected_files),
            )
        )
        # Build a set of affected files that are themselves test files
        test_file_set = {row[0] for row in node_res.all() if row[1]}

        # Fetch all test paths for the filename fallback (map-no-data files only)
        all_test_res = await self._session.execute(
            select(GraphNode.node_id).where(
                GraphNode.repository_id == self._repo_id,
                GraphNode.is_test == True,  # noqa: E712
            )
        )
        test_paths = {row[0] for row in all_test_res.all()}

        gaps = []
        for path in affected_files:
            # Skip test files — they don't need their own tests
            if path in test_file_set:
                continue
            # Coverage proves a test exercises this file: not a gap.
            if path in covered:
                continue
            base = os.path.splitext(os.path.basename(path))[0]
            ext = os.path.splitext(path)[1].lstrip(".")
            has_test = any(
                (
                    f"test_{base}" in tp
                    or f"{base}_test" in tp
                    or f"{base}.spec.{ext}" in tp
                    or f"{base}.spec." in tp
                )
                for tp in test_paths
            )
            if not has_test:
                gaps.append(path)

        return gaps

    @staticmethod
    def _compute_overall_risk(
        direct_risks: list[dict],
        transitive_affected: list[dict],
    ) -> float:
        """Compute overall risk score on 0-10 scale.

        Per-file risk is ``pagerank * (1 + temporal_hotspot)`` — unbounded
        and pagerank-scaled (typically 0-0.3). The old ``min(raw * 100, 10)``
        normalisation clipped *everything*: the 0-1 breadth bonus alone
        scaled to 0-20 points, so any PR with >=20 transitive dependents —
        i.e. any PR touching a hotspot — reported exactly 10.0 and the score
        carried no information.

        Instead, squash the pagerank-scale file term through an exponential
        CDF onto 0-8 points (saturating only asymptotically) and let breadth
        add up to 2 points. Reference points for the file term:
        combined 0.01 -> ~0.8, 0.05 -> ~3.1, 0.1 -> ~5.1, 0.3 -> ~7.6.
        """
        if not direct_risks:
            return 0.0

        avg_direct = sum(r["risk_score"] for r in direct_risks) / len(direct_risks)
        max_direct = max(r["risk_score"] for r in direct_risks)
        breadth_bonus = min(len(transitive_affected) / 20.0, 1.0)  # 0-1

        combined = 0.5 * avg_direct + 0.5 * max_direct
        file_term = 8.0 * (1.0 - math.exp(-10.0 * combined))  # 0-8, asymptotic
        score = min(file_term + 2.0 * breadth_bonus, 10.0)
        return round(score, 2)
