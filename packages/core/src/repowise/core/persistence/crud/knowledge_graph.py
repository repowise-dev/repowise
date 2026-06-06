"""CRUD operations for the knowledge graph domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    KnowledgeGraphLayer,
    KnowledgeGraphNodeMeta,
    KnowledgeGraphProjectMeta,
    KnowledgeGraphTourStep,
)

# ---------------------------------------------------------------------------
# Knowledge Graph layers & tour steps
# ---------------------------------------------------------------------------


async def upsert_kg_layers(session: AsyncSession, repo_id: str, layers: list[dict]) -> None:
    """Replace all KG layers for a repo (delete + bulk insert)."""
    await session.execute(
        delete(KnowledgeGraphLayer).where(KnowledgeGraphLayer.repository_id == repo_id)
    )
    for i, layer in enumerate(layers):
        session.add(
            KnowledgeGraphLayer(
                repository_id=repo_id,
                layer_id=layer["id"],
                name=layer["name"],
                description=layer.get("description", ""),
                node_ids_json=json.dumps(layer.get("nodeIds", layer.get("node_ids", []))),
                display_order=layer.get("display_order", i),
                sub_groups_json=json.dumps(layer.get("subGroups", layer.get("sub_groups", []))),
            )
        )
    await session.flush()


async def get_kg_layers(session: AsyncSession, repo_id: str) -> list[KnowledgeGraphLayer]:
    """Fetch all KG layers ordered by display_order."""
    result = await session.execute(
        select(KnowledgeGraphLayer)
        .where(KnowledgeGraphLayer.repository_id == repo_id)
        .order_by(KnowledgeGraphLayer.display_order)
    )
    return list(result.scalars())


async def upsert_kg_tour_steps(session: AsyncSession, repo_id: str, steps: list[dict]) -> None:
    """Replace all KG tour steps for a repo (delete + bulk insert)."""
    await session.execute(
        delete(KnowledgeGraphTourStep).where(KnowledgeGraphTourStep.repository_id == repo_id)
    )
    for step in steps:
        session.add(
            KnowledgeGraphTourStep(
                repository_id=repo_id,
                step_order=step["order"],
                title=step["title"],
                description=step.get("description", ""),
                node_ids_json=json.dumps(step.get("nodeIds", step.get("node_ids", []))),
                target_path=step.get("target_path"),
                layer_id=step.get("layer_id"),
                reason=step.get("reason", ""),
                depth=step.get("depth"),
                kind=step.get("kind", ""),
                page_type=step.get("page_type"),
            )
        )
    await session.flush()


async def get_kg_tour_steps(session: AsyncSession, repo_id: str) -> list[KnowledgeGraphTourStep]:
    """Fetch all KG tour steps ordered by step_order."""
    result = await session.execute(
        select(KnowledgeGraphTourStep)
        .where(KnowledgeGraphTourStep.repository_id == repo_id)
        .order_by(KnowledgeGraphTourStep.step_order)
    )
    return list(result.scalars())


async def upsert_kg_project_meta(
    session: AsyncSession,
    repo_id: str,
    entry_points: list[str],
    entry_candidates: list[str] | None = None,
) -> None:
    """Replace the project-level curated KG metadata for a repo (one row)."""
    await session.execute(
        delete(KnowledgeGraphProjectMeta).where(KnowledgeGraphProjectMeta.repository_id == repo_id)
    )
    session.add(
        KnowledgeGraphProjectMeta(
            repository_id=repo_id,
            entry_points_json=json.dumps(entry_points),
            entry_candidates_json=json.dumps(entry_candidates or []),
        )
    )
    await session.flush()


async def get_kg_project_meta(
    session: AsyncSession, repo_id: str
) -> KnowledgeGraphProjectMeta | None:
    """Fetch the project-level curated KG metadata row, if any."""
    result = await session.execute(
        select(KnowledgeGraphProjectMeta).where(KnowledgeGraphProjectMeta.repository_id == repo_id)
    )
    return result.scalars().first()


def file_node_meta_from_kg_nodes(nodes: list[dict]) -> list[dict]:
    """Extract per-file curated metadata from exported KG ``nodes``.

    Keeps only ``file:``-prefixed nodes and strips the prefix so the stored
    ``node_id`` matches the architecture view's plain repo-relative paths.
    Shared by the pipeline persister and the on-read file → DB migration.
    """
    return [
        {
            "node_id": node["id"].removeprefix("file:"),
            "node_type": node.get("type", "file"),
            "summary": node.get("summary", ""),
            "tags": node.get("tags", []),
        }
        for node in nodes
        if isinstance(node.get("id"), str) and node["id"].startswith("file:")
    ]


async def upsert_kg_node_meta(session: AsyncSession, repo_id: str, nodes: list[dict]) -> None:
    """Replace all per-node curated KG metadata for a repo (delete + bulk insert).

    Each *node* dict carries ``id``/``node_id``, optional ``type``/``node_type``,
    ``summary`` and ``tags``; ids are stored verbatim (callers strip prefixes).
    """
    await session.execute(
        delete(KnowledgeGraphNodeMeta).where(KnowledgeGraphNodeMeta.repository_id == repo_id)
    )
    for node in nodes:
        session.add(
            KnowledgeGraphNodeMeta(
                repository_id=repo_id,
                node_id=node.get("node_id", node.get("id", "")),
                node_type=node.get("node_type", node.get("type", "file")),
                summary=node.get("summary", ""),
                tags_json=json.dumps(node.get("tags", [])),
            )
        )
    await session.flush()


async def get_kg_node_meta(session: AsyncSession, repo_id: str) -> list[KnowledgeGraphNodeMeta]:
    """Fetch all per-node curated KG metadata rows for a repo."""
    result = await session.execute(
        select(KnowledgeGraphNodeMeta).where(KnowledgeGraphNodeMeta.repository_id == repo_id)
    )
    return list(result.scalars())
