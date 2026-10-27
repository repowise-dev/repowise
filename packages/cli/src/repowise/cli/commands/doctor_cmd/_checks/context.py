"""State the doctor check groups share, plus the store opener they all use."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from repowise.cli.helpers import get_db_url_for_repo, reconcile_schema_best_effort


@dataclass
class StoreDrift:
    """What the three-store reconciliation found; ``--repair`` acts on it."""

    missing_from_vector: set[str] = field(default_factory=set)
    orphaned_vector: set[str] = field(default_factory=set)
    missing_from_fts: set[str] = field(default_factory=set)
    orphaned_fts: set[str] = field(default_factory=set)
    # An index on disk holding no vectors at all. Repairing that is a whole
    # reindex, which doctor does not start on a hosted embedder unasked.
    vector_store_empty: bool = False

    def has_mismatches(self) -> bool:
        return bool(
            self.missing_from_fts
            or self.orphaned_fts
            or self.missing_from_vector
            or self.orphaned_vector
        )


@dataclass
class CheckContext:
    """Inputs one group reads from an earlier one, and results repair and render need."""

    repo_path: Path
    repowise_dir: Path
    # ``doctor --quick``: skip starting the MCP server and reconciling the
    # stores, the two checks that take seconds. The web Setup page runs this
    # on every load and the full set in the background.
    quick: bool = False
    db_ok: bool = False
    page_count: int = 0
    state: dict = field(default_factory=dict)
    state_ok: bool = False
    stale_counts: dict[str, int] = field(default_factory=dict)
    stale_count: int = 0
    drift: StoreDrift = field(default_factory=StoreDrift)
    coord_drift_color: str = "white"
    coord_drift_pct: str = "N/A"
    registration_wedged: bool = False
    agents_need_refresh: bool = False


async def open_store(repo_path: Path):
    """Reconcile the schema, then return ``(engine, session_factory)``.

    Reconciled first so doctor reports the store's real state rather than a
    `no such column` failure on a store one repowise older.
    """
    from repowise.core.persistence import create_engine, create_session_factory

    url = get_db_url_for_repo(repo_path)
    await reconcile_schema_best_effort(url)
    engine = create_engine(url)
    return engine, create_session_factory(engine)
