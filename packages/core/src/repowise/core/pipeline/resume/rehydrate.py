"""Reconstruct phase *inputs* from persisted DB rows for a resumed run.

The graph is rehydrated by the existing :func:`rehydrate_graph_builder`
(re-exported here for a single resume import surface). This module adds the
git side: turning persisted ``git_metadata`` rows back into the
``git_meta_map`` dict that the analysis phases consume.

Caveat — the transient per-line ``BlameIndex`` is intentionally never
persisted (see ``phases/git.py: drop_transient_git_signals``), so a
rehydrated ``git_meta_map`` carries every durable signal but no
``blame_index``. Blame-dependent biomarkers treat its absence as "no
signal" — which is exactly the case for FAST/ESSENTIAL-tier runs that
never computed blame in the first place.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..upgrade import rehydrate_graph_builder

logger = structlog.get_logger(__name__)

__all__ = ["rehydrate_git_meta_map", "rehydrate_graph_builder"]


def _git_metadata_to_dict(row: Any) -> dict[str, Any]:
    """Project a ``GitMetadata`` ORM row to the dict shape analysis expects.

    Uses column introspection so every persisted signal is carried faithfully
    and the keys match the column names the fresh git indexer writes (the
    upsert path sets attributes by matching dict keys to columns). JSON
    columns stay as their stored strings — consumers (e.g. the duplication
    co-change scorer) ``json.loads`` them on demand.
    """
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        name = col.name
        if name in ("id", "repository_id"):
            continue
        out[name] = getattr(row, name)
    return out


async def rehydrate_git_meta_map(session: Any, repo_id: str) -> dict[str, dict[str, Any]]:
    """Rebuild ``{file_path -> git metadata dict}`` from persisted rows.

    Returns an empty dict when nothing was persisted (so a caller can detect
    "git was never indexed" and fall back to recomputing).
    """
    from repowise.core.persistence import get_all_git_metadata

    try:
        rows = await get_all_git_metadata(session, repo_id)
    except Exception as exc:
        logger.debug("rehydrate_git_meta_failed", error=str(exc))
        return {}
    return {path: _git_metadata_to_dict(row) for path, row in rows.items()}
