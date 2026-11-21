"""What changed since a report was written: fixed, changed and new items.

The current items come from the same collector with the same filters, at no
model cost. An item id gone from the current set is fixed; one whose
fingerprint moved is changed; a current id the report never had is new. New
items are listed beside the report, never written into it. A section that
cannot be read now says nothing: its items are neither fixed nor new.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import FirstLookReport, Item


@dataclass(frozen=True, slots=True)
class Staleness:
    report_commit: str | None
    current_commit: str | None
    fixed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    new: tuple[Item, ...] = ()
    #: The current form of each ``changed`` item, in the same order: its path,
    #: line and facts as the index has them now.
    changed_items: tuple[Item, ...] = ()
    #: Sections that could not be read now, with the reason.
    unknown: Mapping[str, str] = field(default_factory=dict)

    @property
    def behind(self) -> bool:
        """The index has moved past the commit the report was written at."""
        return bool(self.current_commit) and self.current_commit != self.report_commit

    def to_json(self) -> dict[str, Any]:
        return {
            "report_commit": self.report_commit,
            "current_commit": self.current_commit,
            "behind": self.behind,
            "fixed": list(self.fixed),
            "changed": list(self.changed),
            "new": [i.to_json() for i in self.new],
            "changed_items": [i.to_json() for i in self.changed_items],
            "unknown": dict(self.unknown),
        }


def compare(
    report: FirstLookReport,
    current: Iterable[Item],
    *,
    current_commit: str | None = None,
    unknown: Mapping[str, str] | None = None,
) -> Staleness:
    """Pure: *report* against the items the collector finds now."""
    # A section the report could not read then has no items to compare.
    unknown = {**report.unavailable, **(unknown or {})}
    now = {i.id: i for i in current}
    was = {i.id: i for i in report.items() if i.section not in unknown}
    changed = tuple(i for i, item in was.items() if i in now and now[i].fingerprint != item.fingerprint)
    return Staleness(
        report_commit=report.stamp.commit,
        current_commit=current_commit,
        fixed=tuple(i for i in was if i not in now),
        changed=changed,
        new=tuple(item for i, item in now.items() if i not in was and item.section not in unknown),
        unknown=unknown,
        changed_items=tuple(now[i] for i in changed),
    )


async def staleness(
    session: Any, repository_id: str, report: FirstLookReport, repo_root: Path | None = None
) -> Staleness:
    """*report* against the index as it is now (read only, no model call)."""
    from repowise.core.persistence.models import Repository

    from .collect import collect_sections
    from .context import load_context

    ctx = await load_context(session, repository_id, repo_root)
    collected, unavailable = await collect_sections(ctx)
    repo = await session.get(Repository, repository_id)
    return compare(
        report,
        (i for c in collected.values() for i in c.items),
        current_commit=repo.head_commit if repo else None,
        unknown=unavailable,
    )


__all__ = ["Staleness", "compare", "staleness"]
