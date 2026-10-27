"""The two query parameters that narrow a map read to the files it shows.

Shared by the area routes and the health map so the tree, the area cards and
the drawn files always describe the same files. The held set itself is
:data:`repowise.core.analysis.finding_registry.MAP_HELD_ORIGINS`.
"""

from __future__ import annotations

from fastapi import Query

HOLD_QUERY = Query(
    "none",
    pattern="^(none|map)$",
    description="`map` leaves out the origins the area map holds by default; `none` (default) reads every file.",
)
INCLUDE_QUERY = Query(
    "",
    max_length=200,
    description="With hold=map: comma-separated held origins to bring back, e.g. `test,build`.",
)
