"""Resolve a scoped-generation request into a concrete page-id set.

Pure, side-effect-free helpers: given the persisted pages and a rehydrated
graph, turn a user's intent (all / unwritten / stale / paths / ids) plus a
cascade mode into the ``only_page_ids`` set the engine generates, the dependents
to mark stale, and the cost plan (which includes the cascade fallout, so the
estimate does not under-quote).

This module has no CLI or server dependencies: it imports only from
``repowise.core``, so the OSS CLI, the OSS server, and hosted all resolve a
generation scope the same way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from repowise.core.cost_estimator.types import PageTypePlan
from repowise.core.generation.cascade import (
    CascadeMode,
    CascadeResult,
    PageDependencies,
    build_page_dependencies,
    expand_cascade,
)
from repowise.core.generation.models import GENERATION_LEVELS, compute_page_id
from repowise.core.generation.page_selection import (
    PageRecord,
    PageSelectionIntent,
    resolve_page_selection,
)
from repowise.core.generation.selection import Selection, SelectionInputs, select_pages

# Page types that describe the whole repository (as opposed to one file/module).
_REPO_WIDE_TYPES = frozenset({"repo_overview", "architecture_diagram", "onboarding"})

# Structural pages the coverage budget does not rank: layer pages come from KG
# membership and onboarding is curated, so init emits every one of them at every
# coverage. A ranked run includes the unwritten ones rather than leaving holes in
# the navigation. (repo_overview / architecture come from the Selection itself.)
_STRUCTURAL_PAGE_TYPES = frozenset({"layer_page", "onboarding"})


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


def _selection_inputs(
    *,
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    kg_ctx: Any,
    select_all: bool,
) -> SelectionInputs:
    """Bundle the inputs :func:`select_pages` needs from the rehydrated graph.

    Shared by the cascade-dependency selection (``select_all=True``) and the
    ranked coverage selection (``select_all=False`` + a coverage-scoped config),
    so both derive their page ids from the same graph metrics and KG modules.
    """
    try:
        community_info_map = graph_builder.community_info() or {}
    except Exception:
        community_info_map = {}

    kg_modules = None
    if kg_ctx and getattr(kg_ctx, "available", False):
        kg_modules = kg_ctx.get_modules() or None

    return SelectionInputs(
        parsed_files=parsed_files,
        pagerank=graph_builder.pagerank(),
        betweenness=graph_builder.betweenness_centrality(),
        community=graph_builder.community_detection(),
        community_info=community_info_map,
        sccs=list(graph_builder.strongly_connected_components()),
        git_meta_map=None,
        config=config,
        kg_modules=kg_modules,
        select_all=select_all,
    )


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
    selection = select_pages(
        _selection_inputs(
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            config=config,
            kg_ctx=kg_ctx,
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


def selection_page_ids(sel: Selection, repo_name: str) -> set[str]:
    """Turn a budgeted :class:`Selection` into the page ids generation emits.

    Mirrors the id assignment in the page generator's level builders exactly
    (``levels.py``), so a ranked coverage pick names the same pages a full run
    at that coverage would write. Repo-wide overview/architecture pages are
    keyed by the repo name; onboarding is not budgeted here (the caller adds it).
    """
    ids: set[str] = set()
    ids.update(compute_page_id("file_page", p) for p in sel.file_page_paths)
    ids.update(compute_page_id("module_page", mg.key) for mg in sel.module_groups)
    ids.update(compute_page_id("scc_page", scc_id) for scc_id, _ in sel.scc_groups)
    ids.update(compute_page_id("api_contract", p) for p in sel.api_contract_paths)
    ids.update(compute_page_id("infra_page", p) for p in sel.infra_paths)
    ids.update(
        compute_page_id("symbol_spotlight", f"{path}::{name}")
        for path, name in sel.symbol_spotlights
    )
    if sel.emit_repo_overview:
        ids.add(compute_page_id("repo_overview", repo_name))
    if sel.emit_arch_diagram:
        ids.add(compute_page_id("architecture_diagram", repo_name))
    return ids


def build_ranked_seed(
    *,
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    kg_ctx: Any,
    records: list[PageRecord],
    repo_name: str,
    coverage_pct: float,
) -> set[str]:
    """The unwritten pages inside the top ``coverage_pct`` by importance.

    Runs the *budgeted* selection at ``coverage_pct`` (the same importance model
    ``repowise init`` uses at that coverage) and maps it to page ids. The
    file / module / cycle / symbol content pages are what coverage rations;
    the repo-wide and structural pages (overview, architecture, layers,
    onboarding) are always included when unwritten, as init emits them at every
    coverage. Only ids that exist as template pages survive: a written page is
    not re-billed, and a selected id with no page yet (a file added since
    indexing) is dropped, since ``generate`` only rewrites existing pages.

    One deliberate simplification: the selection here is demand-free
    (``demand=None``), whereas a full init pass tilts its file-page picks by
    mined session demand. On a fresh install the two are identical; where session
    history exists they can pick the same *count* of files but a slightly
    different set. Not worth reading every transcript on each generate run.
    """
    coverage_cfg = replace(
        config, coverage_pct=coverage_pct, max_pages_pct=coverage_pct, deterministic=False
    )
    selection = select_pages(
        _selection_inputs(
            parsed_files=parsed_files,
            graph_builder=graph_builder,
            config=coverage_cfg,
            kg_ctx=kg_ctx,
            select_all=False,
        )
    )
    return _ranked_ids_to_seed(selection_page_ids(selection, repo_name), records)


def _ranked_ids_to_seed(ranked_ids: set[str], records: list[PageRecord]) -> set[str]:
    """Restrict ranked ids to the unwritten pages, adding structural pages.

    Layer + onboarding pages are structural: the budgeted selection does not
    rank them (layers come from KG membership, onboarding is curated), and init
    writes every one of them regardless of coverage. Add the unwritten ones so a
    coverage upgrade produces the same navigable wiki init would, then keep only
    ids that exist as template pages today.
    """
    structural_ids = {
        r.page_id for r in records if r.page_type in _STRUCTURAL_PAGE_TYPES and r.is_template
    }
    template_ids = {r.page_id for r in records if r.is_template}
    return (ranked_ids | structural_ids) & template_ids


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
    ranked_seed: set[str] | None = None,
) -> ScopePlan:
    """Resolve seeds -> cascade -> the full scope plan.

    ``ranked_seed`` short-circuits the intent selectors: a ``--coverage`` /
    ``--top`` run has already picked the exact page-id set by importance (see
    :func:`build_ranked_seed`), so it is used verbatim as the seed. Otherwise
    the intent (all / unwritten / stale / paths / ids) is resolved against the
    existing records.
    """
    if ranked_seed is not None:
        seed_ids = set(ranked_seed)
        unknown: tuple[str, ...] = ()
        seed_count = len(seed_ids)
    else:
        seeds = resolve_page_selection(records, intent)
        seed_ids = set(seeds.page_ids)
        unknown = seeds.unknown_page_ids
        seed_count = len(seeds)

    cascade: CascadeResult = expand_cascade(seed_ids, cascade_mode, deps)
    return ScopePlan(
        generate_ids=cascade.generate_ids,
        stale_ids=cascade.stale_ids,
        cost_plans=build_cost_plans(cascade.generate_ids),
        unknown_page_ids=unknown,
        seed_count=seed_count,
    )
