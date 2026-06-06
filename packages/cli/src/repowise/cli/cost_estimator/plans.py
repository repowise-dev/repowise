"""Build a generation plan from ingestion output.

Thin wrapper over :func:`repowise.core.generation.select_pages`. The
cost estimator and the page generator therefore *share the same
selection logic* — the pre-run estimate cannot drift from the actual
run.
"""

from __future__ import annotations

from typing import Any

from repowise.core.generation import GENERATION_LEVELS
from repowise.core.generation.selection import SelectionInputs, select_pages

from .types import PageTypePlan


def build_generation_plan(
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    skip_tests: bool = False,
    skip_infra: bool = False,
    kg_modules: list[dict] | None = None,
) -> list[PageTypePlan]:
    """Return the per-page-type plan that ``generate_all`` will execute.

    Parameters
    ----------
    parsed_files:
        Output of the ingestion phase. Test/infra files are filtered
        here when ``skip_tests`` / ``skip_infra`` are set, matching the
        upstream pipeline's behavior.
    graph_builder:
        Finalized GraphBuilder (build() already called).
    config:
        GenerationConfig — its ``coverage_pct`` drives the budget.
    kg_modules:
        Curated wiki modules from the KG artifact, when available. Must be
        passed for ``module_grouping="curated"`` estimates to match the
        actual run — without it the selector falls back to community
        grouping, exactly as generation itself would without an artifact.
    """
    files = parsed_files
    if skip_tests:
        files = [p for p in files if not p.file_info.is_test]
    if skip_infra:
        from repowise.core.generation.selection.selector import _is_infra_file

        files = [p for p in files if not _is_infra_file(p)]

    pagerank = graph_builder.pagerank()
    betweenness = graph_builder.betweenness_centrality()
    community = graph_builder.community_detection()
    sccs = list(graph_builder.strongly_connected_components())
    try:
        community_info_map = graph_builder.community_info() or {}
    except Exception:
        community_info_map = {}

    selection = select_pages(
        SelectionInputs(
            parsed_files=files,
            pagerank=pagerank,
            betweenness=betweenness,
            community=community,
            community_info=community_info_map,
            sccs=sccs,
            git_meta_map=None,
            config=config,
            kg_modules=kg_modules,
        )
    )

    counts = selection.counts()

    plans: list[PageTypePlan] = []
    # Preserve the level-ordered output the prior implementation
    # exposed — callers like the init UI render plans in this order.
    for page_type in (
        "api_contract",
        "symbol_spotlight",
        "file_page",
        "scc_page",
        "module_page",
        "repo_overview",
        "architecture_diagram",
        "infra_page",
    ):
        count = counts.get(page_type, 0)
        if count:
            plans.append(
                PageTypePlan(
                    page_type=page_type,
                    count=count,
                    level=GENERATION_LEVELS[page_type],
                )
            )

    # Onboarding count comes from the registry (curated, gated at
    # generation time); the budget allocator does not touch it.
    if getattr(config, "enable_onboarding", True):
        from repowise.core.generation.onboarding import iter_specs as _iter_onboarding

        onboarding_count = len(_iter_onboarding())
        if onboarding_count:
            plans.append(
                PageTypePlan(
                    page_type="onboarding",
                    count=onboarding_count,
                    level=GENERATION_LEVELS["onboarding"],
                )
            )

    return plans
