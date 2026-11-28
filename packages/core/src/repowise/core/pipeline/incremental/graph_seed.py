"""Read and cache the persisted graph snapshot reused by a scoped update.

Loading hundreds of thousands of ORM edge rows from SQLite is expensive on a
cold cache. After a successful index, the complete NetworkX node/edge snapshot
is sealed locally for a sequential read. It is only a hint: repository identity,
DB edge count and parser fingerprint must match. Missing/stale snapshots cause
a full graph rebuild; SQL rows are never used as a lossy substitute for graph
attributes that are not persisted in the DB.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)
_CACHE_NAME = "graph_seed_edges.pkl"
_CACHE_VERSION = 2
# The loaded snapshot, set aside for the length of one update
# (:func:`invalidate_seed_edge_cache`). A work file, not a cache: nothing
# carries it between runs, and every update removes it.
_KEPT_NAME = _CACHE_NAME + ".kept"


def invalidate_seed_edge_cache(repo_path: Any, *, keep: bool = False) -> bool:
    """Remove a snapshot before an update can change the authoritative DB.

    If the process exits after the DB commit but before writing a replacement,
    the next update must rebuild from current sources rather than trust a
    pre-commit snapshot. With *keep*, the snapshot the update just loaded is
    moved aside instead, where no load reads it: an update whose graph comes
    out equal to it moves it back rather than writing the same bytes again
    (:func:`save_seed_edge_cache`). Returns whether this call moved it aside;
    any earlier set-aside file is removed first, so a later move back can only
    restore what this run loaded.
    """
    index = Path(repo_path) / ".repowise"
    path = index / _CACHE_NAME
    kept = index / _KEPT_NAME
    discard_set_aside_seed(repo_path)
    # A leftover that could not be removed is never overwritten or trusted.
    if keep and path.is_file() and not kept.exists():
        try:
            os.replace(path, kept)
            return True
        except OSError as exc:
            log.warning("graph_seed_cache_keep_failed", path=str(path), error=str(exc))
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("graph_seed_cache_invalidate_failed", path=str(path), error=str(exc))
    return False


async def _database_stamp(repo_path: Path) -> tuple[str, int, str] | None:
    """The repository's id, edge count and parser fingerprint, or ``None``.

    The snapshot is only a hint, so a database that cannot answer means a full
    build, never a failed update.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        return await _query_database_stamp(repo_path)
    except (SQLAlchemyError, OSError) as exc:
        log.warning("graph_seed_stamp_unavailable", error=str(exc))
        return None


async def _query_database_stamp(repo_path: Path) -> tuple[str, int, str] | None:
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


