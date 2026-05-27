"""Per-node cross-link signal collection — a single reusable join layer.

Joins git metadata, dead-code findings, decisions, and docs into one map so
the graph endpoints can annotate nodes without N round-trips, and converts a
``GraphNode`` + its signals into the various node response models.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    GraphNode,
    Page,
)
from repowise.server.schemas import GraphNodeResponse


class NodeSignals:
    """Per-node cross-link signals (git, dead-code, decisions, docs)."""

    __slots__ = (
        "churn_percentile",
        "dead_confidence",
        "has_decision",
        "has_doc",
        "is_dead",
        "is_hotspot",
        "primary_owner",
    )

    def __init__(self) -> None:
        self.is_hotspot: bool = False
        self.churn_percentile: float | None = None
        self.is_dead: bool = False
        self.dead_confidence: float | None = None
        self.has_decision: bool = False
        self.primary_owner: str | None = None
        self.has_doc: bool = False


_EMPTY_SIGNALS = NodeSignals()


async def _collect_node_signals(
    session: AsyncSession,
    repo_id: str,
    node_ids: list[str] | None = None,
) -> dict[str, NodeSignals]:
    """Join git metadata, dead-code findings, decisions, and docs into one map.

    When *node_ids* is provided, queries are scoped to that subset; otherwise
    we fetch repo-wide. Returns ``{node_id: NodeSignals}``. Missing keys imply
    default (all-false) signals.
    """
    signals: dict[str, NodeSignals] = {}

    def _entry(path: str) -> NodeSignals:
        existing = signals.get(path)
        if existing is None:
            existing = NodeSignals()
            signals[path] = existing
        return existing

    # Git metadata: hotspot + churn + owner
    git_q = select(GitMetadata).where(GitMetadata.repository_id == repo_id)
    if node_ids is not None:
        git_q = git_q.where(GitMetadata.file_path.in_(node_ids))
    for gm in (await session.execute(git_q)).scalars():
        s = _entry(gm.file_path)
        s.is_hotspot = bool(gm.is_hotspot)
        s.churn_percentile = gm.churn_percentile
        s.primary_owner = gm.primary_owner_name

    # Dead-code findings (only open, unreachable_file)
    dead_q = select(DeadCodeFinding).where(
        DeadCodeFinding.repository_id == repo_id,
        DeadCodeFinding.status == "open",
        DeadCodeFinding.kind == "unreachable_file",
    )
    if node_ids is not None:
        dead_q = dead_q.where(DeadCodeFinding.file_path.in_(node_ids))
    for f in (await session.execute(dead_q)).scalars():
        s = _entry(f.file_path)
        s.is_dead = True
        s.dead_confidence = f.confidence

    # Decisions: each decision lists affected files in JSON
    dec_q = select(DecisionRecord.affected_files_json).where(
        DecisionRecord.repository_id == repo_id,
        DecisionRecord.status.in_(("active", "proposed")),
    )
    decision_paths: set[str] = set()
    for (raw,) in (await session.execute(dec_q)).all():
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, list):
            for p in parsed:
                if isinstance(p, str):
                    decision_paths.add(p)
    if node_ids is not None:
        decision_paths &= set(node_ids)
    for p in decision_paths:
        _entry(p).has_decision = True

    # Docs (Page.target_path)
    doc_q = select(Page.target_path).where(Page.repository_id == repo_id)
    if node_ids is not None:
        doc_q = doc_q.where(Page.target_path.in_(node_ids))
    for (path,) in (await session.execute(doc_q)).all():
        if path:
            _entry(path).has_doc = True

    return signals


_NodeResponseT = TypeVar("_NodeResponseT")


def _node_to_response(
    n: GraphNode,
    sig: NodeSignals,
    response_cls: type[_NodeResponseT],
    **extra: Any,
) -> _NodeResponseT:
    """Build a node response model from a GraphNode + its signals.

    The 16 shared fields are filled identically for every node response shape;
    *extra* carries the per-shape fields (``confidence_group``, ``commit_count``).
    """
    return response_cls(
        node_id=n.node_id,
        node_type=n.node_type,
        language=n.language,
        symbol_count=n.symbol_count,
        pagerank=n.pagerank,
        betweenness=n.betweenness,
        community_id=n.community_id,
        is_test=n.is_test,
        is_entry_point=n.is_entry_point,
        has_doc=sig.has_doc,
        is_hotspot=sig.is_hotspot,
        churn_percentile=sig.churn_percentile,
        is_dead=sig.is_dead,
        dead_confidence=sig.dead_confidence,
        has_decision=sig.has_decision,
        primary_owner=sig.primary_owner,
        **extra,
    )


def _to_graph_node(n: GraphNode, signals: NodeSignals) -> GraphNodeResponse:
    """Build a GraphNodeResponse from a GraphNode + its collected signals."""
    return _node_to_response(n, signals, GraphNodeResponse)
