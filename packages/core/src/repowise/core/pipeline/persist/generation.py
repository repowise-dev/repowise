"""Generation-phase persistence: wiki pages and knowledge-graph layers."""

from __future__ import annotations

from typing import Any

import structlog

# Patchable steps are looked up on the package at call time, so a patch on
# ``repowise.core.pipeline.persist`` reaches this module too.
from repowise.core.pipeline import persist as _persist

logger = structlog.get_logger("repowise.core.pipeline.persist")


async def persist_generation(result: Any, session: Any, repo_id: str) -> None:
    """Persist generation-phase outputs: wiki pages and knowledge-graph layers.

    Pages upsert per ``page_id`` (archiving prior versions); KG layers/tour
    are full-replace. Both safe to call incrementally / on resume.
    """
    from repowise.core.persistence import upsert_pages_from_generated

    # ---- Pages (if generated) -----------------------------------------------
    # Batched: one SELECT + one flush instead of a SELECT+flush per page. The
    # per-page durability sink already streamed these during generation; this
    # end-of-run pass flushes the post-generation metadata enrichment. See
    # upsert_pages_from_generated for the equivalence contract.
    if result.generated_pages:
        await upsert_pages_from_generated(session, result.generated_pages, repo_id)

    # ---- Knowledge graph layers, tour steps & curated meta ------------------
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        await _persist.persist_kg(kg, session, repo_id)


async def persist_kg(kg: Any, session: Any, repo_id: str) -> None:
    """Persist knowledge-graph layers, tour steps, and curated meta.

    Full-replace semantics, safe to call incrementally — shared by the full
    pipeline (:func:`persist_generation`) and the incremental update path so
    a refreshed KG lands through the same writers.
    """
    from repowise.core.persistence.crud import (
        file_node_meta_from_kg_nodes,
        upsert_kg_layers,
        upsert_kg_node_meta,
        upsert_kg_project_meta,
        upsert_kg_tour_steps,
    )

    if hasattr(kg, "layers") and kg.layers:
        await upsert_kg_layers(session, repo_id, kg.layers)
    if hasattr(kg, "tour") and kg.tour:
        await upsert_kg_tour_steps(session, repo_id, kg.tour)

    # Project-level curated meta (ranked entry points from the curation pass).
    project = getattr(kg, "project", None)
    if isinstance(project, dict) and project.get("entry_points"):
        await upsert_kg_project_meta(
            session,
            repo_id,
            entry_points=project["entry_points"],
            entry_candidates=project.get("entry_candidates", []),
        )

    # Per-node curated meta (type/summary/tags) for file nodes, stored with
    # the "file:" prefix stripped so the architecture view can match its
    # node ids (plain repo-relative paths) directly.
    file_node_meta = file_node_meta_from_kg_nodes(getattr(kg, "nodes", None) or [])
    if file_node_meta:
        await upsert_kg_node_meta(session, repo_id, file_node_meta)