async def _external_calls_current(repo_path: Path, repo_id: str) -> bool:
    """Whether the stored ``external_call_sites`` hold the current version.

    A database that cannot answer reads as not current: a full build, never a
    failed update.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import external_call_sites_complete
    from repowise.core.persistence.database import resolve_db_url

    try:
        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                return await external_call_sites_complete(session, repo_id)
        finally:
            await engine.dispose()
    except (SQLAlchemyError, OSError) as exc:
        log.warning("graph_seed_external_calls_unavailable", error=str(exc))
        return False


def _incremental_cache_is_enabled(root: Path) -> bool:
    """Whether the last persisted index run had incremental graph state enabled."""
    try:
        state = json.loads((root / ".repowise" / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    graph_update = state.get("graph_update")
    return isinstance(graph_update, dict) and graph_update.get("enabled") is True


def _seed_identity_matches(
    cache: Any,
    *,
    repo_id: str,
    edge_count: int,
    fingerprint: str,
) -> bool:
    """Validate the cache schema and the DB identity/stamp fields.

    Not keyed on the checkout's absolute path: node ids are repo-relative, so
    a repo moved or copied with its ``.repowise`` keeps a valid snapshot. The
    repository row id still ties the snapshot to its own store.
    """
    if not isinstance(cache, dict):
        return False
    expected = (
        ("version", _CACHE_VERSION),
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
    if len(cache["edges"]) != edge_count:
        log.info("graph_seed_cache_skipped", reason="snapshot edges do not match the stored count")
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
    # A patch re-resolves only its scope, so it can only maintain a package-call
    # table some full build already filled at this version.
    if not await _external_calls_current(root, repo_id):
        log.info("graph_seed_cache_skipped", reason="external call sites are not current")
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
    fingerprint or that the last run had the feature enabled. The snapshot is never used to build the
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


def _graph_matches_snapshot(graph: Any, snapshot: dict[str, list]) -> bool:
    """Whether *graph* holds exactly the snapshot's nodes and edges, attributes included.

    Order is not compared: no reader of the snapshot depends on it.
    """
    nodes, edges = snapshot["nodes"], snapshot["edges"]
    if len(nodes) != graph.number_of_nodes() or len(edges) != graph.number_of_edges():
        return False
    graph_nodes = graph.nodes
    if any(node not in graph_nodes or graph_nodes[node] != attrs for node, attrs in nodes):
        return False
    return all(graph.get_edge_data(source, target) == attrs for source, target, attrs in edges)


_HEADER_KEYS = ("version", "repo_id", "edge_count", "parser_fingerprint")


@functools.cache
def _metric_memo_code() -> str:
    """Hash of the code a stored metric memo, and the node rows built from it, depend on.

    A memo is only replayed under the same hash, so an upgrade or an edit to a
    metric kernel, the community or flow analysis, or the node row writer
    computes and writes afresh.
    """
    from importlib.metadata import version

    from repowise.core import (
        __version__,
        entry_candidacy,
        ids,
        support_paths,
        test_paths,
    )
    from repowise.core.analysis import execution_flows, execution_graph
    from repowise.core.analysis.communities import community_code_fingerprint
    from repowise.core.ingestion import cohesion, symbol_identity, symbol_use
    from repowise.core.ingestion.graph import (
        _betweenness,
        _centrality_cache,
        _metrics,
        _pagerank,
        _sampled_betweenness,
    )
    from repowise.core.persistence.crud import graph as crud_graph
    from repowise.core.pipeline.persist import graph as persist_graph

    digest = hashlib.sha256(
        "|".join(
            (__version__, community_code_fingerprint(), version("numpy"))
        ).encode()
    )
    for module in (
        _metrics,
        _pagerank,
        _betweenness,
        _sampled_betweenness,
        _centrality_cache,
        cohesion,
        symbol_identity,
        symbol_use,
        execution_flows,
        execution_graph,
        ids,
        test_paths,
        support_paths,
        entry_candidacy,
        persist_graph,
        crud_graph,
    ):
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()


def discard_snapshot_metrics(repo_path: Any) -> None:
    """Drop the metric memo from the stored snapshot, keeping its graph.

    For a writer of graph node rows that saves no snapshot (the config-only
    health re-score): the memo stands for the rows the store holds, so after
    such a write the next update must compute and write them.
    """
    path = Path(repo_path).resolve() / ".repowise" / _CACHE_NAME
    try:
        cache = load_sealed_pickle(path, domain=_CACHE_NAME)
        if isinstance(cache, dict) and cache.pop("metrics", None) is not None:
            dump_sealed_pickle(path, cache, domain=_CACHE_NAME)
    except FileNotFoundError:
        return
    except Exception as exc:
        log.warning("graph_seed_cache_metrics_discard_failed", error=str(exc))
        # A snapshot that may still carry a memo is not left behind.
        invalidate_seed_edge_cache(repo_path)


def confirm_kept_snapshot(repo_path: Any, graph_builder: Any) -> Any | None:
    """Compare the built graph with the snapshot this run set aside, once per update.

    Records the answer on the builder as ``kept_snapshot_match``, which
    :func:`save_seed_edge_cache` reads instead of comparing again. On a match,
    returns the metric memo stored with the snapshot when this code may replay
    it, else ``None``.

    Only a file this run moved aside (``seed_snapshot_kept``) is trusted. The
    graph is first compared with the snapshot in memory, which is cheap; a
    mismatch there is final. A match is confirmed against the file itself: the
    seeded graph shares list and set values with the in-memory snapshot, so a
    pass that edited one in place changed both.
    """
    graph_builder.kept_snapshot_match = False
    previous = getattr(graph_builder, "previous_seed_edges", None)
    if getattr(graph_builder, "seed_snapshot_kept", False) is not True or previous is None:
        return None
    kept = Path(repo_path).resolve() / ".repowise" / _KEPT_NAME
    graph = graph_builder.graph()
    if not kept.is_file() or not _graph_matches_snapshot(graph, previous):
        return None
    try:
        cache = load_sealed_pickle(kept, domain=_CACHE_NAME)
    except Exception as exc:
        log.warning("graph_seed_cache_unavailable", error=str(exc))
        return None
    if not (
        isinstance(cache, dict)
        and isinstance(cache.get("nodes"), list)
        and isinstance(cache.get("edges"), list)
        and _graph_matches_snapshot(graph, cache)
    ):
        return None
    graph_builder.kept_snapshot_match = True
    graph_builder.kept_snapshot_header = {key: cache.get(key) for key in _HEADER_KEYS}
    memo = cache.get("metrics")
    graph_builder.kept_snapshot_has_memo = memo is not None
    if not isinstance(memo, dict) or memo.get("code") != _metric_memo_code():
        return None
    return memo


def _metric_memo(graph_builder: Any) -> dict[str, Any] | None:
    """The memo to store with the snapshot, or ``None`` when the store may not match it.

    A memo stands for the graph node rows the store holds, so it is written
    only when this run's rows came from the builder's own metrics
    (``graph_nodes_written``, set by ``persist_graph_nodes``), computed on the
    graph the snapshot holds (``metrics_on_final_graph``, set by an update's
    rebuild).
    """
    if (
        getattr(graph_builder, "graph_nodes_written", False) is not True
        or getattr(graph_builder, "metrics_on_final_graph", False) is not True
    ):
        return None
    memo_of = getattr(graph_builder, "metric_memo", None)
    memo = memo_of() if memo_of is not None else None
    if memo is None:
        return None
    return {"code": _metric_memo_code(), **memo}


async def save_seed_edge_cache(repo_path: Any, graph_builder: Any) -> None:
    """Save a sealed full graph snapshot after the database commit succeeds.

    The snapshot carries the run's global metrics, which an update whose graph
    comes out the same replays (:func:`confirm_kept_snapshot`). An update
    whose graph equals the snapshot it loaded, and that replayed its metrics,
    puts that file back instead of writing it again.
    """
    from repowise.core.ingestion.parse_cache import parser_fingerprint

    root = Path(repo_path).resolve()
    kept = root / ".repowise" / _KEPT_NAME
    try:
        stamp = await _database_stamp(root)
        if stamp is None:
            return
        repo_id, edge_count, stored_fingerprint = stamp
        header = {
            "version": _CACHE_VERSION,
            "repo_id": repo_id,
            "edge_count": edge_count,
            "parser_fingerprint": stored_fingerprint or parser_fingerprint(),
        }
        graph = graph_builder.graph()
        memo = _metric_memo(graph_builder)
        previous = getattr(graph_builder, "previous_seed_edges", None) or {}
        unchanged = (
            getattr(graph_builder, "kept_snapshot_match", False) is True
            and getattr(graph_builder, "kept_snapshot_header", None) == header
            and kept.is_file()
            # Compared in full during the rebuild, which no later step edits;
            # the counts are the cheap guard that it still holds.
            and len(previous.get("nodes", ())) == graph.number_of_nodes()
            and len(previous.get("edges", ())) == graph.number_of_edges()
        )
        # The kept file's memo is current when this run replayed it, or when
        # neither run has one.
        if unchanged and (
            getattr(graph_builder, "graph_metrics_reused", False) is True
            or (memo is None and not getattr(graph_builder, "kept_snapshot_has_memo", False))
        ):
            os.replace(kept, root / ".repowise" / _CACHE_NAME)
            log.info("graph_seed_cache_unchanged", edges=edge_count)
            return
        payload = {
            **header,
            "nodes": [(node, dict(attrs)) for node, attrs in graph.nodes(data=True)],
            "edges": [
                (source, target, dict(attrs)) for source, target, attrs in graph.edges(data=True)
            ],
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
            # The memo was computed on the graph before these rows came back.
            memo = None
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
        if memo is not None:
            payload["metrics"] = memo
        dump_sealed_pickle(root / ".repowise" / _CACHE_NAME, payload, domain=_CACHE_NAME)
        log.info("graph_seed_cache_saved", nodes=len(payload["nodes"]), edges=edge_count)
    except Exception as exc:
        # The cache is optional; the next update can always rebuild from sources.
        log.warning("graph_seed_cache_save_failed", error=str(exc))
    finally:
        discard_set_aside_seed(root)


def discard_set_aside_seed(repo_path: Any) -> None:
    """Remove the snapshot an update set aside, once nothing will move it back."""
    kept = Path(repo_path) / ".repowise" / _KEPT_NAME
    try:
        kept.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("graph_seed_cache_invalidate_failed", path=str(kept), error=str(exc))
