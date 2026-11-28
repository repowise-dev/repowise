"""Plugin records written with the index: the ``index-persistence`` seam.

Every implementation runs, in registration order. ``apply`` writes in the
index transaction and must not commit; raising rolls back the whole write.
``publish`` runs after the commit and the writer's cleanup; its failure reaches
the caller and the commit stays, so a plugin retries from what ``apply`` recorded.

Writers that only refresh derived rows (health, git tier, module labels) do not
run this seam. Deleting a repository does not run it either; plugin tables should
reference ``repositories.id`` with ``ON DELETE CASCADE``.

``publish_index_persistence`` also refreshes core's rationale comment sidecar
before the plugins publish, since every committed write passes through it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.registry.plugins import SeamRegistry


@dataclass(frozen=True)
class IndexWrite:
    mode: Literal["full", "incremental", "checkpoint"]
    """``checkpoint`` is init's INDEX checkpoint, committed before analysis; the run's
    final ``full`` write follows it, and no ``publish`` runs after it."""

    parsed_files: list[Any] | None
    """The files that parsed. Not a presence list: a file that failed to read or parse
    is missing here and still indexed. Read analysed values from *session*."""

    changed_paths: tuple[str, ...] = ()
    """Incremental: the diff's paths, deleted ones included, or every parsed path
    when ``traversed_paths`` is set."""

    deleted_paths: tuple[str, ...] = ()
    """Incremental: the paths whose rows this write deleted, by the liveness prune
    or, on a health re-score, by the exclude patterns. A rename is its old path here
    and its new path in ``changed_paths``."""

    traversed_paths: frozenset[str] | None = None
    """The paths the walk found, when core reconciled the index against them on this
    write; None otherwise. ``git_metadata`` keeps a row for every tracked file,
    walked or not."""

    degraded: tuple[str, ...] = ()
    """Incremental: ``"<step>: <error>"`` for each thing this update skipped so far."""


class IndexPersistence(Protocol):
    async def apply(self, session: AsyncSession, repository_id: str, write: IndexWrite) -> None:
        """Write in *session* without committing."""
        ...

    async def publish(self, repository_id: str, repo_path: Path) -> None:
        """Publish committed records; the plugin owns retries."""
        ...


index_persistence: SeamRegistry[IndexPersistence] = SeamRegistry("index-persistence")


async def apply_index_persistence(
    session: AsyncSession, repository_id: str, write: IndexWrite
) -> None:
    for _, implementation in index_persistence.all():
        await implementation.apply(session, repository_id, write)


async def publish_index_persistence(repository_id: str, repo_path: Path) -> None:
    # Core's own sidecar first: every committed index write passes here.
    from repowise.core.analysis.decisions.rationale_harvest import refresh_rationale_store

    await asyncio.to_thread(refresh_rationale_store, repo_path)
    for _, implementation in index_persistence.all():
        await implementation.publish(repository_id, repo_path)


__all__ = [
    "IndexPersistence",
    "IndexWrite",
    "apply_index_persistence",
    "index_persistence",
    "publish_index_persistence",
]
