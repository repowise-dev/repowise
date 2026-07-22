"""Resolve a ``repowise generate`` request into a concrete page-id set.

Pure, side-effect-free helpers: given the persisted pages and the freshly
rehydrated graph, turn the user's intent (all / unwritten / stale / paths /
ids) plus a cascade mode into the ``only_page_ids`` set the engine generates,
the dependents to mark stale, and the cost plan (which includes the cascade
fallout, so the estimate does not under-quote).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from repowise.core.cost_estimator.types import PageTypePlan
from repowise.core.generation import GENERATION_LEVELS
from repowise.core.generation.cascade import (
    CascadeMode,
    CascadeResult,
    PageDependencies,
    build_page_dependencies,
    expand_cascade,
)
from repowise.core.generation.models import compute_page_id
from repowise.core.generation.page_selection import (
    PageRecord,
    PageSelectionIntent,
    resolve_page_selection,
)
from repowise.core.generation.selection import SelectionInputs, select_pages

# Page types that describe the whole repository (as opposed to one file/module).
_REPO_WIDE_TYPES = frozenset({"repo_overview", "architecture_diagram", "onboarding"})


@dataclass(frozen=True)
class ScopePlan:
    """Everything the engine needs to run and report one scoped generation."""

    generate_ids: set[str]
    stale_ids: set[str]
    cost_plans: list[PageTypePlan]
    unknown_page_ids: tuple[str, ...]
    seed_count: int


def load_page_records(pages: list[Any]) -> list[PageRecord]:
    """Build :class:`PageRecord` views from persisted ``Page`` rows.

    A page is "template" (unwritten) when a model never touched it: the
    template provider stamps ``provider_name='template'`` and
    ``metadata.deterministic=True``. Either signal is enough.
    """
    records: list[PageRecord] = []
    for p in pages:
        try:
            meta = json.loads(getattr(p, "metadata_json", None) or "{}")
        except ValueError:
            meta = {}
        is_template = getattr(p, "provider_name", "") == "template" or bool(
            meta.get("deterministic")
        )
        records.append(
            PageRecord(
                page_id=p.id,
                page_type=p.page_type,
                target_path=p.target_path,
                is_template=is_template,
                freshness_status=getattr(p, "freshness_status", "fresh"),
            )
        )
    return records


def _layer_membership(kg_ctx: Any) -> dict[str, str]:
    """Map each file path to its ``layer_page:<id>`` from KG layer membership."""
    out: dict[str, str] = {}
    if not (kg_ctx and getattr(kg_ctx, "available", False)):
        return out
    from repowise.core.analysis.knowledge_graph import _slugify

    for layer in kg_ctx.get_layers():
        layer_id = layer.get("id") or f"layer:{_slugify(layer.get('name', ''))}"
        page_id = compute_page_id("layer_page", layer_id)
        for nid in layer.get("nodeIds", []):
            if isinstance(nid, str) and nid.startswith("file:"):
                out.setdefault(nid[5:], page_id)
    return out


def build_dependencies(
    *,
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    kg_ctx: Any,
    records: list[PageRecord],
    repo_name: str,
) -> PageDependencies:
    """Compute the structural file -> container-page map for cascade.

    Uses the same full-coverage selection the engine will use
    (``select_all=True``), so the module/SCC page ids it derives match the ones
    generation emits. Repo-wide ids are the overview + architecture pages
    (keyed by repo name) plus every persisted onboarding page.
    """
    try:
        community_info_map = graph_builder.community_info() or {}
    except Exception:
        community_info_map = {}

    kg_modules = None
    if kg_ctx and getattr(kg_ctx, "available", False):
        kg_modules = kg_ctx.get_modules() or None

    selection = select_pages(
        SelectionInputs(
            parsed_files=parsed_files,
            pagerank=graph_builder.pagerank(),
            betweenness=graph_builder.betweenness_centrality(),
            community=graph_builder.community_detection(),
            community_info=community_info_map,
            sccs=list(graph_builder.strongly_connected_components()),
            git_meta_map=None,
            config=config,
            kg_modules=kg_modules,
            select_all=True,
        )
    )

    repo_wide_ids = [
        compute_page_id("repo_overview", repo_name),
        compute_page_id("architecture_diagram", repo_name),
    ]
    repo_wide_ids += [r.page_id for r in records if r.page_type == "onboarding"]

    return build_page_dependencies(
        module_groups=selection.module_groups,
        scc_groups=selection.scc_groups,
        layer_page_of=_layer_membership(kg_ctx),
        repo_wide_ids=repo_wide_ids,
    )


def build_cost_plans(generate_ids: set[str]) -> list[PageTypePlan]:
    """Group the id set by page type into cost-estimator plans.

    Because ``generate_ids`` already carries the cascade fallout, the resulting
    estimate covers it too — the under-quote the plan warned about.
    """
    counts: dict[str, int] = {}
    for pid in generate_ids:
        ptype = pid.split(":", 1)[0]
        counts[ptype] = counts.get(ptype, 0) + 1
    return [
        PageTypePlan(page_type=ptype, count=n, level=GENERATION_LEVELS.get(ptype, 2))
        for ptype, n in sorted(counts.items(), key=lambda kv: GENERATION_LEVELS.get(kv[0], 2))
    ]


def resolve_scope(
    *,
    records: list[PageRecord],
    intent: PageSelectionIntent,
    cascade_mode: CascadeMode,
    deps: PageDependencies,
) -> ScopePlan:
    """Resolve intent -> seeds -> cascade -> the full scope plan."""
    seeds = resolve_page_selection(records, intent)
    cascade: CascadeResult = expand_cascade(set(seeds.page_ids), cascade_mode, deps)
    return ScopePlan(
        generate_ids=cascade.generate_ids,
        stale_ids=cascade.stale_ids,
        cost_plans=build_cost_plans(cascade.generate_ids),
        unknown_page_ids=seeds.unknown_page_ids,
        seed_count=len(seeds),
    )
