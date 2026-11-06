"""Knowledge-graph refresh for an incremental run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import LogFn, _noop_log


async def refresh_knowledge_graph(
    repo_path: Any,
    parsed_files: list,
    graph_builder: Any,
    repo_structure: Any,
    git_meta_map: dict,
    dead_code_report: Any,
    *,
    prior_fingerprint: str | None,
    log: LogFn | None = None,
) -> Any | None:
    """Rebuild the KG skeleton + curation when the graph shape changed.

    The knowledge graph (layers, tour, entry points, curated node meta) was
    historically rebuilt only by the full init pipeline, so every incremental
    ``repowise update`` carried the init-time KG forward verbatim and agents
    read a stale orientation snapshot (#669). This reruns the deterministic
    skeleton + curation passes against the freshly rebuilt graph, then carries
    forward the prior artifact's LLM-enriched layer names and node summaries
    by stable id — so index-only updates stay LLM-free without regressing
    enrichment. LLM re-enrichment stays with the caller (docs mode only).

    Returns the refreshed result, or ``None`` when the graph fingerprint is
    unchanged (the persisted artifact is already current) or the rebuild
    failed (keep the prior artifact rather than export a broken one).
    """
    log = log or _noop_log
    try:
        from repowise.core.analysis.knowledge_graph import (
            KnowledgeGraphResult,
            build_knowledge_graph_skeleton,
            compute_kg_fingerprint,
            should_skip_kg_rebuild,
        )

        kg_json_path = Path(repo_path) / ".repowise" / "knowledge-graph.json"
        new_fingerprint = compute_kg_fingerprint(graph_builder)
        if should_skip_kg_rebuild(prior_fingerprint, new_fingerprint, kg_json_path):
            return None

        tech_stack: list[dict] = []
        try:
            from repowise.core.generation.editor_files.tech_stack import detect_tech_stack

            tech_stack = [
                {"name": t.name, "version": t.version, "category": t.category}
                for t in detect_tech_stack(repo_path)
            ]
        except Exception:
            pass  # tech stack is contextual metadata, not structural

        prior_kg = KnowledgeGraphResult.from_file(kg_json_path)

        kg = build_knowledge_graph_skeleton(
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            tech_stack=tech_stack,
            external_systems=[],
            git_meta_map=git_meta_map,
            dead_code_report=dead_code_report,
            repo_path=Path(repo_path),
        )
        kg.fingerprint = new_fingerprint

        from repowise.core.analysis.kg_curation import (
            apply_summary_floor,
            curate_knowledge_graph,
            curation_enabled,
        )

        kg = curate_knowledge_graph(
            kg,
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            repo_structure=repo_structure,
            community_info=graph_builder.community_info(),
            git_meta_map=git_meta_map,
            enabled=curation_enabled(),
            # Floor after the prior-artifact carry-forward below so carried
            # page-derived summaries win over the deterministic floor.
            defer_summary_floor=True,
        )

        if prior_kg is not None:
            _carry_forward_kg_enrichment(kg, prior_kg)

        # Summaries degrade to empty on failure, same as the init-path seam.
        import contextlib

        with contextlib.suppress(Exception):
            apply_summary_floor(kg, parsed_files)

        log(
            f"Knowledge graph refreshed: [cyan]{len(kg.layers)}[/cyan] layers, "
            f"[cyan]{len(kg.tour)}[/cyan] tour steps"
        )
        return kg
    except Exception as exc:
        log(f"[yellow]Knowledge-graph refresh skipped: {exc}[/yellow]")
        return None


def _carry_forward_kg_enrichment(kg: Any, prior_kg: Any) -> None:
    """Adopt the prior artifact's LLM-enriched prose onto the rebuilt KG.

    Matching is by stable id, and only fields the deterministic passes left
    empty are filled — structural changes always win over stale prose. Layer
    descriptions exist only after LLM enrichment (curation names layers but
    leaves descriptions empty), so a non-empty prior description is the
    signal that the prior name/description pair is the enriched one.
    """
    prior_layers = {layer.get("id"): layer for layer in prior_kg.layers or []}
    for layer in kg.layers or []:
        prior = prior_layers.get(layer.get("id"))
        if prior and prior.get("description") and not layer.get("description"):
            layer["name"] = prior.get("name") or layer.get("name")
            layer["description"] = prior["description"]

    prior_summaries = {n.get("id"): n["summary"] for n in prior_kg.nodes or [] if n.get("summary")}
    for node in kg.nodes or []:
        if not node.get("summary"):
            prior_summary = prior_summaries.get(node.get("id"))
            if prior_summary:
                node["summary"] = prior_summary

    # With curation disabled the skeleton carries no tour and only the LLM
    # path builds one — keep the prior tour rather than exporting none.
    if not kg.tour and prior_kg.tour:
        kg.tour = prior_kg.tour
