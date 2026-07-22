"""Vector-store construction shared across CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_TABLE_NAME = "wiki_pages"


def existing_vector_dim(lance_dir: Path) -> int | None:
    """Return the width of the vectors already stored at *lance_dir*.

    The table records what built it, which makes this the one embedder fact
    that cannot drift from reality. ``None`` means "could not establish" — no
    directory, no lancedb installed, no table yet, or a schema without a
    fixed-width vector column — and every caller treats that as "do not act",
    never as a difference.
    """
    if not lance_dir.exists():
        return None
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
    return dim if isinstance(dim, int) and dim > 0 else None


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
    decisions stay matchable across runs; falls back to an in-memory store
    (which only sees this run's vectors) when LanceDB isn't installed.

    Returns ``None`` when handing back a store would destroy the existing one
    (see :func:`_mock_would_clobber`). Every caller already treats ``None`` as
    "embedding is off for this run"; full-text search is unaffected either way.
    """
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    lance_dir = repo_path / ".repowise" / "lancedb"
    if _mock_would_clobber(lance_dir, embedder):
        # Say it once, here, rather than in each caller: skipping silently is
        # how someone ends up wondering why search went quiet. Worded as
        # "not updating" rather than "skipped" because the generation phase
        # substitutes a throwaway in-memory store for a None one, so this run
        # does still embed — it just does not persist anywhere.
        from repowise.cli.helpers import console

        console.print(
            "[yellow]Search index left unchanged:[/yellow] this run has no real embedder, "
            "and the\nexisting index was built with one. Kept it rather than overwrite it. "
            "Set an\nembedder key and run [cyan]repowise reindex[/cyan] to refresh it."
        )
        return None
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore

        lance_dir.mkdir(parents=True, exist_ok=True)
        return LanceDBVectorStore(str(lance_dir), embedder=embedder)
    except ImportError:
        return InMemoryVectorStore(embedder)
