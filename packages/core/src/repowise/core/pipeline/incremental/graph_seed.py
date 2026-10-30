"""Read and cache the persisted graph snapshot reused by a scoped update.

Loading hundreds of thousands of ORM edge rows from SQLite is expensive on a
cold cache. After a successful index, the complete NetworkX node/edge snapshot
is sealed locally for a sequential read. It is only a hint: repository identity,
DB edge count and parser fingerprint must match. Missing/stale snapshots cause
a full graph rebuild; SQL rows are never used as a lossy substitute for graph
attributes that are not persisted in the DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)
_CACHE_NAME = "graph_seed_edges.pkl"
_CACHE_VERSION = 2


def invalidate_seed_edge_cache(repo_path: Any) -> None:
    """Remove a snapshot before an update can change the authoritative DB.

    If the process exits after the DB commit but before writing a replacement,
    the next update must rebuild from current sources rather than trust a
    pre-commit snapshot.
    """
    path = Path(repo_path) / ".repowise" / _CACHE_NAME
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("graph_seed_cache_invalidate_failed", path=str(path), error=str(exc))


async def _database_stamp(repo_path: Path) -> tuple[str, int, str] | None:
    from sqlalchemy import func, select

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import GraphEdge, Repository

    engine = create_engine(resolve_db_url(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo_id = (
                await session.execute(
                    select(Repository.id).where(
                        Repository.local_path == str(repo_path.resolve())
                    )
                )
            ).scalar_one_or_none()
            if repo_id is None:
                return None
            edge_count = await session.scalar(
                select(func.count(GraphEdge.id)).where(GraphEdge.repository_id == repo_id)
            )
            fingerprint = (
                await session.execute(
                    select(Repository.graph_edges_parser_fingerprint).where(
                        Repository.id == repo_id
                    )
                )
            ).scalar_one_or_none()
            return repo_id, int(edge_count or 0), fingerprint or ""
    finally:
        await engine.dispose()


async def load_seed_edges(repo_path: Any) -> dict[str, list] | None:
    """The full persisted graph snapshot, or ``None`` to force a full build."""
    root = Path(repo_path).resolve()
    stamp = await _database_stamp(root)
    if stamp is None or stamp[1] == 0:
        return None
    repo_id, edge_count, fingerprint = stamp
    from repowise.core.ingestion.parse_cache import parser_fingerprint

    if not fingerprint or fingerprint != parser_fingerprint():
        log.info("graph_seed_cache_skipped", reason="edge parser fingerprint mismatch")
        return None
    cache_path = root / ".repowise" / _CACHE_NAME
    try:
        cache = load_sealed_pickle(cache_path, domain=_CACHE_NAME)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("graph_seed_cache_unavailable", error=str(exc))
        return None
    if not isinstance(cache, dict):
        log.info("graph_seed_cache_skipped", reason="snapshot payload is not a mapping")
        return None
    expected = (
        ("version", _CACHE_VERSION),
        ("repo_path", str(root)),
        ("repo_id", repo_id),
        ("edge_count", edge_count),
        ("parser_fingerprint", fingerprint),
    )
    for key, value in expected:
        if cache.get(key) != value:
            log.info("graph_seed_cache_skipped", reason=f"snapshot {key} mismatch")
            return None
    for key in ("nodes", "edges"):
        if not isinstance(cache.get(key), list):
            log.info("graph_seed_cache_skipped", reason=f"snapshot {key} payload is invalid")
            return None
    log.info("graph_seed_cache_hit", nodes=len(cache["nodes"]), edges=len(cache["edges"]))
    return {"nodes": cache["nodes"], "edges": cache["edges"]}


async def save_seed_edge_cache(repo_path: Any, graph_builder: Any) -> None:
    """Save a sealed full graph snapshot after the database commit succeeds."""
    from repowise.core.ingestion.parse_cache import parser_fingerprint

    root = Path(repo_path).resolve()
    try:
        stamp = await _database_stamp(root)
        if stamp is None:
            return
        repo_id, edge_count, stored_fingerprint = stamp
        graph = graph_builder.graph()
        payload = {
            "version": _CACHE_VERSION,
            "repo_path": str(root),
            "repo_id": repo_id,
            "edge_count": edge_count,
            "parser_fingerprint": stored_fingerprint or parser_fingerprint(),
            "nodes": [(node, dict(attrs)) for node, attrs in graph.nodes(data=True)],
            "edges": [(source, target, dict(attrs)) for source, target, attrs in graph.edges(data=True)],
        }
        if len(payload["edges"]) != edge_count:
            log.warning(
                "graph_seed_cache_edge_count_mismatch",
                graph_edges=len(payload["edges"]),
                db_edges=edge_count,
            )
            return
        dump_sealed_pickle(root / ".repowise" / _CACHE_NAME, payload, domain=_CACHE_NAME)
        log.info("graph_seed_cache_saved", nodes=len(payload["nodes"]), edges=edge_count)
    except Exception as exc:
        # The cache is optional; the next update can always rebuild from sources.
        log.warning("graph_seed_cache_save_failed", error=str(exc))
