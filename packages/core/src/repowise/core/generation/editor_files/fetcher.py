"""EditorFileDataFetcher: queries the repowise DB and returns EditorFileData.

All queries operate on already-persisted data — no LLM calls required.
Uses the existing CRUD layer where possible; raw selects for aggregate queries.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.perf.coverage import coverage_for_metrics
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    GraphNode,
    HealthFileMetric,
    HealthFinding,
    Page,
)

from .data import (
    CodeHealthBlock,
    DecisionSummary,
    EditorFileData,
    HotspotFile,
    KGLayerSummary,
    KGTourStepSummary,
    KeyModule,
)
from .tech_stack import detect_build_commands, detect_tech_stack

# Maximum items per section to keep CLAUDE.md within ~200 lines
_MAX_MODULES = 10
_MAX_ENTRY_POINTS = 10
_MAX_HOTSPOTS = 5
_MAX_DECISIONS = 8


class EditorFileDataFetcher:
    """Fetches all data needed to render an editor-file template."""

    def __init__(
        self,
        session: AsyncSession,
        repo_id: str,
        repo_path: Path,
    ) -> None:
        self._session = session
        self._repo_id = repo_id
        self._repo_path = repo_path

    async def fetch(self) -> EditorFileData:
        """Run all queries and return a populated EditorFileData."""
        repo = await crud.get_repository(self._session, self._repo_id)
        repo_name = repo.name if repo else self._repo_path.name

        kg_layers, kg_tour = await self._get_kg_data()

        return EditorFileData(
            repo_name=repo_name,
            indexed_at=datetime.now(UTC).strftime("%Y-%m-%d"),
            indexed_commit=_get_head_short_sha(self._repo_path),
            architecture_summary=await self._get_architecture_summary(),
            key_modules=await self._get_key_modules(),
            entry_points=await self._get_entry_points(),
            tech_stack=detect_tech_stack(self._repo_path),
            hotspots=await self._get_hotspots(),
            decisions=await self._get_decisions(),
            build_commands=detect_build_commands(self._repo_path),
            avg_confidence=await self._get_avg_confidence(),
            code_health=await self._get_code_health(),
            kg_layers=kg_layers,
            kg_tour=kg_tour,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_architecture_summary(self) -> str:
        """Extract 2-4 sentences from the repo_overview wiki page."""
        pages = await crud.list_pages(
            self._session,
            self._repo_id,
            page_type="repo_overview",
            limit=1,
        )
        if not pages:
            return ""
        content = pages[0].content or ""
        return _extract_sentences(content, max_sentences=4)

    async def _get_key_modules(self) -> list[KeyModule]:
        """Top modules by PageRank with owner from git metadata."""
        # Fetch module pages sorted by their target_path (proxy for PageRank
        # ordering is applied by joining with graph_nodes).
        pages_result = await self._session.execute(
            select(Page, GraphNode.pagerank, GraphNode.symbol_count)
            .join(
                GraphNode,
                (GraphNode.repository_id == Page.repository_id)
                & (GraphNode.node_id == Page.target_path),
                isouter=True,
            )
            .where(
                Page.repository_id == self._repo_id,
                Page.page_type == "module_page",
            )
            .order_by(GraphNode.pagerank.desc().nulls_last())
            .limit(_MAX_MODULES)
        )
        rows = pages_result.all()

        if not rows:
            return []

        # Build a lookup of primary owners from git_metadata
        target_paths = [row[0].target_path for row in rows]
        owner_map = await self._get_owners_for_paths(target_paths)

        modules: list[KeyModule] = []
        for page, _pagerank, symbol_count in rows:
            purpose = _extract_sentences(page.content or "", max_sentences=1)
            purpose = _truncate_at_word(purpose, 80).rstrip(".") if purpose else ""
            modules.append(
                KeyModule(
                    name=page.target_path,
                    purpose=purpose,
                    file_count=symbol_count or 0,
                    owner=owner_map.get(page.target_path),
                )
            )
        return modules

    async def _get_entry_points(self) -> list[str]:
        """Curated orientation entry points (re-export barrels and sinks demoted).

        Prefers the curated ``kg_project_meta`` list. The raw ``is_entry_point``
        flag also tags every package-export file (cn.ts, types/*.ts) — high
        fan-in sinks that are the opposite of where a reader starts, and ordering
        them by PageRank floats those sinks to the top. Falls back to the
        flag (PageRank-ordered) only for indexes built before the curation pass.
        """
        meta = await crud.get_kg_project_meta(self._session, self._repo_id)
        if meta is not None:
            try:
                curated = json.loads(meta.entry_points_json or "[]")
            except (json.JSONDecodeError, TypeError):
                curated = []
            if curated:
                return curated[:_MAX_ENTRY_POINTS]

        result = await self._session.execute(
            select(GraphNode.node_id)
            .where(
                GraphNode.repository_id == self._repo_id,
                GraphNode.is_entry_point == True,  # noqa: E712
            )
            .order_by(GraphNode.pagerank.desc())
            .limit(_MAX_ENTRY_POINTS)
        )
        return [row[0] for row in result.all()]

    async def _get_hotspots(self) -> list[HotspotFile]:
        """Top hotspot files by churn_percentile with owner info."""
        result = await self._session.execute(
            select(
                GitMetadata.file_path,
                GitMetadata.churn_percentile,
                GitMetadata.commit_count_90d,
                GitMetadata.primary_owner_name,
            )
            .where(
                GitMetadata.repository_id == self._repo_id,
                GitMetadata.is_hotspot == True,  # noqa: E712
            )
            .order_by(
                GitMetadata.churn_percentile.desc(),
                GitMetadata.file_path.asc(),  # deterministic tie-break
            )
            .limit(_MAX_HOTSPOTS)
        )
        return [
            HotspotFile(
                path=row[0],
                churn_percentile=round(row[1] * 100, 1),  # stored as 0.0-1.0
                commit_count_90d=row[2],
                owner=row[3],
            )
            for row in result.all()
        ]

    async def _get_decisions(self) -> list[DecisionSummary]:
        """Active decision records, least-stale first."""
        result = await self._session.execute(
            select(DecisionRecord)
            .where(
                DecisionRecord.repository_id == self._repo_id,
                DecisionRecord.status == "active",
            )
            .order_by(DecisionRecord.staleness_score.asc())
            .limit(_MAX_DECISIONS)
        )
        records = list(result.scalars().all())
        summaries: list[DecisionSummary] = []
        for rec in records:
            rationale = (rec.rationale or "").strip()
            rationale = rationale[:100].rstrip(".,;") if rationale else ""
            decision_text = (rec.decision or "").strip()
            decision_text = decision_text[:120].rstrip(".,;") if decision_text else ""
            summaries.append(
                DecisionSummary(
                    title=rec.title,
                    status=rec.status,
                    rationale=rationale,
                    decision=decision_text,
                )
            )
        return summaries

    async def _get_avg_confidence(self) -> float:
        """Average confidence score across all wiki pages for this repo."""
        result = await self._session.execute(
            select(func.avg(Page.confidence)).where(
                Page.repository_id == self._repo_id,
            )
        )
        avg = result.scalar_one_or_none()
        return round(float(avg), 2) if avg is not None else 0.0

    async def _get_code_health(self) -> CodeHealthBlock | None:
        """Build the compact code-health block for CLAUDE.md.

        Filters per plan §9: critical biomarkers in hotspot files, plus
        any Brain Method finding. Empty list when no health data yet.
        """
        metric_rows = list(
            (
                await self._session.execute(
                    select(HealthFileMetric).where(
                        HealthFileMetric.repository_id == self._repo_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not metric_rows:
            return None

        # KPIs.
        total_nloc = sum(max(m.nloc, 1) for m in metric_rows)
        avg = (
            sum(m.score * max(m.nloc, 1) for m in metric_rows) / total_nloc
            if total_nloc
            else sum(m.score for m in metric_rows) / len(metric_rows)
        )
        worst = min(metric_rows, key=lambda m: m.score)

        # Hotspot-flagged paths.
        hotspot_paths_res = await self._session.execute(
            select(GitMetadata.file_path).where(
                GitMetadata.repository_id == self._repo_id,
                GitMetadata.is_hotspot == True,  # noqa: E712
            )
        )
        hotspot_paths = {row[0] for row in hotspot_paths_res.all()}

        hotspot_metrics = [m for m in metric_rows if m.file_path in hotspot_paths]
        if hotspot_metrics:
            h_nloc = sum(max(m.nloc, 1) for m in hotspot_metrics)
            hotspot_health = (
                sum(m.score * max(m.nloc, 1) for m in hotspot_metrics) / h_nloc if h_nloc else avg
            )
        else:
            hotspot_health = avg

        # Maintainability pillar headline: NLOC-weighted over the per-file
        # maintainability scores, skipping rows that predate the split. ``None``
        # when unmeasured so the section omits the line rather than printing 10.0.
        maint_rows = [
            m for m in metric_rows if getattr(m, "maintainability_score", None) is not None
        ]
        maintainability_average: float | None = None
        if maint_rows:
            m_nloc = sum(max(m.nloc, 1) for m in maint_rows)
            maintainability_average = (
                sum(m.maintainability_score * max(m.nloc, 1) for m in maint_rows) / m_nloc
                if m_nloc
                else sum(m.maintainability_score for m in maint_rows) / len(maint_rows)
            )

        # Performance pillar headline: same NLOC-weighted average over the
        # per-file performance scores (static performance RISK). ``None`` when
        # unmeasured so the section omits the line rather than printing 10.0.
        perf_rows = [m for m in metric_rows if getattr(m, "performance_score", None) is not None]
        performance_average: float | None = None
        if perf_rows:
            p_nloc = sum(max(m.nloc, 1) for m in perf_rows)
            performance_average = (
                sum(m.performance_score * max(m.nloc, 1) for m in perf_rows) / p_nloc
                if p_nloc
                else sum(m.performance_score for m in perf_rows) / len(perf_rows)
            )

        # Honest performance headline: how much of the analyzed code a perf
        # detector actually ran on. Restricted to real code (LANGUAGE_MAPS), so a
        # mostly-C++/Kotlin repo reads a low coverage %, never a bare 10/10.
        lang_by_path = await crud.get_file_language_map(self._session, self._repo_id)
        perf_coverage = coverage_for_metrics(metric_rows, lang_by_path)

        # Critical biomarkers: brain methods, or critical-severity findings
        # in hotspot files. Cap at 5 to keep CLAUDE.md tight.
        f_res = await self._session.execute(
            select(HealthFinding)
            .where(
                HealthFinding.repository_id == self._repo_id,
                HealthFinding.status == "open",
            )
            .order_by(HealthFinding.health_impact.desc())
        )
        all_findings = list(f_res.scalars().all())

        # Open performance-finding count + density over covered LOC (the honest
        # headline the diluted /10 hides).
        performance_findings = sum(
            1 for f in all_findings if (f.dimension or "defect") == "performance"
        )
        performance_findings_density: float | None = None
        if perf_coverage.covered_nloc > 0:
            performance_findings_density = round(
                10000.0 * performance_findings / perf_coverage.covered_nloc, 2
            )

        critical = []
        for f in all_findings:
            if len(critical) >= 5:
                break
            if f.biomarker_type == "brain_method" or (
                f.severity == "critical" and f.file_path in hotspot_paths
            ):
                critical.append(
                    {
                        "path": f.file_path,
                        "summary": (
                            f"{f.biomarker_type.replace('_', ' ')}"
                            + (f" ({f.function_name})" if f.function_name else "")
                            + f" — impact −{f.health_impact:.1f}"
                        ),
                    }
                )

        return CodeHealthBlock(
            hotspot_health=round(hotspot_health, 2),
            average_health=round(avg, 2),
            worst_score=round(worst.score, 2),
            worst_path=worst.file_path,
            hotspot_trend="stable",
            maintainability_average=(
                round(maintainability_average, 2) if maintainability_average is not None else None
            ),
            performance_average=(
                round(performance_average, 2) if performance_average is not None else None
            ),
            performance_findings=performance_findings,
            performance_findings_density=performance_findings_density,
            performance_coverage_pct=(
                perf_coverage.pct_loc if perf_coverage.analyzed_files else None
            ),
            performance_skipped_files=perf_coverage.skipped_files,
            performance_unsupported_languages=perf_coverage.unsupported_languages,
            critical_biomarkers=critical,
            untested_hotspots=[],  # Phase 2 fills this from coverage data
        )

    async def _get_kg_data(
        self,
    ) -> tuple[list[KGLayerSummary], list[KGTourStepSummary]]:
        """Fetch KG layers and tour steps from the DB."""
        import json

        db_layers = await crud.get_kg_layers(self._session, self._repo_id)

        layers = [
            KGLayerSummary(
                name=l.name,
                file_count=len(json.loads(l.node_ids_json) if l.node_ids_json else []),
                description=_truncate_at_word(l.description or "", 80),
            )
            for l in db_layers
        ]

        # Guided tour: the overview page's metadata_json carries the
        # authoritative tour (order/title/target_path) — the same source
        # the MCP get_overview tool renders. The KG tour-step rows often
        # persist empty node_ids_json, which used to leave every CLAUDE.md
        # tour step path-less ("3. **index.ts**" with no way to tell which
        # of the five index.ts re-export hubs it meant).
        tour: list[KGTourStepSummary] = []
        pages = await crud.list_pages(
            self._session,
            self._repo_id,
            page_type="repo_overview",
            limit=1,
        )
        if pages:
            try:
                ov_meta = json.loads(pages[0].metadata_json or "{}")
            except (json.JSONDecodeError, TypeError):
                ov_meta = {}
            for s in ov_meta.get("guided_tour") or []:
                tour.append(
                    KGTourStepSummary(
                        order=s.get("order") or len(tour) + 1,
                        title=s.get("title") or "",
                        primary_file=s.get("target_path") or "",
                        reason=s.get("reason") or "",
                    )
                )
        if not tour:
            db_tour = await crud.get_kg_tour_steps(self._session, self._repo_id)
            for s in db_tour:
                node_ids = json.loads(s.node_ids_json) if s.node_ids_json else []
                primary = node_ids[0].removeprefix("file:") if node_ids else ""
                tour.append(
                    KGTourStepSummary(
                        order=s.step_order,
                        title=s.title,
                        primary_file=primary,
                    )
                )

        return layers, tour

    async def _get_owners_for_paths(self, paths: list[str]) -> dict[str, str]:
        """Return {path: primary_owner_name} for the given paths."""
        if not paths:
            return {}
        result = await self._session.execute(
            select(GitMetadata.file_path, GitMetadata.primary_owner_name).where(
                GitMetadata.repository_id == self._repo_id,
                GitMetadata.file_path.in_(paths),
                GitMetadata.primary_owner_name.isnot(None),
            )
        )
        return {row[0]: row[1] for row in result.all()}


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------


def _get_head_short_sha(repo_path: Path) -> str:
    """Return the short SHA of HEAD, or empty string if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _extract_sentences(text: str, max_sentences: int) -> str:
    """Return up to *max_sentences* prose sentences from the start of *text*.

    Strips markdown headers/code fences AND list/table lines so only prose
    remains. List items rarely end with sentence punctuation, so keeping
    them used to jam bullets onto the tail of the last real sentence
    ("...rendered in a web UI. - **Languages**") and leave orphaned
    fragments like "1. **Inputs**" in the rendered CLAUDE.md.
    """
    # Remove markdown headers and code fences
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Drop list items, table rows, and blockquotes — prose only.
    # Both "1." and "1)" enumeration styles count as list items.
    text = re.sub(r"^\s*(?:[-*+]\s|\d+[.)]\s|\||>).*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`]+)`", r"\1", text)  # strip backticks, keep text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links → text
    text = re.sub(r"\n{2,}", "\n", text).strip()

    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]
    return " ".join(sentences[:max_sentences])


def _truncate_at_word(text: str, limit: int) -> str:
    """Truncate *text* to at most *limit* chars without splitting a word.

    Hard ``[:80]`` slices cut mid-word ("classificat", "repowi") which reads
    as a rendering bug in every generated table. Cutting at the last word
    boundary and appending an ellipsis keeps the same budget honestly.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{cut}…"
