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
