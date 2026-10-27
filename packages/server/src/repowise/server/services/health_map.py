"""The bounded field the Code Health map draws, selected deterministically.

A map is a cap, and a cap is a claim about what is not drawn. Ordering the
whole repository by lines of code and taking the top N is a defensible sample
for the health lens and the wrong one for the performance lens: on this
repository it hides tens of files that carry open opportunities, because a
small file can hold the worst cause in the tree.

So the feed admits files in a fixed order - the caller's pinned paths first,
then a small floor per module so a little module never vanishes behind a big
one, then everything else by origin tier (production, tooling and build, test,
docs examples), and inside a tier the files carrying a performance cause in
the default queue, in rank order, before a health-debt rank that puts unhealthy, large
files ahead of clean ones. Vendored and generated files are held out unless the
caller asks for them, because they are not the reader's to change. The response
states exactly what the cap and the hold pushed out. One response carries both
the rendered set and the counts describing it, so a caption can never disagree
with the field beside it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.areas import in_area
from repowise.core.analysis.finding_registry import HELD_ORIGINS, stored_origin
from repowise.core.analysis.health.counts import DEFAULT_COUNTS
from repowise.core.analysis.health.counts import project as project_counts
from repowise.core.analysis.health.perf.coverage import supported_perf_languages
from repowise.core.analysis.health.perf.opportunity_rank import ACTIONABILITY_ORDER
from repowise.core.analysis.health.scope import DEFAULT_SCOPE, parse_scope
from repowise.core.persistence import crud

__all__ = [
    "DEFAULT_MAP_CAP",
    "MAX_MAP_CAP",
    "HealthMapFeed",
    "HealthMapService",
]

DEFAULT_MAP_CAP = 2000
"""Nodes drawn by default. Above this the field stops separating anything."""

MAX_MAP_CAP = 8000
"""Ceiling a caller may raise the cap to. The client draws on a canvas, which
holds this many nodes at frame rate; past it the field is a texture."""

_MAX_ACTIVE = 50
"""Guaranteed paths one request may pin. A deep link names one or a few."""

_MODULE_ROLLUP_CAP = 200
"""Modules described in the textual alternative, worst performance first."""

_MODULE_FLOOR = 2
"""Files each module is guaranteed before the global ranking spends the cap."""

_MODULE_FLOOR_SHARE = 0.2
"""Most of the cap the per-module floor may take, so the ranking still leads."""

_BASIS = "active_then_module_floor_then_origin_tier_then_performance_then_debt"

#: Where a file's code comes from, as an admission tier. Lower draws first. A
#: row written before ``code_origin`` existed falls back on ``is_test``.
_ORIGIN_TIER: dict[str, int] = {
    "production": 0,
    "tooling": 1,
    "build": 1,
    "test": 2,
    "docs_example": 3,
    "vendored": 4,
    "generated": 4,
}


@dataclass
class HealthMapFeed:
    """One bounded field plus the exact scope of what it leaves out."""

    files: list[dict[str, Any]]
    cap: int
    shown: int
    eligible_total: int
    repository_total: int
    selection: dict[str, Any]
    omitted: dict[str, int]
    recovery: dict[str, Any]
    modules: list[dict[str, Any]] = field(default_factory=list)
    performance: dict[str, Any] | None = None
    #: Which half of the repository the field describes.
    scope: str = DEFAULT_SCOPE
    counts: str = DEFAULT_COUNTS
    #: The directory the field is narrowed to, ``None`` for the whole repository.
    area: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "counts": self.counts,
            "area": self.area,
            "files": self.files,
            "cap": self.cap,
            "shown": self.shown,
            "eligible_total": self.eligible_total,
            "repository_total": self.repository_total,
            "selection": self.selection,
            "omitted": self.omitted,
            "recovery": self.recovery,
            "modules": self.modules,
            "performance": self.performance,
        }


def parse_active(raw: str | None) -> tuple[str, ...]:
    """Split and bound the caller's guaranteed paths.

    Order is preserved and duplicates collapse, because the first band is
    admitted in the order the caller asked for.
    """
    if not raw:
        return ()
    seen: dict[str, None] = {}
    for part in raw.split(","):
        path = part.strip()
        if path and path not in seen:
            seen[path] = None
        if len(seen) >= _MAX_ACTIVE:
            break
    return tuple(seen)


class HealthMapService:
    """Builds the map feed for one repository."""

    def __init__(self, session: AsyncSession, repository_id: str) -> None:
        self._session = session
        self._repository_id = repository_id

    async def feed(
        self,
        *,
        cap: int = DEFAULT_MAP_CAP,
        active: tuple[str, ...] = (),
        scope: str = DEFAULT_SCOPE,
        counts: str = DEFAULT_COUNTS,
        include_held: bool = False,
        query: str | None = None,
        area: str | None = None,
        held: frozenset[str] | None = None,
    ) -> HealthMapFeed:
        session, repo_id = self._session, self._repository_id
        metrics = await crud.get_health_metrics(session, repo_id)
        rollups = await crud.performance_file_rollups(session, repo_id)
        if area:
            # A drilled area is its own field: every count, the cap and the
            # performance totals below describe the area, never the repository.
            metrics = [m for m in metrics if in_area(m.file_path, area)]
            rollups = [r for r in rollups if in_area(r.file_path, area)]
        if held is not None:
            # The area map's held origins (``hold=map``) leave the field before
            # any count, as they leave the area rollups beside it; the held set
            # then decides vendored and generated too, even when every group
            # was brought back and the set is empty.
            metrics = [m for m in metrics if stored_origin(m.code_origin, m.is_test) not in held]
            include_held = True
        languages = await crud.get_file_language_map(session, repo_id)
        # The same set ``hotspot_health`` averages over, so the hotspot lens
        # and that KPI can never disagree about which files are hotspots.
        hotspots = await crud.get_hotspot_file_paths(session, repo_id)
        summary = await crud.get_performance_summary(session, repo_id)
        perf_languages = supported_perf_languages()

        scope_narrowed = parse_scope(scope) == "production"
        if scope_narrowed:
            metrics = [m for m in metrics if not m.is_test]
            kept = {m.file_path for m in metrics}
            rollups = [r for r in rollups if r.file_path in kept]

        # Re-marks the same field: every node keeps its size and its module,
        # and only the colour moves. A file with no recorded split cannot be
        # coloured on this basis, so it leaves the field rather than sitting
        # there in whatever colour it last had.
        #
        # ``rollups`` is deliberately left whole. The page says performance is
        # scored separately and never blended into health, so its totals must
        # not move when the health reading does; only the per-node join below
        # narrows, and a node that is not drawn simply never looks one up.
        counted = len(metrics)
        metrics, _ = project_counts(counts, metrics)

        by_path = {m.file_path: m for m in metrics}
        # A zero-NLOC file cannot be sized, and the map drops it on arrival.
        # Counting it as eligible would promise a node that never appears.
        drawable = [m for m in metrics if (m.nloc or 0) > 0]
        # Vendored and generated code is held out unless asked for, but a path
        # the caller pinned is drawn whatever it is: a link names its subject.
        pinned = set(active)
        eligible = [
            m
            for m in drawable
            if include_held or m.code_origin not in HELD_ORIGINS or m.file_path in pinned
        ]
        held_out = len(drawable) - len(eligible)
        eligible_paths = {m.file_path for m in eligible}
        burden = {r.file_path: r for r in rollups}

        # A file earns a priority slot with a cause in the default queue, the
        # count the performance headline leads with; the rest wait in the queue view.
        open_rollups = [r for r in rollups if r.queued]
        perf_position = {
            r.file_path: i for i, r in enumerate(open_rollups) if r.file_path in eligible_paths
        }
        # One sort on one key, so every band below is a walk down one list.
        ordered = sorted(eligible, key=lambda m: _admission_key(m, perf_position))

        chosen: list[str] = []
        taken: set[str] = set()

        def admit(path: str) -> bool:
            if path in taken or path not in eligible_paths or len(chosen) >= cap:
                return False
            chosen.append(path)
            taken.add(path)
            return True

        active_shown = [path for path in active if admit(path)]

        # Search pins its matches, so the box can find a file the cap left out.
        needle = (query or "").strip().lower()
        query_matches = 0
        query_shown = 0
        if needle:
            for metric in ordered:
                if needle not in metric.file_path.lower():
                    continue
                query_matches += 1
                if query_shown < _MAX_ACTIVE and admit(metric.file_path):
                    query_shown += 1

        # The floor walks the same ranked list once per guaranteed slot, so
        # every module gets its most pressing file before any gets a second,
        # and the modules holding the most pressing files go first when the
        # budget cannot reach them all.
        floor_budget = int(cap * _MODULE_FLOOR_SHARE)
        floor_before = len(chosen)
        per_module: dict[str, int] = {}
        for slot in range(_MODULE_FLOOR):
            for metric in ordered:
                if len(chosen) - floor_before >= floor_budget:
                    break
                key = metric.module or ""
                if per_module.get(key, 0) == slot and admit(metric.file_path):
                    per_module[key] = slot + 1
        floor_shown = len(chosen) - floor_before

        ranked_before = len(chosen)
        for metric in ordered:
            if len(chosen) >= cap:
                break
            admit(metric.file_path)
        ranked_shown = len(chosen) - ranked_before

        drawn = [by_path[path] for path in chosen]
        files = [
            self._row(metric, burden.get(metric.file_path), languages, perf_languages, hotspots)
            for metric in drawn
        ]

        # Eligible and not taken, so this is what the cap pushed out. A cause
        # on a file with no lines can never be drawn at any cap, and counting
        # it here would promise a recovery that pinning the path cannot give.
        undrawn_perf = [
            r for r in open_rollups if r.file_path in eligible_paths and r.file_path not in taken
        ]
        omitted = {
            "files": len(eligible) - len(chosen),
            "performance_files": len(undrawn_perf),
            "opportunities": sum(r.queued for r in undrawn_perf),
            "observations": sum(r.observations for r in undrawn_perf),
            # Drawable, and left out by the vendored/generated hold, not the cap.
            "held": held_out,
        }
        # What the bands after the pins admitted, split the way older readers
        # count it: the performance band, and everything else.
        banded = chosen[floor_before:]
        performance_shown = sum(1 for p in banded if p in perf_position)

        return HealthMapFeed(
            files=files,
            cap=cap,
            shown=len(chosen),
            eligible_total=len(eligible),
            repository_total=counted,
            selection={
                "basis": _BASIS,
                "active_requested": list(active),
                "active_shown": active_shown,
                "active_missing": [p for p in active if p not in taken],
                "performance_shown": performance_shown,
                "performance_eligible": len(perf_position),
                # Admitted by rank without an open cause. Kept under its old
                # name, which predates the debt rank, for existing readers.
                "nloc_shown": len(banded) - performance_shown,
                "module_floor_shown": floor_shown,
                "ranked_shown": ranked_shown,
                "query": needle or None,
                "query_matches": query_matches,
                "query_shown": query_shown,
                "include_held": include_held,
            },
            omitted=omitted,
            recovery={
                "guarantee_paths": (
                    "Re-request this feed with active=<comma-separated paths> to pin any "
                    "file into the drawn field, or q=<substring> to pin up to "
                    f"{_MAX_ACTIVE} files whose path contains it."
                ),
                "opportunity_queue": (
                    f"/api/repos/{repo_id}/health/performance-opportunities lists open "
                    "opportunities, drawn or not, in production code; pass "
                    "context=all for every execution context."
                ),
                "raise_cap": f"cap accepts up to {MAX_MAP_CAP}.",
                "include_held": "include_held=true draws vendored and generated files too.",
            },
            modules=self._modules(drawn, burden),
            # The stored performance summary is a repo-wide aggregate and there
            # is no narrowed copy of it, so a narrowed field omits the block
            # rather than serving repo-wide totals beside production-only rows.
            performance=(
                None
                if scope_narrowed
                else self._performance_block(rollups, summary, len(perf_position))
            ),
            scope=DEFAULT_SCOPE if not scope_narrowed else "production",
            counts=counts,
            area=area or None,
        )

    def _row(
        self,
        metric: Any,
        rollup: Any,
        languages: dict[str, str | None],
        perf_languages: Any,
        hotspots: set[str],
    ) -> dict[str, Any]:
        """One node. The lens reads the rollup; everything else is geometry.

        The counts are always present, because a missing count is how the lens
        recognizes a server with no read model and it must not confuse that
        with a clear file. The two nullable keys are omitted when empty: most
        of a large field carries no cause, and ``null`` on every row of it is
        payload spent saying nothing.
        """
        row: dict[str, Any] = {
            "file_path": metric.file_path,
            "score": metric.score,
            "nloc": metric.nloc,
            "module": metric.module,
            "line_coverage_pct": metric.line_coverage_pct,
            "has_test_file": metric.has_test_file,
            "maintainability_score": metric.maintainability_score,
            "performance_analyzed": languages.get(metric.file_path) in perf_languages,
            # What the default queue shows, so the lens and the headline agree.
            # Observations stay every open one on the file: evidence, not a count to act on.
            "performance_opportunities": rollup.queued if rollup else 0,
            "performance_observations": rollup.observations if rollup else 0,
            "code_origin": metric.code_origin,
            "is_hotspot": metric.file_path in hotspots,
        }
        actionability = _leading_actionability(rollup)
        if actionability is not None:
            row["performance_actionability"] = actionability
            row["performance_rank"] = rollup.best_rank
        return row

    def _modules(self, drawn: list[Any], burden: dict[str, Any]) -> list[dict[str, Any]]:
        """Per-module rollup over the drawn field, for the navigable list.

        Counted over what is drawn rather than over the repository, because
        this list is how a keyboard reaches the field it describes.
        """
        rollup: dict[str, dict[str, Any]] = {}
        for metric in drawn:
            key = metric.module or "(ungrouped)"
            row = rollup.setdefault(
                key,
                {
                    "module": key,
                    "files_shown": 0,
                    "opportunities": 0,
                    "observations": 0,
                    "plan_ready": 0,
                    "best_rank": None,
                },
            )
            row["files_shown"] += 1
            file_burden = burden.get(metric.file_path)
            if file_burden is None:
                continue
            row["opportunities"] += file_burden.queued
            row["observations"] += file_burden.observations
            row["plan_ready"] += file_burden.plan_ready
            best = row["best_rank"]
            row["best_rank"] = (
                file_burden.best_rank if best is None else min(best, file_burden.best_rank)
            )
        ordered = sorted(
            rollup.values(),
            key=lambda r: (
                0 if r["best_rank"] is not None else 1,
                r["best_rank"] if r["best_rank"] is not None else 0,
                r["module"],
            ),
        )
        return ordered[:_MODULE_ROLLUP_CAP]

    def _performance_block(
        self, rollups: list[Any], summary: Any, eligible: int
    ) -> dict[str, Any] | None:
        """What the lens needs to describe itself honestly, or nothing.

        ``None`` means this index has never materialized the read model, which
        is a different answer from "analyzed and nothing surfaced" and the lens
        must not render the two the same way. Its totals are the repository's
        whole inventory, every context and state; the nodes count the queue.
        """
        if summary is None and not rollups:
            return None
        return {
            "files_with_opportunities": len(rollups),
            "files_with_opportunities_eligible": eligible,
            "opportunities_total": sum(r.opportunities for r in rollups),
            "observations_total": sum(r.observations for r in rollups),
            "actionability": {
                "plan_ready": sum(r.plan_ready for r in rollups),
                "advisory": sum(r.advisory for r in rollups),
                "investigate": sum(r.investigate for r in rollups),
                "expected": sum(r.expected for r in rollups),
            },
            "model_version": getattr(summary, "performance_model_version", None),
            "analyzed_commit": getattr(summary, "analyzed_commit", None),
        }


def _origin_tier(metric: Any) -> int:
    """Admission tier for one row; an unstamped row is read off ``is_test``."""
    origin = metric.code_origin
    if origin in _ORIGIN_TIER:
        return _ORIGIN_TIER[origin]
    return _ORIGIN_TIER["test"] if metric.is_test else _ORIGIN_TIER["production"]


def _admission_key(metric: Any, perf_position: dict[str, int]) -> tuple[Any, ...]:
    """Where one file sits in the admission order, as a total sort key.

    Inside a tier, an open performance cause leads in queue order. Then health
    debt, ``(10 - score) * sqrt(nloc)``: an unhealthy file outranks a clean one
    of any size, and among equally unhealthy files the bigger one carries more.
    A file health could not score has no debt to rank, so it follows the scored
    ones by size. The path breaks every tie, so two reads agree.
    """
    nloc = metric.nloc or 0
    tier = _origin_tier(metric)
    position = perf_position.get(metric.file_path)
    if position is not None:
        return (tier, 0, position, 0, metric.file_path)
    if metric.score is None:
        return (tier, 2, 0, -nloc, metric.file_path)
    debt = max(0.0, 10.0 - metric.score) * math.sqrt(nloc)
    return (tier, 1, -debt, -nloc, metric.file_path)


def _leading_actionability(rollup: Any) -> str | None:
    """The best thing a reader could do about this file, or nothing.

    Best rather than most common: a file with one stored plan and nine
    investigations is a file with a stored plan. Read over the queued causes,
    the ones the lens counts.
    """
    if rollup is None:
        return None
    # ``expected`` is never a lead: it has nothing to do.
    states = rollup.queued_states
    return next((s for s in ACTIONABILITY_ORDER if s != "expected" and s in states), None)
