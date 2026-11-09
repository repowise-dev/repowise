"""Knowledge-graph refresh for an incremental run."""

from __future__ import annotations

import json
from collections.abc import Iterable
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
    changed_paths: Iterable[str],
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
    Files in ``changed_paths`` carry no summary: the prior one describes the
    old code, so they get the floor as a fresh index would.

    An unchanged fingerprint skips the rebuild, but an edit that keeps the
    graph's shape still moves the changed files' own fields: those nodes are
    patched on the prior artifact instead (:func:`_patch_changed_file_nodes`).

    Returns the refreshed or patched result, or ``None`` when nothing moved
    (the persisted artifact is already current) or the rebuild failed (keep
    the prior artifact rather than export a broken one).
    """
    log = log or _noop_log
    changed_paths = set(changed_paths)
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
            patched = _patch_changed_file_nodes(
                kg_json_path, parsed_files, graph_builder, git_meta_map, changed_paths
            )
            if patched is not _REBUILD:
                if patched is not None:
                    patched.fingerprint = new_fingerprint  # the artifact does not store it
                return patched

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
            _carry_forward_kg_enrichment(kg, prior_kg, changed_paths=changed_paths)

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


# Returned by the patch when a changed file's entry status moved: the
# project's entry points are ranked across the whole graph, so rebuild.
_REBUILD = object()


def _patch_changed_file_nodes(
    kg_json_path: Path,
    parsed_files: list,
    graph_builder: Any,
    git_meta_map: dict,
    changed_paths: set[str],
) -> Any | None:
    """Rebuild the changed files' nodes on the prior artifact; ``None`` if none moved.

    The changed files' file and symbol nodes, and the edges into their
    symbols, come from the skeleton and curation passes run over those files
    alone, so they match a full rebuild. Layers, tour and the other files'
    nodes stay as stored: with the graph's shape unchanged they are what a
    rebuild would write, give or take a pagerank or community move the
    fingerprint cannot see. The skeleton pass walks the in-memory graph's
    edges once; the store work scales with the changed files.
    """
    changed = [
        pf
        for pf in parsed_files
        if getattr(pf, "file_info", None) and pf.file_info.path in changed_paths
    ]
    if not changed:
        return None

    from repowise.core.analysis.knowledge_graph import KnowledgeGraphResult

    kg = KnowledgeGraphResult.from_file(kg_json_path)
    if kg is None:
        return None
    paths = {pf.file_info.path for pf in changed}
    fresh = _changed_file_skeleton(changed, graph_builder, git_meta_map)
    old_nodes = [n for n in kg.nodes or [] if n.get("filePath") in paths]
    if _entry_status_moved(old_nodes, fresh.nodes):
        return _REBUILD
    old_ids = {n.get("id") for n in old_nodes}
    old_edges = [e for e in kg.edges or [] if _into_symbol(e, old_ids)]
    new_edges = [e for e in fresh.edges if _into_symbol(e, {n["id"] for n in fresh.nodes})]
    if _same(old_nodes, fresh.nodes) and _same(old_edges, new_edges):
        return None
    kg.nodes = [n for n in kg.nodes or [] if n.get("filePath") not in paths] + fresh.nodes
    kg.edges = [e for e in kg.edges or [] if not _into_symbol(e, old_ids)] + new_edges
    return kg


def _changed_file_skeleton(changed: list, graph_builder: Any, git_meta_map: dict) -> Any:
    """The skeleton, curated node types, barrel tags and floor for *changed* only."""
    from repowise.core.analysis.kg_curation import (
        _curate_node_types,
        _flagged_entry_paths,
        apply_summary_floor,
    )
    from repowise.core.analysis.knowledge_graph import build_knowledge_graph_skeleton

    fresh = build_knowledge_graph_skeleton(
        parsed_files=changed,
        graph_builder=graph_builder,
        repo_structure=None,
        tech_stack=[],
        external_systems=[],
        git_meta_map=git_meta_map,
    )
    pf_by_path = {pf.file_info.path: pf for pf in changed}
    manifest = frozenset(
        path
        for path, pf in pf_by_path.items()
        if getattr(pf.file_info, "is_manifest_entry", False)
        or getattr(pf.file_info, "is_application_main", False)
    )
    _flagged_entry_paths(fresh, pf_by_path, manifest)  # retags barrels in place
    _curate_node_types(fresh)
    apply_summary_floor(fresh, changed)
    return fresh


def _entry_status_moved(old_nodes: list[dict], new_nodes: list[dict]) -> bool:
    def flags(nodes: list[dict]) -> dict[str, frozenset]:
        return {
            n["id"]: frozenset({"entry_point", "barrel"} & set(n.get("tags") or []))
            for n in nodes
            if str(n.get("id", "")).startswith("file:")
        }

    return flags(old_nodes) != flags(new_nodes)


def _into_symbol(edge: dict, node_ids: set) -> bool:
    target = edge.get("target")
    return target in node_ids and not str(target).startswith("file:")


def _same(old: list[dict], new: list[dict]) -> bool:
    def key(items: list[dict]) -> list[str]:
        return sorted(json.dumps(i, sort_keys=True) for i in items)

    return key(old) == key(new)


def _carry_forward_kg_enrichment(
    kg: Any, prior_kg: Any, *, changed_paths: set[str] = frozenset()
) -> None:
    """Adopt the prior artifact's LLM-enriched prose onto the rebuilt KG.

    Matching is by stable id, and only fields the deterministic passes left
    empty are filled — structural changes always win over stale prose. Layer
    descriptions exist only after LLM enrichment (curation names layers but
    leaves descriptions empty), so a non-empty prior description is the
    signal that the prior name/description pair is the enriched one.
    A node summary is carried only for files outside ``changed_paths``.
    """
    prior_layers = {layer.get("id"): layer for layer in prior_kg.layers or []}
    for layer in kg.layers or []:
        prior = prior_layers.get(layer.get("id"))
        if prior and prior.get("description") and not layer.get("description"):
            layer["name"] = prior.get("name") or layer.get("name")
            layer["description"] = prior["description"]

    prior_summaries = {n.get("id"): n["summary"] for n in prior_kg.nodes or [] if n.get("summary")}
    for node in kg.nodes or []:
        if node.get("filePath") in changed_paths:
            continue
        if not node.get("summary"):
            prior_summary = prior_summaries.get(node.get("id"))
            if prior_summary:
                node["summary"] = prior_summary

    # With curation disabled the skeleton carries no tour and only the LLM
    # path builds one — keep the prior tour rather than exporting none.
    if not kg.tour and prior_kg.tour:
        kg.tour = prior_kg.tour
