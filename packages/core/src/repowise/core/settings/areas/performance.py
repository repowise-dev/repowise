"""Performance: what repowise keeps on disk and in memory, and what it costs.

A setting lands here when its job is the index's footprint rather than what
the index says, or when turning it off is the way to save time, disk or
memory. Its key keeps the name of the part it governs
(``wiki.page_versions_keep``, ``refactoring.enabled``), so config.yaml stays
grouped by subject; only the listing and the settings page group them here.
"""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "performance"
_s = factory(AREA)

SETTINGS: tuple[Setting, ...] = (
    _s(
        "wiki.page_versions_keep",
        "int",
        10,
        "Earlier versions kept per wiki page; older ones are deleted when the page is rewritten.",
        # Read on every page write, so a change applies to the next one; pages
        # no write touches are trimmed by the next update that indexes a commit.
        minimum=1,
        maximum=100_000,
    ),
    _s("refactoring.enabled", "bool", True, "Work out refactoring plans in the health pass."),
    _s(
        "health.duplication.enabled",
        "bool",
        True,
        "Find duplicated code, which DRY violation findings, copied test assertion blocks "
        "and Extract Helper plans are built from. Off skips it in init and update and "
        "deletes its cache files under .repowise/.",
    ),
    _s(
        "performance.warm_caches",
        "bool",
        True,
        "Build the MCP server's search caches right after an agent connects, so the "
        "first question is fast. Off leaves them to the first question that needs them.",
        reload="restart",
    ),
)
