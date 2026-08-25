"""PR blast radius analyzer.

Given a set of changed files, computes:
  - Direct risk per file (hotspot * centrality)
  - Transitive affected files (graph ancestors up to max_depth)
  - Co-change warnings (historical co-change partners NOT in the PR)
  - Recommended reviewers (top owners of affected files)
  - Test gaps (affected files without a corresponding test file)
  - Structural impact heuristic (0-10; uncalibrated, not a probability)

Reuses existing data: graph_nodes/graph_edges (SQL), git_metadata, and the
co_change_partners_json field stored in git_metadata rows.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.risk_semantics import structural_impact_contract
from repowise.core.exclusion import build_exclude_spec, is_excluded
from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.models import GitMetadata, GraphNode, Repository


def rank_tests_by_reach(by_file: Mapping[str, Iterable[str]]) -> list[str]:
    """Test ids ordered by how many changed files each one reaches.

    Callers cap this list and tell the agent to run the head of it first, so a
    flat alphabetical sort hands over the head of the alphabet rather than the
    tests that cover most of the change. Ties keep alphabetical order, which
    leaves a single-file change ordered exactly as before.
    """
    reach = Counter(test_id for tests in by_file.values() for test_id in tests)
    return sorted(reach, key=lambda t: (-reach[t], t))


#: Prefixes that name a module for its slot in a dispatch table rather than for
#: its subject, so its tests are named after the subject alone: ``tool_dead_code``
#: is tested by ``test_dead_code``, not ``test_tool_dead_code``.
_ROLE_PREFIXES = ("tool_", "get_")

#: Below this, a stripped stem is too generic to be evidence of anything.
_MIN_STEM_LENGTH = 3


def test_name_stems(base: str) -> list[tuple[str, bool]]:
    """Stems a test file for ``base`` could be named after, each with whether the
    match has to be exact.

    The full stem keeps the historical substring match, which absorbs suffixes
    like ``test_parser_edge_cases``. A stripped stem is shorter and so collides
    far more easily (``tool_repos`` -> ``repos``, a prefix of ``repository``),
    and clearing a gap on a coincidence is the one error that costs a reader.
    """
    stems = [(base, False)]
    for prefix in _ROLE_PREFIXES:
        stripped = base[len(prefix) :]
        if base.startswith(prefix) and len(stripped) >= _MIN_STEM_LENGTH:
            stems.append((stripped, True))
    return stems


def _names_a_test_for(stem: str, ext: str, test_path: str, exact: bool) -> bool:
    """Whether ``test_path`` follows a naming convention for ``stem``."""
    if exact:
        named = os.path.splitext(os.path.basename(test_path))[0]
        return named in (f"test_{stem}", f"{stem}_test", f"{stem}.spec")
    return (
        f"test_{stem}" in test_path
        or f"{stem}_test" in test_path
        or f"{stem}.spec.{ext}" in test_path
        or f"{stem}.spec." in test_path
    )


class PRBlastRadiusAnalyzer:
    """Compute blast radius for a proposed PR given its changed files."""

    def __init__(
        self,
        session: AsyncSession,
        repo_id: str,
        repository_alias: str | None = None,
    ) -> None:
        self._session = session
        self._repo_id = repo_id
        self._repository_alias = repository_alias

    async def analyze_files(
        self,
        changed_files: list[str],
        max_depth: int = 3,
        exclude_spec: Any = None,
    ) -> dict:
        """Return full blast-radius analysis for the given changed files.

        Parameters
        ----------
        changed_files:
            Relative file paths that are modified in the PR.
        max_depth:
            Maximum BFS depth for transitive ancestor lookup.
        """
        if exclude_spec is None:
            repo_path = (
                await self._session.execute(
                    select(Repository.local_path).where(Repository.id == self._repo_id)
                )
            ).scalar_one_or_none()
            if repo_path:
                exclude_spec = build_exclude_spec(repo_path)
        changed_files = [
            path for path in changed_files if not (exclude_spec and is_excluded(path, exclude_spec))
        ]
        changed_set = set(changed_files)

        # 1. Per-file direct risk
        direct_risks = await self._score_files(changed_files)

        # 2. Transitive affected files
        transitive_affected = await self._transitive_affected(
            changed_files, max_depth, exclude_spec
        )
        # Sorted, because this list is cut before it is shown. ``test_gaps``
        # preserves this order and ``get_risk``'s PR directive renders three of
        # it as ``missing_tests``, the third line an agent is told to read.
        # A bare set union is hash-ordered, so which three appeared varied
        # between processes on identical input. The scale is a PR's own changed
        # files, not the whole affected set — ``directives`` filters to those
        # before cutting — so this is a handful of paths, reported stably.
        all_affected_paths = sorted(changed_set | {e["path"] for e in transitive_affected})

        # 3. Co-change warnings
        cochange_warnings = await self._cochange_warnings(changed_files, changed_set)

        # 4. Recommended reviewers (over all affected files)
        recommended_reviewers = await self._recommend_reviewers(all_affected_paths)

        # 5. Test gaps
        test_gaps = await self._find_test_gaps(all_affected_paths)

        # 6. Canonical test impact. MCP and REST consume this same typed
        #    population; ``guarding_tests`` is its compatibility projection.
        test_impact = await self._test_impact(changed_files, exclude_spec=exclude_spec)
        from repowise.core.analysis.test_impact import legacy_guarding_tests

        guarding_tests = legacy_guarding_tests(test_impact)

        # 7. Structural impact heuristic (0-10). The compatibility field is an
        #    exact alias supplied by the shared public semantics contract.
        structural_impact_score = self._compute_overall_risk(direct_risks, transitive_affected)

        return {
            "direct_risks": direct_risks,
            "transitive_affected": transitive_affected,
            "cochange_warnings": cochange_warnings,
            "recommended_reviewers": recommended_reviewers,
            "test_gaps": test_gaps,
            "test_impact": test_impact,
            "guarding_tests": guarding_tests,
            **structural_impact_contract(structural_impact_score),
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

        results: list[dict[str, Any]] = []
        for path in paths:
            meta = meta_by_path.get(path)
            node = node_by_path.get(path)
            temporal = float(getattr(meta, "temporal_hotspot_score", 0.0) or 0.0)
            centrality = float(getattr(node, "pagerank", 0.0) or 0.0)
            structural_score = self._score_file(temporal, centrality)
            results.append(
                {
                    "path": path,
                    "structural_score": round(structural_score, 4),
                    # Compatibility alias for pre-semantics clients.
                    "risk_score": round(structural_score, 4),
                    "temporal_hotspot": round(temporal, 4),
                    "centrality": round(centrality, 6),
                }
            )

        results.sort(key=lambda x: -float(x["structural_score"]))
        return results

    @staticmethod
    def _score_file(temporal_hotspot_score: float, centrality: float) -> float:
        """Compute file-level risk: centrality * (1 + temporal_hotspot_score)."""
        return centrality * (1.0 + temporal_hotspot_score)

    async def _transitive_affected(
        self, changed_files: list[str], max_depth: int, exclude_spec: Any = None
    ) -> list[dict]:
        """BFS over reverse graph edges (source_node_id -> target_node_id direction).

        We want files that *import* the changed files (i.e. are affected when a
        changed file changes).  In graph_edges, a dependency edge means
        ``source imports target``, so we look for rows where
        ``target_node_id IN (frontier)`` and collect the ``source_node_id``
        values — those are the files that depend on our changed set.

        Not every row is a dependency edge, though, which is what the sentence
        above used to assume. ``co_changes`` rows live in the same table, so an
        unfiltered BFS treated "these two files tend to change together" as
        "this file imports that one" and walked through it, then walked
        through *that* file's co-change partners at the next depth. On this
        repository a PR touching ``core/__init__.py`` reached five co-change
        rows and two real importers at depth 1, and since ``may_break`` is
        capped at five and sorted by depth, the noise crowded out the answer.
        Those partners are already reported, correctly labelled, by
        :meth:`_cochange_warnings`.
        """
        visited: dict[str, int] = {}  # path -> depth at which it was first reached
        # Sorted, not just deduped. This walk's output is cut twice downstream
        # — ``may_break`` takes 15 of it, and that is the first field
        # ``get_risk``'s PR directive tells an agent to read — so the order
        # inside a depth band decides what an agent is shown. A hash-ordered
        # seed plus an unordered ``SELECT DISTINCT`` made that order vary
        # between processes on identical input.
        frontier = sorted(set(changed_files))

        for depth in range(1, max_depth + 1):
            if not frontier:
                break
            # SQLite / SQLAlchemy compatible IN query via text()
            placeholders = ",".join(f":p{i}" for i in range(len(frontier)))
            allowed = ",".join(f":e{i}" for i in range(len(FILE_DEPENDENCY_EDGE_TYPES)))
            params: dict[str, Any] = {"repo_id": self._repo_id}
            params.update({f"p{i}": v for i, v in enumerate(frontier)})
            params.update({f"e{i}": v for i, v in enumerate(sorted(FILE_DEPENDENCY_EDGE_TYPES))})
            rows = await self._session.execute(
                text(
                    f"SELECT DISTINCT e.source_node_id FROM graph_edges e "
                    f"JOIN graph_nodes source ON source.repository_id = e.repository_id "
                    f"AND source.node_id = e.source_node_id AND source.node_type = 'file' "
                    f"JOIN graph_nodes target ON target.repository_id = e.repository_id "
                    f"AND target.node_id = e.target_node_id AND target.node_type = 'file' "
                    f"WHERE e.repository_id = :repo_id "
                    f"AND e.target_node_id IN ({placeholders}) "
                    f"AND e.edge_type IN ({allowed})"
                ),
                params,
            )
            next_frontier = []
            for (src,) in rows:
                if (
                    src not in visited
                    and src not in set(changed_files)
                    and not is_excluded(src, exclude_spec)
                ):
                    visited[src] = depth
                    next_frontier.append(src)
            frontier = next_frontier

        # Depth first, then path: a sort on depth alone is stable, so files
        # sharing a depth kept the row order the query happened to return.
        return [
            {
                "path": p,
                "depth": d,
                "distance": d,
                "direct": d == 1,
                "direction": "dependent_to_dependency",
                "evidence_kind": "structural",
                "claim": "structural_reach",
                "runtime_breakage_claim": False,
            }
            for p, d in sorted(visited.items(), key=lambda item: (item[1], item[0]))
        ]

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
                            "relationship_type": "co_change",
                            "direction": "undirected",
                            "evidence_kind": "historical",
                            "provenance": "git_history",
                            **(
                                {"support": partner["frequency"]}
                                if partner.get("frequency") is not None
                                else {}
                            ),
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

        reviewers: list[dict[str, Any]] = [
            {
                "email": email,
                "files": len(pcts),
                "ownership_pct": round(sum(pcts) / len(pcts), 3) if pcts else 0.0,
            }
            for email, pcts in owner_files.items()
        ]
        reviewers.sort(key=lambda x: (-int(x["files"]), -float(x["ownership_pct"])))
        return reviewers[:5]

    async def _find_test_gaps(self, affected_files: list[str]) -> list[str]:
        """Return the files nothing can be shown to test.

        Three signals, checked in descending order of what they can prove, and a
        file only becomes a gap when all three stay silent. Asserting "nothing
        tests this" is the one claim here that costs a reader something if it is
        wrong, so the bar for making it is no evidence at all.

        1. A per-test coverage row (from ``repowise coverage add``) is
           execution-*proof*: never a gap.
        2. A test file reaching it in the dependency graph is evidence, not
           proof - control reaching a file is not a run exercising it - but it
           is a recorded edge rather than a guess, and it finds the suites that
           name their tests for behaviour instead of for the file under test.
           Used only as this floor; nothing downstream reads a coverage figure
           off it.
        3. Otherwise the filename pattern (test_<name>, <name>_test,
           <name>.spec.*), tried against every stem in ``test_name_stems`` - an
           honest "unknown", never asserted as untested.

        Test files themselves are excluded; they don't need their own tests.
        """
        if not affected_files:
            return []

        from repowise.core.analysis.test_reachability import tests_reaching
        from repowise.core.persistence.crud import covered_source_files

        # Coverage-proven-tested files: absent from gaps regardless of naming.
        covered = await covered_source_files(self._session, self._repo_id, set(affected_files))

        # Graph-reached files: likewise absent. Degrades to "no signal" rather
        # than raising - a failed walk must not turn into a false accusation.
        try:
            reached = set(await tests_reaching(self._session, self._repo_id, affected_files))
        except Exception:
            reached = set()

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
            # The graph records a test reaching it: not a gap either.
            if path in reached:
                continue
            base = os.path.splitext(os.path.basename(path))[0]
            ext = os.path.splitext(path)[1].lstrip(".")
            has_test = any(
                _names_a_test_for(stem, ext, tp, exact)
                for stem, exact in test_name_stems(base)
                for tp in test_paths
            )
            if not has_test:
                gaps.append(path)

        return gaps

    async def _test_impact(self, changed_files: list[str], exclude_spec: Any = None) -> dict:
        """Canonical measured + inferred test-impact population."""
        from repowise.core.analysis.test_impact import analyze_test_impact

        return await analyze_test_impact(
            self._session,
            self._repo_id,
            changed_files,
            repository_alias=self._repository_alias,
            exclude_spec=exclude_spec,
        )

    async def _guarding_tests(self, changed_files: list[str]) -> dict:
        """Compatibility projection of :meth:`_test_impact`.

        ``basis`` retains its historical measured/inferred/none domain and
        measured-first fallback. The canonical ``test_impact`` block contains
        the additive union and per-recommendation truth.
        """
        from repowise.core.analysis.test_impact import legacy_guarding_tests

        return legacy_guarding_tests(await self._test_impact(changed_files))

    async def _inferred_guarding_tests(self, changed_files: list[str], empty: dict) -> dict:
        """Compatibility wrapper returning only structurally inferred rows."""
        impact = await self._test_impact(changed_files)
        inferred = [row for row in impact["recommendations"] if row["basis"] == "inferred"]
        by_file = {
            item["source_file"]: item["inferred_tests"]
            for item in impact["files"]
            if item["inferred_tests"]
        }
        return {
            "map_present": empty.get("map_present", impact["coverage"]["map_present"]),
            "basis": "inferred" if inferred else "none",
            "tests_to_run": [row["test_id"] for row in inferred],
            "tests_to_run_with_basis": inferred,
            "tests_to_run_total": len(inferred),
            "tests_to_run_emitted": len(inferred),
            "tests_to_run_truncated": False,
            "by_file": by_file,
            "analysis": impact["analysis"],
            "coverage": impact["coverage"],
            "inference": impact["inference"],
        }

    @staticmethod
    def _compute_overall_risk(
        direct_risks: list[dict],
        transitive_affected: list[dict],
    ) -> float:
        """Compute the uncalibrated structural-impact heuristic on 0-10.

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

        avg_direct = sum(r["structural_score"] for r in direct_risks) / len(direct_risks)
        max_direct = max(r["structural_score"] for r in direct_risks)
        breadth_bonus = min(len(transitive_affected) / 20.0, 1.0)  # 0-1

        combined = 0.5 * avg_direct + 0.5 * max_direct
        file_term = 8.0 * (1.0 - math.exp(-10.0 * combined))  # 0-8, asymptotic
        score = min(file_term + 2.0 * breadth_bonus, 10.0)
        return round(score, 2)
