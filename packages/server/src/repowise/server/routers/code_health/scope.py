"""Narrowing a code-health response to one half of the repository."""

from __future__ import annotations

from typing import Any

from fastapi import Query

from repowise.core.analysis.health.scope import DEFAULT_SCOPE, SCOPES, parse_scope

ScopeQuery = Query(
    DEFAULT_SCOPE,
    description=(
        "Which files to report on: 'all' (production and tests) or 'production'. "
        "Tests score higher than production code, so narrowing lowers every figure "
        "without a defect having been found."
    ),
    pattern=f"^({'|'.join(SCOPES)})$",
)


def narrow(
    scope: str, metrics: list[Any], *keyed_by_path: list[Any]
) -> tuple[list[Any], ...]:
    """Filter *metrics* to *scope*, then everything else to the files it kept.

    Metric rows carry ``is_test``, so they answer the question directly.
    Findings and their kin only carry a path, so they follow the metrics.
    """
    if parse_scope(scope) != "production":
        return (metrics, *keyed_by_path)
    kept = [m for m in metrics if not m.is_test]
    paths = {m.file_path for m in kept}
    return (kept, *([r for r in rows if r.file_path in paths] for rows in keyed_by_path))
