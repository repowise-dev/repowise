"""Build a generation plan from ingestion output.

Thin wrapper over :func:`repowise.core.generation.select_pages`. The
cost estimator and the page generator therefore *share the same
selection logic* — the pre-run estimate cannot drift from the actual
run. The pages generation then skips are skipped here by the same rules:
module groups with nothing to write about, and onboarding slots whose gate
fails.
"""

from __future__ import annotations

from typing import Any

import structlog

from repowise.core.generation import GENERATION_LEVELS
from repowise.core.generation.selection import SelectionInputs, select_pages
from repowise.core.generation.selection.selector import near_clone_paths

from .types import PageTypePlan

log = structlog.get_logger(__name__)


def build_generation_plan(
    parsed_files: list[Any],
    graph_builder: Any,
    config: Any,
    skip_tests: bool = False,
    skip_infra: bool = False,
    kg_modules: list[dict] | None = None,
    git_meta_map: dict[str, dict] | None = None,
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
        Curated wiki modules from the KG artifact, when available. Only
        steers which adjacent directories merge into a concept group, so its
        absence changes which pages exist rather than how many, and the
        estimate holds either way.
    git_meta_map:
        Per-file git metadata, including hotspot flags, matching the map
        passed to generation so both select the same file pages.
    """
    from repowise.core.generation.page_generator.helpers import _is_infra_file, is_code_file

    files = parsed_files
    if skip_tests:
        files = [p for p in files if not p.file_info.is_test]
    if skip_infra:
        files = [p for p in files if not _is_infra_file(p)]

    pagerank = graph_builder.pagerank()
    betweenness = graph_builder.betweenness_centrality()
    community = graph_builder.community_detection()
    sccs = list(graph_builder.strongly_connected_components())
    try:
        community_info_map = graph_builder.community_info() or {}
    except Exception:
        community_info_map = {}

    # The near-clones generation drops never get a page, so the plan leaves
    # them out the same way, chosen from the same code files.
    code_files = [p for p in files if is_code_file(p)]
    drop = near_clone_paths(
        code_files, pagerank, enabled=getattr(config, "dedupe_near_clones", True)
    )
    selection = select_pages(
        SelectionInputs(
            parsed_files=[p for p in files if p.file_info.path not in drop],
            pagerank=pagerank,
            betweenness=betweenness,
            community=community,
            community_info=community_info_map,
            sccs=sccs,
            git_meta_map=git_meta_map,
            config=config,
            kg_modules=kg_modules,
            symbol_uses=graph_builder.symbol_uses(),
        )
    )

    counts = selection.counts()
    counts["module_page"] = _written_module_pages(
        selection.module_groups,
        [p for p in code_files if p.file_info.path not in drop],
        graph_builder,
    )

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
        "infra_page",
    ):
        count = counts.get(page_type, 0)
        if count:
            plans.append(
                PageTypePlan(
                    page_type=page_type,
                    count=count,
                    level=GENERATION_LEVELS[page_type],
                    folder_coverage=(
                        selection.folder_coverage if page_type == "file_page" else []
                    ),
                )
            )

    # Flow pages come from the call graph, by the selection generation runs.
    from repowise.core.generation.flow_pages import plans_for_index

    flow_count = len(plans_for_index(graph_builder, files))
    if flow_count:
        plans.append(
            PageTypePlan(
                page_type="flow_page",
                count=flow_count,
                level=GENERATION_LEVELS["flow_page"],
            )
        )

    if getattr(config, "enable_onboarding", True):
        # Every parsed file, as generation hands the onboarding gates.
        onboarding = _onboarding_plan(parsed_files, graph_builder, git_meta_map)
        if onboarding is not None:
            plans.append(onboarding)

    return plans


def _written_module_pages(groups: list[Any], code_files: list[Any], graph_builder: Any) -> int:
    """How many of *groups* level 4 writes: one with no file that defines or imports
    anything, and no parts, gets no page."""
    from repowise.core.analysis.dead_code.file_reachability import file_dependency_neighbors
    from repowise.core.generation.page_generator.levels import written_module_groups

    graph = graph_builder.graph()
    has_material = {
        p.file_info.path: bool(p.symbols)
        or bool(file_dependency_neighbors(graph, p.file_info.path, incoming=False))
        for p in code_files
    }
    return len(written_module_groups(list(groups), has_material))


def _onboarding_plan(
    files: list[Any], graph_builder: Any, git_meta_map: dict[str, dict] | None
) -> PageTypePlan | None:
    """The onboarding pages: every model-written slot, and each deterministic slot
    whose gate passes. Deterministic slots render from facts, so they cost nothing.

    Ceiling: the model-written slots are counted without their gates, which need
    the docs and entry points generation reads, so a slot whose gate fails
    still counts here. A deterministic gate is judged on the signals this plan
    holds (files, graph, git metadata); one reading anything else would be
    judged without it.
    """
    from repowise.core.generation.onboarding import OnboardingSignals
    from repowise.core.generation.onboarding import iter_specs as _iter_onboarding

    specs = _iter_onboarding()
    model = sum(1 for spec in specs if not spec.deterministic)
    deterministic = [spec for spec in specs if spec.deterministic]
    free = 0
    if deterministic:
        signals = OnboardingSignals(
            repo_name="",
            repo_structure=None,
            parsed_files=tuple(files),
            source_map={},
            graph_builder=graph_builder,
            pagerank=graph_builder.pagerank(),
            betweenness=graph_builder.betweenness_centrality(),
            community=graph_builder.community_detection(),
            sccs=tuple(graph_builder.strongly_connected_components()),
            git_meta_map=git_meta_map,
        )
        for spec in deterministic:
            try:
                free += spec.build_context(signals) is not None
            except Exception as exc:
                log.debug("plan.onboarding_gate_failed", slot=spec.slot, error=str(exc))
                free += 1
    if not model + free:
        return None
    return PageTypePlan(
        page_type="onboarding",
        count=model + free,
        level=GENERATION_LEVELS["onboarding"],
        free=free,
    )
