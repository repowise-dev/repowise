"""Vector-store construction shared across CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.core.store_location import resolve_store_dir

_TABLE_NAME = "wiki_pages"


def existing_vector_dim(lance_dir: Path) -> int | None:
    """Return the width of the vectors already stored at *lance_dir*.

    The table records what built it, which makes this the one embedder fact
    that cannot drift from reality. ``None`` means "could not establish" — no
    directory, no lancedb installed, no table yet, or a schema without a
    fixed-width vector column — and every caller treats that as "do not act",
    never as a difference.

    The width the writer recorded beside the table is read first, so the
    steady state never imports lancedb. A store written before that record
    existed is probed once and the answer recorded; the writer re-stamps it
    on its next write, so a stale record cannot outlive one.
    """
    if not lance_dir.exists():
        return None
    from repowise.core.persistence.vector_store.lancedb_store import (
        read_recorded_vector_dim,
        record_vector_dim,
    )

    recorded = read_recorded_vector_dim(lance_dir, _TABLE_NAME)
    if recorded is not None:
        return recorded
    try:
        import lancedb  # type: ignore[import]

        db = lancedb.connect(str(lance_dir))
        if _TABLE_NAME not in db.table_names():
            return None
        field = db.open_table(_TABLE_NAME).schema.field("vector")
        dim = getattr(field.type, "list_size", None)
    except Exception:
        return None
    # pyarrow reports -1 for variable-length lists, which tells us nothing.
    if not (isinstance(dim, int) and dim > 0):
        return None
    record_vector_dim(lance_dir, _TABLE_NAME, dim)
    return dim


def semantic_search_status(embedder: str | None, embed_failed_pages: int) -> str:
    """Whether a run left semantic search usable: ``available`` or not.

    Read from what the run did, not only from the embedder's name: a real
    embedder whose vector writes failed leaves the same empty index the mock
    does, and calling it available hid exactly that.
    """
    if not embedder or embedder == "mock" or embed_failed_pages:
        return "unavailable"
    return "available"


def embed_failure_message(embedder: str | None, embed_failed_pages: int) -> str | None:
    """The error a run exits with when a real embedder failed to write.

    ``None`` for the mock: its vectors were never semantic, so losing them
    changes nothing a reader relies on.
    """
    if not embed_failed_pages or not embedder or embedder == "mock":
        return None
    return (
        f"Embedding failed for {embed_failed_pages} page(s), so semantic search is "
        "unavailable (full-text search still works). Fix the cause in the warning "
        "above (for a broken LanceDB install: pip install --force-reinstall lancedb), "
        "then run: repowise reindex"
    )


def _mock_would_clobber(lance_dir: Path, embedder: Any) -> bool:
    """True when writing *embedder* into the store at *lance_dir* would drop it.

    The mock is the keyless default, and its 8-wide vectors written into a
    table some earlier run filled at 1536 make the LanceDB writer drop the
    table, taking every page *and* decision embedding with it. That is the
    right call when a real embedder changes model, and never the right call
    for the mock: nothing is gained, a working index is lost, and the user is
    told nothing. So only the mock is refused here — a real-to-real width
    change is still the intended rebuild.
    """
    from repowise.core.providers.embedding.base import MockEmbedder

    if not isinstance(embedder, MockEmbedder):
        return False
    existing = existing_vector_dim(lance_dir)
    return existing is not None and existing != embedder.dimensions


def build_vector_store(repo_path: Path, embedder: Any) -> Any | None:
    """Build the repo-local vector store, preferring LanceDB.

    Uses LanceDB at ``.repowise/lancedb`` so previously-embedded pages and
    decisions stay matchable across runs. There is no in-memory fallback:
    the store imports lancedb on first use, so a missing or broken install
    fails there with a named error, which the embed step reports as a failed
    embed rather than a run that only looked healthy.

    Returns ``None`` when handing back a store would destroy the existing one
    (see :func:`_mock_would_clobber`). Every caller already treats ``None`` as
    "embedding is off for this run"; full-text search is unaffected either way.
    """
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    lance_dir = resolve_store_dir(repo_path) / "lancedb"
    if _mock_would_clobber(lance_dir, embedder):
        # Say it once, here, rather than in each caller: skipping silently is
        # how someone ends up wondering why search went quiet. Worded as
        # "not updating" rather than "skipped" because the generation phase
        # substitutes a throwaway in-memory store for a None one, so this run
        # does still embed — it just does not persist anywhere.
        #
        # stderr, not stdout: this fires two modules below whichever command
        # asked for a store, so a command-level "divert notices in --format
        # json" cannot reach it, and on stdout it would land inside the JSON
        # document. Same defect that corrupted ``search --format json`` from
        # ``build_embedder``; fixed at the source for the same reason.
        from repowise.cli.helpers import err_console

        err_console.print(
            "[yellow]Search index left unchanged:[/yellow] this run has no real embedder, "
            "and the\nexisting index was built with one. Kept it rather than overwrite it. "
            "Set an\nembedder key and run [cyan]repowise reindex[/cyan] to refresh it."
        )
        return None
    lance_dir.mkdir(parents=True, exist_ok=True)
    return LanceDBVectorStore(str(lance_dir), embedder=embedder)
