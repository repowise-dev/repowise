"""Performance: what repowise keeps on disk and in memory, and what it costs.

A setting lands here when its job is the index's footprint rather than what
the index says. Its key keeps the name of the part it governs
(``wiki.page_versions_keep``), so config.yaml stays grouped by subject.
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
    ),
)
