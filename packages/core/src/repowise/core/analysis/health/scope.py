"""What health scores, and the names of the halves it can report on.

The ``production`` half is the file set ``production_average`` and
``worst_performer`` read (``ranking.split_in_scope``), so a narrowed reading,
its trend and the headline beside it agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from typing import Any, Literal

HealthScope = Literal["all", "production"]
DEFAULT_SCOPE: HealthScope = "all"
SCOPES: tuple[HealthScope, ...] = ("all", "production")

# SQL is declared non-code by the language registry — nothing parses it into
# symbols or import edges — but health walks it through sqlglot and carries
# markers of its own for it, so it scores here.
_ALSO_SCORED = frozenset({"sql"})


@cache
def _scored_languages() -> frozenset[str]:
    # Deferred: the language registry reaches ingestion, which imports this
    # package back. The same cycle ``test_paths`` breaks the same way.
    from ...ingestion.languages.registry import REGISTRY

    return frozenset(s.tag for s in REGISTRY.all_specs() if s.is_code) | _ALSO_SCORED


def scores_language(language: str | None) -> bool:
    """Whether health scores a file written in *language*.

    True for every language the registry declares as code, plus SQL. False for
    prose, configuration, data and markup — Markdown, JSON, YAML, TOML, HTML,
    Protobuf, GraphQL, lockfiles and anything unrecognised. None of them carry
    code shape to measure, while the history markers fire on them hard: a
    changelog every fix commit touches reads as a bug magnet. They get no
    score, no findings and no metric row.

    A wider question than ``complexity.languages.has_health_dialect``: a code
    file in a language with no dialect still gets a metric row (its history
    findings stand), but no score.
    """
    return bool(language) and language in _scored_languages()


def parse_scope(value: str | None) -> HealthScope:
    """Normalise a caller's scope, falling back to the default on anything else."""
    return value if value in SCOPES else DEFAULT_SCOPE  # type: ignore[return-value]


def rows_in_scope(scope: str | None, rows: Iterable[Any]) -> list[Any]:
    """*rows* kept by *scope*: all of them, or only production code for
    ``production`` (no tests, tooling, build, vendored, generated or docs
    example files). A row is judged by its path and origin, never its score:
    an unscored production row stays, so a caller still counts it."""
    if parse_scope(scope) != "production":
        return list(rows)
    # Deferred: ranking pulls in scoring and the queue, which this module stays out of.
    from .ranking import split_in_scope

    return split_in_scope(rows)[0]
