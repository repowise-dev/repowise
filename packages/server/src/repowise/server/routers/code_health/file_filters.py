"""Row filters shared by the file inventory and the work queue.

Both surfaces narrow the same per-file metric rows by the same five controls,
and a queue that disagreed with the inventory about what "untested" means
would be a second answer to one question.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from repowise.core.analysis.health.grading import GOOD_MIN
from repowise.core.persistence import crud

FAILING_DESCRIPTION = f"score below {GOOD_MIN}, where green starts"


async def hotspot_paths(session: Any, repo_id: str) -> set[str]:
    """Paths git metadata marks as hotspots."""
    git_meta = await crud.get_all_git_metadata(session, repo_id)
    return {p for p, gm in git_meta.items() if getattr(gm, "is_hotspot", False)}


def metric_filter(
    *,
    search: str | None = None,
    module: str | None = None,
    only_hotspots: bool = False,
    only_untested: bool = False,
    only_failing: bool = False,
    hotspots: set[str] | None = None,
) -> Callable[[Any], bool]:
    """A predicate over metric rows for the inventory/queue controls.

    ``only_failing`` is the band edge green starts at, so "failing" here means
    the same thing the page's colour does.
    """
    needle = search.lower() if search else None
    hotspot_set = hotspots or set()

    def keep(m: Any) -> bool:
        if needle and needle not in m.file_path.lower():
            return False
        if module and not m.file_path.startswith(module):
            return False
        if only_hotspots and m.file_path not in hotspot_set:
            return False
        if only_untested and m.has_test_file:
            return False
        return not (only_failing and m.score >= GOOD_MIN)

    return keep
