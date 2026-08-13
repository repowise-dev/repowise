"""Every orientation surface names the same front door.

``RepoStructure.entry_points`` arrives ``sorted()`` by path
(``ingestion/traverser.py:470``): deterministic, and meaningless as an
orientation order. The overview ranked it; the onboarding pages, the KG
layer-naming prompt, the KG tour and the exported KG project block each took a
raw prefix, so a truncated list showed whatever sorted first and the surfaces
disagreed with each other about where the program starts.

The order asserted here is ``orientation_entry_points``: a conventional entry
name first, then shallower path, then a generic glue stem last. ``src/main.py``
leads and ``src/features/api/index.ts`` (a glue stem, nested) comes last.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from repowise.core.generation.entry_points import orientation_entry_points
from repowise.core.ingestion.models import RepoStructure

# Path-sorted, which is what ``traverser.py:470`` actually hands over: the
# barrel sorts above the real entry because ``src/features`` precedes
# ``src/main.py``, and ``packages/`` precedes both.
RAW = [
    "packages/svc/src/server.ts",
    "src/features/api/index.ts",
    "src/main.py",
    "tools/scripts/run.py",
]
# Conventional entry name first (``main``, ``server``), then depth, with the
# glue stem last. ``run`` is deliberately NOT in this bucket: it is in the
# registry's ``entry_flag_stems`` (which is what made ``run.py`` a candidate at
# all) but not in ``entry_filename_stems``, so ``packages/svc/src/server.ts``
# outranks it despite being three levels deeper.
RANKED = [
    "src/main.py",
    "packages/svc/src/server.ts",
    "tools/scripts/run.py",
    "src/features/api/index.ts",
]


def _structure(entry_points: list[str]) -> RepoStructure:
    return RepoStructure(
        is_monorepo=False,
        packages=[],
        root_language_distribution={"python": 1.0},
        total_files=len(entry_points),
        total_loc=100,
        entry_points=list(entry_points),
    )


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------


def test_ranks_a_conventional_entry_above_a_buried_barrel():
    assert orientation_entry_points(_structure(RAW)) == RANKED


def test_limit_keeps_the_best_not_the_first():
    """The whole point of the truncation bug: ``[:1]`` on the raw list kept
    the barrel and dropped ``main.py``."""
    assert orientation_entry_points(_structure(RAW), limit=1) == ["src/main.py"]


def test_glue_is_demoted_not_dropped():
    """``packages/cli/src/index.ts`` is a genuine package front door in a
    monorepo, so ranking must not lose it."""
    ranked = orientation_entry_points(_structure(RAW))
    assert set(ranked) == set(RAW)


def test_no_repo_structure_is_an_empty_list():
    assert orientation_entry_points(None) == []
    assert orientation_entry_points(object()) == []


def test_ordering_is_stable_for_an_already_ranked_list():
    assert orientation_entry_points(_structure(RANKED)) == RANKED


# ---------------------------------------------------------------------------
# the surfaces
# ---------------------------------------------------------------------------


def test_repo_overview_entry_points_are_ranked(sample_config, sample_repo_structure):
    """The overview sorted already, but not the same way.

    ``sorted(entry_points, key=entry_point_rank_key)`` passes the key
    positionally, so ``conventional_stems`` defaulted to empty and the overview
    ranked on depth alone. This is red on origin/main, not a parity guard:
    ``tools/scripts/run.py`` used to beat ``packages/svc/src/server.ts`` on
    depth, and now loses to it on name.
    """
    from repowise.core.generation.context_assembler import ContextAssembler

    ctx = ContextAssembler(sample_config).assemble_repo_overview(
        replace(sample_repo_structure, entry_points=RAW), {}, [], {}
    )
    assert ctx.entry_points == RANKED


def _onboarding_entry_points(slot: str) -> list[str]:
    """Build one onboarding page context and read back its entry points."""
    from types import SimpleNamespace

    from repowise.core.generation import onboarding
    from repowise.core.generation.onboarding.signals import OnboardingSignals
    from repowise.core.generation.onboarding.slots import (
        SLOT_GETTING_STARTED,
        SLOT_HOW_IT_WORKS,
    )

    spec = onboarding.get_spec(
        SLOT_HOW_IT_WORKS if slot == "how_it_works" else SLOT_GETTING_STARTED
    )
    assert spec is not None
    signals = OnboardingSignals(
        repo_name="testrepo",
        repo_structure=_structure(RAW),
        parsed_files=(),
        source_map={},
        graph_builder=SimpleNamespace(
            community_info=lambda: {},
            execution_flows=lambda: SimpleNamespace(flows=[]),
        ),
        pagerank={},
        betweenness={},
        community={},
        sccs=(),
        git_meta_map={},
        dead_code_by_file={},
        decisions_all=(),
        # getting_started needs a manifest signal or it declines to build; the
        # archetype gate on how_it_works is satisfied by the tour stops below.
        external_systems=({"name": "fastapi", "ecosystem": "pypi", "is_dev_dep": False},),
        completed_page_summaries={},
        tour_stops=({"title": "Entry Point", "nodeIds": ["file:src/main.py"]},),
        layer_order=("app",),
    )
    ctx = spec.build_context(signals)
    assert ctx is not None, f"{slot} declined to build; the fixture no longer feeds its gate"
    return list(ctx.entry_points)


@pytest.mark.parametrize(("slot", "limit"), [("how_it_works", 5), ("getting_started", 6)])
def test_onboarding_pages_agree_with_the_overview(slot, limit):
    """Both pages truncate, so an unranked list did not merely reorder them:
    it decided which entry points a reader ever saw."""
    assert _onboarding_entry_points(slot) == RANKED[:limit]


def test_kg_project_block_is_ranked():
    """``project.entry_points`` in the exported knowledge-graph.json. The
    curator overwrites this when it runs, but curation is feature-flagged and
    this is what a ``REPOWISE_KG_CURATION=0`` export carries."""
    from types import SimpleNamespace

    import networkx as nx

    from repowise.core.analysis.knowledge_graph import build_knowledge_graph_skeleton

    builder = SimpleNamespace(
        graph=lambda: nx.DiGraph(),
        pagerank=lambda: {},
        betweenness_centrality=lambda: {},
        community_detection=lambda: {},
        community_info=lambda: {},
    )
    result = build_knowledge_graph_skeleton(
        parsed_files=[],
        graph_builder=builder,
        repo_structure=_structure(RAW),
        tech_stack=[],
        external_systems=[],
    )
    assert result.project["entry_points"] == RANKED


def test_deterministic_kg_tour_starts_at_the_best_entry_point():
    """``build_deterministic_tour`` opens at ``entry_points[0]``. It is handed
    the already-truncated list from the tour generator, so an unranked list
    started the guided tour at a re-export barrel."""
    from repowise.core.generation.knowledge_graph import build_deterministic_tour

    layers = [{"name": "App", "nodeIds": [f"file:{p}" for p in RAW]}]
    ranked = orientation_entry_points(_structure(RAW), limit=10)
    stops = build_deterministic_tour({p: 0.1 for p in RAW}, ranked, layers)

    assert stops
    assert stops[0]["nodeIds"] == ["file:src/main.py"]
