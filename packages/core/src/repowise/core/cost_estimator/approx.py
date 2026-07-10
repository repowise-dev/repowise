"""Pre-parse generation-plan approximation.

:func:`build_generation_plan` needs parsed files and a finalized graph, which
means running the ingestion pipeline first. The server's pre-flight check must
answer "roughly how many pages, roughly how much" *before* any indexing work,
from nothing but a file count. This module derives an approximate plan from
the same selection ratios the real selector converges on, so the pre-flight
number and the post-index estimate stay in the same ballpark.

Approximation, not selection: counts here can differ from the real plan by a
handful of pages (module grouping and SCC detection need the graph). The
variance bracket reported by :func:`~.estimator.estimate_cost` absorbs that.
"""

from __future__ import annotations

from .types import PageTypePlan

# Observed module-page ratio: curated/community grouping lands near one
# module page per ~25 selected file pages, floored at 1 and capped so a
# monorepo estimate does not balloon.
_FILES_PER_MODULE = 25
_MAX_MODULE_PAGES = 24

# The curated onboarding collection has 8 fixed slots; slots without
# enough signal are skipped, so this biases slightly high on tiny repos.
_ONBOARDING_SLOTS = 8


def approximate_generation_plan(
    file_count: int,
    *,
    coverage_pct: float = 0.20,
    onboarding: bool = True,
) -> list[PageTypePlan]:
    """Return an approximate plan for *file_count* documentable files."""
    file_count = max(0, file_count)
    file_pages = round(file_count * max(0.0, min(coverage_pct, 1.0)))
    if file_count > 0:
        file_pages = max(1, file_pages)
    module_pages = (
        min(_MAX_MODULE_PAGES, max(1, file_pages // _FILES_PER_MODULE)) if file_pages else 0
    )

    plans = [
        PageTypePlan(page_type="file_page", count=file_pages, level=0),
        PageTypePlan(page_type="module_page", count=module_pages, level=1),
        PageTypePlan(page_type="repo_overview", count=1 if file_count else 0, level=2),
        PageTypePlan(page_type="architecture_diagram", count=1 if file_count else 0, level=2),
    ]
    if onboarding and file_count:
        plans.append(PageTypePlan(page_type="onboarding", count=_ONBOARDING_SLOTS, level=2))
    return [p for p in plans if p.count > 0]
