"""LLM enrichment of the knowledge graph (layer naming + tour generation).

Takes the deterministic skeleton from ``analysis.knowledge_graph`` and uses
the LLM to:
1. Give each community-derived layer a semantic name and description.
2. Generate a guided codebase tour (10-15 steps).
3. Backfill node summaries from generated wiki pages (zero LLM cost).
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_LAYER_NAMING_SYSTEM = (
    "You are a codebase architecture analyst. Your task is to assign "
    "semantic, human-readable names and one-sentence descriptions to "
    "detected code communities (groups of related files). "
    "Output valid JSON only. No preamble, no markdown fences."
)

_TOUR_GENERATION_SYSTEM = (
    "You are a codebase onboarding expert. Create a guided tour of 5-12 "
    "steps that walks a new developer through the codebase in logical "
    "progression — from entry points through core logic to supporting "
    "infrastructure. Each step should reference specific files. "
    "Output valid JSON only. No preamble, no markdown fences."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def enrich_knowledge_graph(
    kg_skeleton: Any,
    llm_client: Any,
    graph_builder: Any,
    repo_structure: Any,
    tech_stack: list[dict],
    generated_pages: list[Any] | None = None,
    progress: Any | None = None,
    reasoning: str = "auto",
) -> Any:
    """Enrich deterministic KG with LLM-generated layer names and tour."""
    enriched_layers = await _enrich_layers(
        kg_skeleton.layers, llm_client, graph_builder, repo_structure, tech_stack,
        reasoning=reasoning,
    )

    # When curation is enabled it has already written the canonical,
    # layer-aware tour (deterministic, one per layer top→bottom). The LLM must
    # not reselect or reorder it — keep the curated tour as-is (prose narration
    # can be layered on separately). Otherwise fall back to LLM tour generation.
    from repowise.core.analysis.kg_curation import curation_enabled

    if curation_enabled() and kg_skeleton.tour:
        tour = kg_skeleton.tour
    else:
        tour = await _generate_tour(
            enriched_layers, llm_client, graph_builder, repo_structure, kg_skeleton,
            reasoning=reasoning,
        )

    if generated_pages:
        _backfill_summaries(kg_skeleton, generated_pages)

    # Deterministic summary floor, applied *after* the page backfill so rich
    # page summaries always win and only never-paged files fall back. Gated by
    # the curation flag (the seam already floored FAST-mode output; this covers
    # the generate-mode path where the seam deferred to let backfill run first).
    if curation_enabled():
        from repowise.core.analysis.kg_curation import apply_summary_floor

        try:
            apply_summary_floor(kg_skeleton)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("kg_summary_floor_failed", error=str(exc))

    kg_skeleton.layers = enriched_layers
    kg_skeleton.tour = tour

    # Post-generation invariant gate: validate the fully-assembled KG (layers,
    # tour, summaries) against hard invariants and quality signals before it is
    # persisted. Deterministic, zero-LLM, bounded (no regeneration loop) —
    # tags low-signal summaries and surfaces any structural regression.
    try:
        from repowise.core.generation.kg_reviewer import apply_review

        apply_review(kg_skeleton)
    except Exception as exc:  # pragma: no cover - defensive, never blocks export
        logger.warning("kg_reviewer_failed", error=str(exc))

    return kg_skeleton


# ---------------------------------------------------------------------------
# Layer enrichment
# ---------------------------------------------------------------------------

_LAYER_BATCH_SIZE = 5


async def _enrich_layers(
    layers: list[dict],
    llm_client: Any,
    graph_builder: Any,
    repo_structure: Any,
    tech_stack: list[dict],
    reasoning: str = "auto",
) -> list[dict]:
    """Batch-process layers through LLM for semantic naming."""
    if not layers:
        return layers

    pagerank = graph_builder.pagerank()
    enriched = list(layers)
    # Join LLM responses back onto layers by their stable id, never by list
    # position: positional joins silently corrupt every name when a model
    # returns batch-relative indices.
    by_id = {layer["id"]: layer for layer in enriched if layer.get("id")}

    for batch_start in range(0, len(layers), _LAYER_BATCH_SIZE):
        batch = layers[batch_start : batch_start + _LAYER_BATCH_SIZE]
        batch_context = []
        for layer in batch:
            node_ids = layer.get("nodeIds", [])
            file_paths = [nid.removeprefix("file:") for nid in node_ids if nid.startswith("file:")]
            top_files = sorted(file_paths, key=lambda p: pagerank.get(p, 0.0), reverse=True)[:20]

            batch_context.append({
                "id": layer.get("id", ""),
                "heuristic_label": layer["name"],
                "file_count": len(file_paths),
                "top_files": top_files,
            })

        user_prompt = _build_layer_naming_prompt(batch_context, tech_stack, repo_structure)
        batch_ids = {layer.get("id") for layer in batch}

        try:
            response = await llm_client.generate(
                _LAYER_NAMING_SYSTEM,
                user_prompt,
                max_tokens=2048,
                temperature=0.3,
                reasoning=reasoning,
            )
            parsed = _parse_json_response(response.content)
            if parsed and "layers" in parsed:
                for item in parsed["layers"]:
                    layer_id = item.get("id")
                    target = by_id.get(layer_id) if layer_id in batch_ids else None
                    if target is None:
                        logger.warning(
                            "kg_layer_naming_unknown_id",
                            layer_id=layer_id,
                            batch_ids=sorted(filter(None, batch_ids)),
                        )
                        continue
                    if item.get("name"):
                        target["name"] = item["name"]
                    if item.get("description"):
                        target["description"] = item["description"]
        except Exception as exc:
            logger.warning("kg_layer_naming_batch_failed", error=str(exc))

    return enriched


def _build_layer_naming_prompt(
    batch_context: list[dict],
    tech_stack: list[dict],
    repo_structure: Any,
) -> str:
    tech_names = [t.get("name", "") for t in tech_stack[:10] if t.get("name")]
    entry_points = list(repo_structure.entry_points)[:5] if repo_structure else []

    lines = [
        "Assign a concise semantic name (2-4 words) and a one-sentence description "
        "to each code community below.",
        "The name must describe what the *majority* of the community's files do. "
        "The heuristic label is only a hint — do not reuse an architectural "
        "category word (e.g. middleware, service, controller, repository) unless "
        "it accurately fits the files; a docs/config/tooling group must not be "
        "named for a runtime category it does not belong to.",
        "",
        f"Tech stack: {', '.join(tech_names) if tech_names else 'unknown'}",
        f"Entry points: {', '.join(entry_points) if entry_points else 'none detected'}",
        "",
        "Communities:",
    ]

    for ctx in batch_context:
        lines.append(f"\n--- Community \"{ctx['id']}\" ---")
        lines.append(f"Heuristic label: {ctx['heuristic_label']}")
        lines.append(f"File count: {ctx['file_count']}")
        lines.append(f"Top files: {', '.join(ctx['top_files'][:10])}")

    lines.append("")
    lines.append(
        "Respond with each community's id echoed verbatim: "
        '{"layers": [{"id": "...", "name": "...", "description": "..."}]}'
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tour generation
# ---------------------------------------------------------------------------


async def _generate_tour(
    layers: list[dict],
    llm_client: Any,
    graph_builder: Any,
    repo_structure: Any,
    kg_skeleton: Any,
    reasoning: str = "auto",
) -> list[dict]:
    """Generate guided tour from enriched layers + entry points."""
    pagerank = graph_builder.pagerank()
    entry_points = list(repo_structure.entry_points)[:10] if repo_structure else []
    top_files = sorted(pagerank.keys(), key=lambda p: pagerank.get(p, 0.0), reverse=True)[:15]

    layer_summaries = []
    for layer in layers:
        node_ids = layer.get("nodeIds", [])
        file_paths = [nid.removeprefix("file:") for nid in node_ids if nid.startswith("file:")]
        top_in_layer = sorted(file_paths, key=lambda p: pagerank.get(p, 0.0), reverse=True)[:5]
        layer_summaries.append({
            "name": layer["name"],
            "description": layer.get("description", ""),
            "file_count": len(file_paths),
            "key_files": top_in_layer,
        })

    user_prompt = _build_tour_prompt(layer_summaries, entry_points, top_files)

    try:
        response = await llm_client.generate(
            _TOUR_GENERATION_SYSTEM,
            user_prompt,
            max_tokens=3000,
            temperature=0.3,
            reasoning=reasoning,
        )
        parsed = _parse_json_response(response.content)
        if parsed and "tour" in parsed:
            tour = []
            for step in parsed["tour"]:
                node_ids = []
                for f in step.get("files", step.get("nodeIds", [])):
                    if f.startswith("file:"):
                        node_ids.append(f)
                    else:
                        node_ids.append(f"file:{f}")
                tour.append({
                    "order": step.get("order", len(tour) + 1),
                    "title": step.get("title", f"Step {len(tour) + 1}"),
                    "description": step.get("description", ""),
                    "nodeIds": node_ids,
                })
            return tour
    except Exception as exc:
        logger.warning("kg_tour_generation_failed", error=str(exc))

    return build_deterministic_tour(pagerank, entry_points, layers)


def _build_tour_prompt(
    layer_summaries: list[dict],
    entry_points: list[str],
    top_files: list[str],
) -> str:
    lines = [
        "Create a guided codebase tour for a new developer. Each step should have:",
        "- order (integer starting at 1)",
        "- title (short, descriptive)",
        "- description (1-2 sentences explaining what to look at and why)",
        "- files (list of file paths relevant to this step)",
        "",
        f"Entry points: {', '.join(entry_points) if entry_points else 'none detected'}",
        f"Top files by importance: {', '.join(top_files[:10])}",
        "",
        "Architectural layers:",
    ]

    for ls in layer_summaries:
        lines.append(f"  - {ls['name']} ({ls['file_count']} files): {ls.get('description', '')}")
        if ls["key_files"]:
            lines.append(f"    Key files: {', '.join(ls['key_files'])}")

    lines.append("")
    lines.append(
        'Respond with: {"tour": [{"order": 1, "title": "...", '
        '"description": "...", "files": ["path/to/file.py"]}]}'
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deterministic tour fallback
# ---------------------------------------------------------------------------


def build_deterministic_tour(
    pagerank: dict[str, float],
    entry_points: list[str],
    layers: list[dict],
) -> list[dict]:
    """Build a tour without LLM, using structural signals only."""
    steps: list[dict] = []
    used_files: set[str] = set()
    order = 1

    # Step 1: main entry point
    if entry_points:
        ep = entry_points[0]
        steps.append({
            "order": order,
            "title": "Entry Point",
            "description": f"Start with the main entry point: {ep}",
            "nodeIds": [f"file:{ep}"],
        })
        used_files.add(ep)
        order += 1

    # Steps 2-N: one step per layer, ordered by size descending
    sorted_layers = sorted(
        layers,
        key=lambda l: len(l.get("nodeIds", [])),
        reverse=True,
    )

    for layer in sorted_layers[:10]:
        node_ids = layer.get("nodeIds", [])
        file_paths = [nid.removeprefix("file:") for nid in node_ids if nid.startswith("file:")]
        top_file = max(
            (f for f in file_paths if f not in used_files),
            key=lambda p: pagerank.get(p, 0.0),
            default=None,
        )
        if top_file:
            steps.append({
                "order": order,
                "title": layer["name"],
                "description": f"Explore the {layer['name']} layer, starting with {top_file}",
                "nodeIds": [f"file:{top_file}"],
            })
            used_files.add(top_file)
            order += 1

    return steps


# ---------------------------------------------------------------------------
# Summary backfill
# ---------------------------------------------------------------------------


def _backfill_summaries(kg_result: Any, generated_pages: list[Any]) -> None:
    """Populate node summaries from generated wiki pages (zero LLM cost)."""
    page_summaries: dict[str, str] = {}
    for p in generated_pages:
        summary = getattr(p, "summary", None) or ""
        target = getattr(p, "target_path", None) or ""
        if summary and target:
            page_summaries[target] = summary

    for node in kg_result.nodes:
        # Any file-level node (any presentation type — file/config/service/
        # pipeline/schema/document). Only fill empties: a page summary is the
        # richest source, and the deterministic curation floor is applied
        # *after* this backfill so it never blocks a real page summary.
        if not str(node.get("id", "")).startswith("file:"):
            continue
        path = node.get("filePath", node["id"].removeprefix("file:"))
        if path in page_summaries and not node.get("summary"):
            node["summary"] = page_summaries[path]


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------


def _parse_json_response(content: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown fences."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end])
            except json.JSONDecodeError:
                pass
    return None
