"""What every first-look section reads, loaded once per report.

The facts are :func:`load_repo_facts`, the same filtered rows ``repowise next``
ranks, so the report and ``next`` cannot disagree about a finding. The health
metrics are the stored rows every health surface serves (worst first, exclude
rules applied), and each file's origin is its stored ``code_origin``, read off
``path_origin()`` only for a file with no row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.core.code_origin import path_origin

#: The one origin whose findings the report lists; Hygiene is the exception,
#: where the origin is the finding.
PRODUCTION = "production"


class UnavailableError(Exception):
    """A section's store could not be read; the report names it instead."""


@dataclass
class FirstLookContext:
    session: Any
    repository_id: str
    #: The checkout; ``None`` where none is at hand (a served snapshot).
    repo_root: Path | None
    #: :class:`~repowise.core.analysis.actions.facts.RepoFacts`.
    facts: Any
    #: Stored health metric rows, worst first.
    metrics: list[Any] = field(default_factory=list)
    _origins: dict[str, str] = field(default_factory=dict)
    _cache: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from repowise.core.analysis.finding_registry import stored_origin

        self._origins = {
            m.file_path: stored_origin(m.code_origin, m.is_test)
            for m in self.metrics
            if getattr(m, "file_path", None)
        }

    def origin(self, path: str) -> str:
        """The stored origin of *path*, else ``path_origin()``."""
        return self._origins.get(path) or path_origin(path)

    def is_production(self, path: str) -> bool:
        return self.origin(path) == PRODUCTION

    def metric(self, path: str) -> Any | None:
        """The stored health metric row of *path*, or ``None``."""
        if "metrics" not in self._cache:
            self._cache["metrics"] = {m.file_path: m for m in self.metrics}
        return self._cache["metrics"].get(path)

    @property
    def origins(self) -> dict[str, str]:
        """Stored origin per file with a health row."""
        return self._origins

    @property
    def rules(self) -> Any:
        """The rules' :class:`~repowise.core.analysis.actions.context.RepoContext`."""
        if "rules" not in self._cache:
            from repowise.core.analysis.actions.context import build_context

            self._cache["rules"] = build_context(self.facts)
        return self._cache["rules"]

    def require(self, store: str) -> None:
        """Raise :class:`UnavailableError` when the facts could not read *store*."""
        reason = self.facts.unavailable.get(store)
        if reason:
            raise UnavailableError(reason)


async def load_context(
    session: Any, repository_id: str, repo_root: Path | None = None
) -> FirstLookContext:
    from repowise.core.persistence.crud.analysis.actions import load_repo_facts
    from repowise.core.persistence.crud.analysis.health.metrics import get_health_metrics

    facts = await load_repo_facts(session, repository_id)
    metrics = await get_health_metrics(session, repository_id)
    return FirstLookContext(session, repository_id, repo_root, facts, metrics)


__all__ = ["PRODUCTION", "FirstLookContext", "UnavailableError", "load_context"]
