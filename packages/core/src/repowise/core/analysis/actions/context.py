"""Facts about the repository that decide which rules apply to it.

The same rule set runs on a single-author side project and a forty-person
monorepo. What changes is which rules can say anything true: ownership rules
need more than one active author, test rules need to know whether coverage was
measured, and "busy" means a different commit count in each.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .facts import RepoFacts

WEEK = timedelta(days=7)

#: Below this many active authors in 90 days, ownership is the shape of the
#: repository, not a risk in it.
TEAM_MIN_AUTHORS = 3

#: A file is busy when it clears both: a floor so a quiet repository does not
#: call two commits "hot", and the repository's own 90th percentile so a busy
#: one does not call every file hot.
BUSY_FLOOR = 5
BUSY_PERCENTILE = 0.9

#: The same two-sided bar for bug-fix commits: at least three, and the
#: repository's 95th percentile among files that had any, so "fixes keep
#: landing" names the few files that stand out rather than every file touched
#: by a fix.
FIX_FLOOR = 3
FIX_PERCENTILE = 0.95

#: Said by every rule that ranks files by their history when the index holds
#: fewer commits than it takes to call one file busy: a one-commit import or a
#: shallow clone has churn and fix counts, but they describe the clone.
HISTORY_TOO_SHORT = (
    "The index holds too few commits to rank files by their history; "
    "fetch the full history and run `repowise update`."
)


@dataclass(frozen=True, slots=True)
class RepoContext:
    anchor: datetime | None
    week_start: datetime | None
    production_files: int
    active_authors_90d: int
    busy_threshold: int
    fix_threshold: int
    fix_commits_90d: int
    history_commits: int | None = None

    @property
    def is_team(self) -> bool:
        return self.active_authors_90d >= TEAM_MIN_AUTHORS

    @property
    def history_too_short(self) -> bool:
        return self.history_commits is not None and self.history_commits < BUSY_FLOOR

    def in_week(self, when: datetime | None) -> bool:
        if when is None or self.week_start is None:
            return False
        return _naive(when) >= _naive(self.week_start)


def _naive(value: datetime) -> datetime:
    # SQLite hands back naive datetimes and Postgres aware ones; compare in UTC
    # wall time either way.
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def build_context(facts: RepoFacts) -> RepoContext:
    production = [f for f in facts.files.values() if not f.is_test]
    busy = _percentile([f.commits_90d for f in production if f.commits_90d > 0], BUSY_PERCENTILE)
    fixes = _percentile(
        [f.fix_commits_90d for f in production if f.fix_commits_90d > 0], FIX_PERCENTILE
    )
    anchor = facts.anchor
    return RepoContext(
        anchor=anchor,
        week_start=(anchor - WEEK) if anchor else None,
        production_files=len(production),
        active_authors_90d=facts.active_authors_90d,
        busy_threshold=max(BUSY_FLOOR, busy),
        fix_threshold=max(FIX_FLOOR, fixes),
        fix_commits_90d=facts.fix_commits_90d,
        history_commits=facts.history_commits,
    )
