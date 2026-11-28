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
    decision_rekey_pending: bool = False

    def has_mismatches(self) -> bool:
        return bool(
            self.missing_from_fts
            or self.orphaned_fts
            or self.missing_from_vector
            or self.orphaned_vector
            or self.decision_rekey_pending
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
    stale_guidance: str = ""
    drift: StoreDrift = field(default_factory=StoreDrift)
    coord_drift_color: str = "white"
    coord_drift_pct: str = "N/A"
    registration_wedged: bool = False
    agents_need_refresh: bool = False
    # config.yaml keys holding a value repowise ignores; ``--repair`` resets them.
    invalid_settings: list[str] = field(default_factory=list)
    # Retired config.yaml keys still in the file; ``--repair`` removes them.
    retired_settings: list[str] = field(default_factory=list)


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


def sqlite_page_counts(db_path: Path) -> tuple[int, int, int]:
    """``(page_size, page_count, freelist_count)`` of a SQLite file, read-only.

    SQLite keeps pages freed by deletes in the file for reuse instead of
    shrinking it, so the file size is a high-water mark; the free pages say
    how much of it holds nothing.
    """
    import sqlite3

    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        conn.close()
    return page_size, pages, free
