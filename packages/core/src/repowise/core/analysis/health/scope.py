"""What health scores, and the names of the halves it can report on.

Production/test narrowing itself is a column read (``HealthFileMetric.is_test``,
stamped from the shared path classifier), so only the vocabulary lives here.
"""

from __future__ import annotations

from functools import cache
from typing import Literal

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
    """
    return bool(language) and language in _scored_languages()


def parse_scope(value: str | None) -> HealthScope:
    """Normalise a caller's scope, falling back to the default on anything else."""
    return value if value in SCOPES else DEFAULT_SCOPE  # type: ignore[return-value]
