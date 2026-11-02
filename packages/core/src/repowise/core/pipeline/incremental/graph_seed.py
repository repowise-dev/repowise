"""Read and cache the persisted graph snapshot reused by a scoped update.

Loading hundreds of thousands of ORM edge rows from SQLite is expensive on a
cold cache. After a successful index, the complete NetworkX node/edge snapshot
is sealed locally for a sequential read. It is only a hint: repository identity,
DB edge count and parser fingerprint must match. Missing/stale snapshots cause
a full graph rebuild; SQL rows are never used as a lossy substitute for graph
attributes that are not persisted in the DB.
"""

from __future__ import annotations

import json
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


def _incremental_cache_is_enabled(root: Path) -> bool:
    """Whether the last persisted index run opted in to incremental graph state."""
    try:
        state = json.loads((root / ".repowise" / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    graph_update = state.get("graph_update")
    return isinstance(graph_update, dict) and graph_update.get("enabled") is True


def _seed_identity_matches(
    cache: Any,
    *,
    root: Path,
    repo_id: str,
    edge_count: int,
    fingerprint: str,
) -> bool:
    """Validate the cache schema and the DB identity/stamp fields."""
    if not isinstance(cache, dict):
        return False
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
            return False
    for key in ("nodes", "edges"):
        if not isinstance(cache.get(key), list):
            log.info("graph_seed_cache_skipped", reason=f"snapshot {key} payload is invalid")
            return False
    return True


async def load_seed_edges(repo_path: Any) -> dict[str, list] | None:
    """The full persisted graph snapshot, or ``None`` to force a full build."""
    root = Path(repo_path).resolve()
    if not _incremental_cache_is_enabled(root):
        log.info(
            "graph_seed_cache_skipped",
            reason="incremental graph feature was not enabled on the last index",
        )
        return None

    stamp = await _database_stamp(root)
    if stamp is None:
        log.info("graph_seed_cache_skipped", reason="repository is not indexed")
        return None
    if stamp[1] == 0:
        log.info("graph_seed_cache_skipped", reason="repository has no persisted graph edges")
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
    if not _seed_identity_matches(
        cache,
        root=root,
        repo_id=repo_id,
        edge_count=edge_count,
        fingerprint=fingerprint,
    ):
        return None
    log.info("graph_seed_cache_hit", nodes=len(cache["nodes"]), edges=len(cache["edges"]))
    return {"nodes": cache["nodes"], "edges": cache["edges"]}


async def load_seed_edges_for_reconciliation(repo_path: Any) -> dict[str, list] | None:
    """Load a DB-current snapshot for preserving rows omitted by a full build.

    Unlike :func:`load_seed_edges`, this does not require the current parser
    fingerprint or a prior opt-in. The snapshot is never used to build the
    graph; it is only a source of attributes for rows the DB deliberately kept
    when a file could not be read or parsed.
    """
    root = Path(repo_path).resolve()
    stamp = await _database_stamp(root)
    if stamp is None or stamp[1] == 0:
        return None
    cache_path = root / ".repowise" / _CACHE_NAME
    try:
        cache = load_sealed_pickle(cache_path, domain=_CACHE_NAME)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("graph_seed_reconciliation_unavailable", error=str(exc))
        return None
    if not _seed_identity_matches(
        cache,
        root=root,
        repo_id=stamp[0],
        edge_count=stamp[1],
        fingerprint=stamp[2],
    ):
        return None
    return {"nodes": cache["nodes"], "edges": cache["edges"]}


async def _persisted_graph_ids(
    repo_path: Path, repo_id: str
) -> tuple[set[str], set[tuple[str, str, str]]]:
    from sqlalchemy import select

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import GraphEdge, GraphNode

    engine = create_engine(resolve_db_url(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            edge_result = await session.stream(
                select(
                    GraphEdge.source_node_id,
                    GraphEdge.target_node_id,
                    GraphEdge.edge_type,
                ).where(GraphEdge.repository_id == repo_id)
            )
            edge_ids = {
                (source, target, edge_type)
                async for source, target, edge_type in edge_result
            }
            await edge_result.close()
            node_result = await session.stream(
                select(GraphNode.node_id).where(GraphNode.repository_id == repo_id)
            )
            node_ids = {node_id async for node_id, in node_result}
            await node_result.close()
            return node_ids, edge_ids
    finally:
        await engine.dispose()


def _unparsed_snapshot_rows(
    previous_snapshot: dict[str, list], unparsed_paths: set[str]
) -> tuple[dict[str, dict], list[tuple]]:
    previous_nodes = {
        node_id: attrs
        for node_id, attrs in previous_snapshot["nodes"]
        if attrs.get("file_path") in unparsed_paths
    }
    previous_edges = [
        (source, target, attrs)
        for source, target, attrs in previous_snapshot["edges"]
        if source in previous_nodes
    ]
    return previous_nodes, previous_edges


def _restore_edge_endpoints(
    graph: Any,
    previous_snapshot: dict[str, list],
    previous_nodes: dict[str, dict],
    previous_edges: list[tuple],
) -> set[str] | None:
    required_nodes = {
        node_id
        for source, target, _attrs in previous_edges
        for node_id in (source, target)
        if node_id not in graph
    }
    if required_nodes - previous_nodes.keys():
        previous_nodes.update(
            (node_id, attrs)
            for node_id, attrs in previous_snapshot["nodes"]
            if node_id in required_nodes
        )
    if required_nodes - previous_nodes.keys():
        log.warning(
            "graph_seed_unparsed_rows_unavailable",
            missing_nodes=len(required_nodes - previous_nodes.keys()),
        )
        return None

    for source, target, attrs in previous_edges:
        existing = graph.get_edge_data(source, target)
        if existing is not None and existing.get("edge_type") != attrs.get("edge_type"):
            log.warning(
                "graph_seed_unparsed_edge_conflict",
                source=source,
                target=target,
            )
            return None
    return required_nodes


def restore_unparsed_graph_rows(
    graph_builder: Any,
    previous_snapshot: dict[str, list],
    parsed_paths: set[str],
) -> None:
    """Keep prior graph rows for traversed files the full build could not parse."""
    graph = graph_builder.graph()
    unparsed_paths = set(getattr(graph_builder, "traversed_file_paths", set())) - parsed_paths
    if not unparsed_paths:
        return
    previous_nodes, previous_edges = _unparsed_snapshot_rows(previous_snapshot, unparsed_paths)
    if not previous_edges:
        return
    required_nodes = _restore_edge_endpoints(graph, previous_snapshot, previous_nodes, previous_edges)
    if required_nodes is None:
        return

    for node_id in required_nodes:
        graph.add_node(node_id, **previous_nodes[node_id])
    restored_edges = 0
    for source, target, attrs in previous_edges:
        existing = graph.get_edge_data(source, target)
        if existing is not None:
            continue
        graph.add_edge(source, target, **attrs)
        restored_edges += 1
    if restored_edges:
        log.info(
            "graph_seed_unparsed_rows_restored",
            files=len(unparsed_paths),
            edges=restored_edges,
        )


def _snapshot_rows_for_ids(
    previous_snapshot: dict[str, list],
    missing_node_ids: set[str],
    missing_edge_ids: set[tuple[str, str, str]],
) -> tuple[dict[str, dict], dict[tuple[str, str, str], tuple]] | None:
    previous_nodes = {
        node_id: attrs
        for node_id, attrs in previous_snapshot["nodes"]
        if node_id in missing_node_ids
    }
    previous_edges = {
        (source, target, attrs.get("edge_type", "imports")): (source, target, attrs)
        for source, target, attrs in previous_snapshot["edges"]
        if (source, target, attrs.get("edge_type", "imports")) in missing_edge_ids
    }
    absent_nodes = missing_node_ids - previous_nodes.keys()
    absent_edges = missing_edge_ids - previous_edges.keys()
    if absent_nodes or absent_edges:
        log.warning(
            "graph_seed_cache_rows_unavailable",
            missing_edges=len(absent_edges),
            missing_nodes=len(absent_nodes),
        )
        return None
    return previous_nodes, previous_edges


def _restore_snapshot_rows(
    graph: Any,
    previous_snapshot: dict[str, list],
    missing_node_ids: set[str],
    missing_edge_ids: set[tuple[str, str, str]],
) -> bool:
    rows = _snapshot_rows_for_ids(previous_snapshot, missing_node_ids, missing_edge_ids)
    if rows is None:
        return False
    previous_nodes, previous_edges = rows
    for node_id in missing_node_ids:
        graph.add_node(node_id, **previous_nodes[node_id])
    for source, target, attrs in previous_edges.values():
        graph.add_edge(source, target, **attrs)
    return True


async def _restore_persisted_graph_rows(
    repo_path: Path,
    repo_id: str,
    graph: Any,
    previous_snapshot: dict[str, list] | None,
) -> bool:
    """Restore DB-retained rows from a verified prior snapshot after a full build."""
    node_ids, edge_ids = await _persisted_graph_ids(repo_path, repo_id)
    current_edge_ids = {
        (source, target, attrs.get("edge_type", "imports"))
        for source, target, attrs in graph.edges(data=True)
    }
    missing_edge_ids = edge_ids - current_edge_ids
    missing_node_ids = node_ids - set(graph.nodes)
    if not missing_edge_ids and not missing_node_ids:
        return current_edge_ids == edge_ids
    if previous_snapshot is None:
        log.warning(
            "graph_seed_cache_rows_unavailable",
            missing_edges=len(missing_edge_ids),
            missing_nodes=len(missing_node_ids),
        )
        return False
    if not _restore_snapshot_rows(graph, previous_snapshot, missing_node_ids, missing_edge_ids):
        return False

    restored_edge_ids = {
        (source, target, attrs.get("edge_type", "imports"))
        for source, target, attrs in graph.edges(data=True)
    }
    if restored_edge_ids != edge_ids:
        log.warning(
            "graph_seed_cache_rows_mismatch",
            graph_edges=len(restored_edge_ids),
            db_edges=len(edge_ids),
        )
        return False
    return True


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
            previous_snapshot = getattr(graph_builder, "previous_seed_edges", None)
            if previous_snapshot is None or not await _restore_persisted_graph_rows(
                root, repo_id, graph, previous_snapshot
            ):
                log.warning(
                    "graph_seed_cache_edge_count_mismatch",
                    graph_edges=graph.number_of_edges(),
                    db_edges=edge_count,
                )
                return
            payload["nodes"] = [(node, dict(attrs)) for node, attrs in graph.nodes(data=True)]
            payload["edges"] = [
                (source, target, dict(attrs)) for source, target, attrs in graph.edges(data=True)
            ]
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
