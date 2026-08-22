"""How much of a coverage measurement still describes the current file.

A coverage report is a measurement taken at one commit. Nothing re-runs the
tests, so the stored percentage keeps being served unchanged while the code
underneath it moves. The report date is already rendered next to the figure
(``ingested_at`` / ``ingested_commit_sha``), which tells a reader the
measurement is old but not what that costs *for this file*: a month-old report
is perfect for a file nobody touched and meaningless for the one under active
development, and the date reads identically in both cases.

This module answers the per-file half. Given the covered line set the report
recorded and the lines that have changed since it was taken, it splits the
measurement into the part that still describes the file and the part that does
not:

* ``confirmed`` - lines the report saw covered, that have not changed since.
  The measurement still holds here.
* ``invalidated`` - lines the report saw covered, that have changed since. The
  test that covered them ran against different code, so the fact is void. Not
  "now uncovered": unknown.

Deliberately no re-derived percentage. Whether an invalidated line is covered
today is exactly what nobody knows without running the tests, and a blended
"estimated coverage" would be a number no reader could check. ``drift_pct`` is
the share of the measurement that went unknown, which is a fact about the
*measurement*, not a claim about the code.

Ceiling, deliberate: the diff runs ``<ref>..HEAD``, so uncommitted work in the
tree is not counted as drift. Health analysis reads the working tree, so a file
with heavy uncommitted edits will under-report its drift until those land. The
upgrade is a second diff against the working tree, unioned in; it is left out
here because every consumer of this module renders a committed-history figure
beside it and mixing the two silently would be worse than the gap.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from ...change_risk.features import _git
from ...changed_lines import changed_lines


@dataclass(frozen=True)
class CoverageDecay:
    """The measured/still-valid split for one file's coverage.

    ``measured`` is the size of the covered line set the report recorded, so
    ``confirmed + invalidated == measured`` always holds. ``drift_pct`` is
    ``invalidated / measured``, or 0.0 when the report recorded no covered
    lines at all (nothing to invalidate, so nothing drifted).
    """

    measured: int
    confirmed: int
    invalidated: int
    drift_pct: float

    @property
    def is_stale(self) -> bool:
        """Whether enough of the measurement went unknown to stop quoting it.

        The threshold is a display convention, not a scoring input. Nothing in
        this module deducts health points; consumers use it to decide whether
        to caveat the figure they render.
        """
        return self.measured >= STALE_MIN_MEASURED and self.drift_pct >= STALE_DRIFT_PCT


#: Share of a measurement that must have gone unknown before a consumer should
#: caveat the figure. A fifth is enough that "91% covered" has stopped being a
#: fair summary of the file in front of the reader.
STALE_DRIFT_PCT = 20.0

#: Covered lines a file must have before the ratio means anything. Measured on
#: this repo's own index: without a floor the drift ranking is led by package
#: ``__init__.py`` files carrying a single covered line, where one version bump
#: is 100% drift and says nothing about the file. Ten lines puts the trigger at
#: two changed lines, which is the smallest move worth caveating a figure over.
STALE_MIN_MEASURED = 10


def measurement_ref(
    repo_path: str,
    ingested_commit_sha: str | None,
    ingested_at: datetime | None,
) -> str | None:
    """The commit a measurement was taken at, for diffing forward from.

    Prefers the recorded sha. Falls back to the commit that was HEAD at
    ``ingested_at`` when the sha is absent, which it is for any row written
    before the repository row carried a head commit, and for any row written by
    a path that could not resolve one. The fallback is approximate by up to one
    commit and that is the correct trade: an approximate drift figure is worth
    more than no figure, and the error is bounded by how much lands in a single
    commit.

    ``None`` means the measurement cannot be placed in history at all, and the
    caller should render the stored figure without a drift claim rather than
    guess.
    """
    if ingested_commit_sha and _ref_exists(repo_path, ingested_commit_sha):
        return ingested_commit_sha
    if ingested_at is None:
        return None
    # ``ingested_at`` is written UTC-aware, but a SQLite-backed store hands it
    # back naive, and git reads a naive timestamp as *local* time. Left alone
    # that shifts the cut by the reader's offset and can pick the wrong side of
    # a commit. Stamping UTC back on is correct for every row this column has
    # ever held.
    stamped = ingested_at if ingested_at.tzinfo is not None else ingested_at.replace(tzinfo=UTC)
    try:
        sha = _git(
            ["rev-list", "-1", f"--before={stamped.isoformat()}", "HEAD"],
            repo_path,
        ).strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return sha or None


def _ref_exists(repo_path: str, ref: str) -> bool:
    try:
        return bool(
            _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo_path, check=False)
            .strip()
        )
    except (subprocess.SubprocessError, OSError):
        return False


def decay_since(
    repo_path: str,
    ref: str,
    covered_by_file: dict[str, set[int]],
) -> dict[str, CoverageDecay]:
    """Per-file decay for every file in *covered_by_file*, from one diff.

    One ``git diff`` for the whole range rather than one per file: the callers
    hold every coverage row in the repo, and a per-file diff would be a
    subprocess per row. Files with no changed lines in the range still get a
    record, with everything confirmed, so a consumer can render "nothing has
    moved" as distinct from "we did not check".

    Returns an empty mapping when the range cannot be diffed (an unknown ref, a
    repository git cannot read), because a decay figure that silently defaults
    to zero drift would read as a freshness claim this never made.
    """
    if not covered_by_file:
        return {}
    try:
        changed, _label = changed_lines(repo_path, f"{ref}..HEAD")
    except (ValueError, subprocess.SubprocessError, OSError):
        return {}

    out: dict[str, CoverageDecay] = {}
    for path, covered in covered_by_file.items():
        out[path] = decay_for_file(covered, changed.get(path) or set())
    return out


def decay_for_file(covered: set[int], changed: set[int]) -> CoverageDecay:
    """The split for one file. Pure, and the unit the tests pin."""
    measured = len(covered)
    if measured == 0:
        return CoverageDecay(measured=0, confirmed=0, invalidated=0, drift_pct=0.0)
    invalidated = len(covered & changed)
    return CoverageDecay(
        measured=measured,
        confirmed=measured - invalidated,
        invalidated=invalidated,
        drift_pct=round(100.0 * invalidated / measured, 2),
    )
