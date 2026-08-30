"""The bounded field the Code Health map draws, selected deterministically.

A map is a cap, and a cap is a claim about what is not drawn. Ordering the
whole repository by lines of code and taking the top N is a defensible sample
for the health lens and the wrong one for the performance lens: on this
repository it hides tens of files that carry open opportunities, because a
small file can hold the worst cause in the tree.

So the feed admits files in three bands - the caller's active selection first,
then the files carrying open performance opportunities in rank order, then the
lines-of-code sample fills whatever capacity is left - and states exactly what
the cap pushed out. One response carries both the rendered set and the counts
describing it, so a caption can never disagree with the field beside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.perf.coverage import supported_perf_languages
from repowise.core.persistence import crud

__all__ = [
    "DEFAULT_MAP_CAP",
    "MAX_MAP_CAP",
    "HealthMapFeed",
    "HealthMapService",
]

DEFAULT_MAP_CAP = 2000
"""Nodes drawn by default. Above this the field stops separating anything."""

MAX_MAP_CAP = 4000
"""Ceiling a caller may raise the cap to. Beyond it the canvas is a texture."""

_MAX_ACTIVE = 50
"""Guaranteed paths one request may pin. A deep link names one or a few."""

_MODULE_ROLLUP_CAP = 200
"""Modules described in the textual alternative, worst performance first."""


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

    def payload(self) -> dict[str, Any]:
        return {
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
        self, *, cap: int = DEFAULT_MAP_CAP, active: tuple[str, ...] = ()
    ) -> HealthMapFeed:
        session, repo_id = self._session, self._repository_id
        metrics = await crud.get_health_metrics(session, repo_id)
        rollups = await crud.performance_file_rollups(session, repo_id)
        languages = await crud.get_file_language_map(session, repo_id)
        summary = await crud.get_performance_summary(session, repo_id)
        perf_languages = supported_perf_languages()

        by_path = {m.file_path: m for m in metrics}
        # A zero-NLOC file cannot be sized, and the map drops it on arrival.
        # Counting it as eligible would promise a node that never appears.
        eligible = [m for m in metrics if (m.nloc or 0) > 0]
        eligible_paths = {m.file_path for m in eligible}
        burden = {r.file_path: r for r in rollups}

        chosen: list[str] = []
        taken: set[str] = set()

        def admit(path: str) -> bool:
            if path in taken or path not in eligible_paths or len(chosen) >= cap:
                return False
            chosen.append(path)
            taken.add(path)
            return True

        active_shown = [path for path in active if admit(path)]

        performance_eligible = [r.file_path for r in rollups if r.file_path in eligible_paths]
        performance_shown = sum(1 for path in performance_eligible if admit(path))

        nloc_before = len(chosen)
        for metric in sorted(eligible, key=lambda m: (-(m.nloc or 0), m.file_path)):
            if len(chosen) >= cap:
                break
            admit(metric.file_path)
        nloc_shown = len(chosen) - nloc_before

        drawn = [by_path[path] for path in chosen]
        files = [
            self._row(metric, burden.get(metric.file_path), languages, perf_languages)
            for metric in drawn
        ]

        # Eligible and not taken, so this is what the cap pushed out. A cause
        # on a file with no lines can never be drawn at any cap, and counting
        # it here would promise a recovery that pinning the path cannot give.
        undrawn_perf = [
            r for r in rollups if r.file_path in eligible_paths and r.file_path not in taken
        ]
        omitted = {
            "files": len(eligible) - len(chosen),
            "performance_files": len(undrawn_perf),
            "opportunities": sum(r.opportunities for r in undrawn_perf),
            "observations": sum(r.observations for r in undrawn_perf),
        }

        return HealthMapFeed(
            files=files,
            cap=cap,
            shown=len(chosen),
            eligible_total=len(eligible),
            repository_total=len(metrics),
            selection={
                "basis": "active_then_performance_then_nloc",
                "active_requested": list(active),
                "active_shown": active_shown,
                "active_missing": [p for p in active if p not in taken],
                "performance_shown": performance_shown,
                "performance_eligible": len(performance_eligible),
                "nloc_shown": nloc_shown,
            },
            omitted=omitted,
            recovery={
                "guarantee_paths": (
                    "Re-request this feed with active=<comma-separated paths> to pin any "
                    "file into the drawn field."
                ),
                "opportunity_queue": (
                    f"/api/repos/{repo_id}/health/performance-opportunities lists open "
                    "opportunities, drawn or not, in production code; pass "
                    "context=all for every execution context."
                ),
                "raise_cap": f"cap accepts up to {MAX_MAP_CAP}.",
            },
            modules=self._modules(drawn, burden),
            performance=self._performance_block(rollups, summary, len(performance_eligible)),
        )

    def _row(
        self,
        metric: Any,
        rollup: Any,
        languages: dict[str, str | None],
        perf_languages: Any,
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
            "performance_opportunities": rollup.opportunities if rollup else 0,
            "performance_observations": rollup.observations if rollup else 0,
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
            row["opportunities"] += file_burden.opportunities
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
        must not render the two the same way.
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
            },
            "model_version": getattr(summary, "performance_model_version", None),
            "analyzed_commit": getattr(summary, "analyzed_commit", None),
        }


def _leading_actionability(rollup: Any) -> str | None:
    """The best thing a reader could do about this file, or nothing.

    Best rather than most common: a file with one stored plan and nine
    investigations is a file with a stored plan.
    """
    if rollup is None or rollup.opportunities == 0:
        return None
    if rollup.plan_ready:
        return "plan_ready"
    if rollup.advisory:
        return "advisory"
    return "investigate"
