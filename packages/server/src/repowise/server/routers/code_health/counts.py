"""Reading a code-health response with or without its change-history half."""

from __future__ import annotations

from typing import Any

from fastapi import Query

from repowise.core.analysis.health.counts import COUNTS, DEFAULT_COUNTS, parse_counts
from repowise.core.analysis.health.counts import project as project_metrics
from repowise.core.analysis.health.models import split_by_origin

CountsQuery = Query(
    DEFAULT_COUNTS,
    description=(
        "What the score counts: 'everything' (code shape and change history, as "
        "calibrated) or 'code_shape' (the git-derived half removed). Change "
        "history rises as a file is worked on, so it answers what a repository "
        "has been through rather than what its code is like."
    ),
    pattern=f"^({'|'.join(COUNTS)})$",
)


def project(
    counts: str, metrics: list[Any], *findings: list[Any]
) -> tuple[Any, ...]:
    """Re-score *metrics* under *counts*, returning ``(rows, ..., unscored)``.

    The findings a reading stops counting are dropped with it: under
    ``code_shape`` a history finding contributes to no figure on the page, so
    leaving it in the lists would show work that sums past the score it sits
    under. The count of rows the reading could not answer for is returned
    alongside rather than recomputed, which on a large repo is a second walk of
    the whole table building a second set of wrappers.
    """
    projected, unscored = project_metrics(counts, metrics)
    if parse_counts(counts) != "code_shape":
        return (projected, *findings, unscored)
    return (projected, *(split_by_origin(rows)[0] for rows in findings), unscored)
