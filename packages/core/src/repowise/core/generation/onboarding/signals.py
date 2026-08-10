"""Shared signal bundle passed to onboarding subkind builders.

Each subkind reads only what it needs from this object. Keeping all signals
in one typed bundle means subkinds compose easily — adding a new subkind
doesn't require new plumbing in :func:`PageGenerator.generate_all`.

Signals are read-only snapshots assembled at the start of level 8; subkind
builders must not mutate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repowise.core.ingestion.models import ParsedFile, RepoStructure

from ..concept_tree.vocabulary import HouseTerm


@dataclass(frozen=True)
class OnboardingSignals:
    """Inputs available to every onboarding subkind context builder.

    Attributes mirror the data that :func:`PageGenerator.generate_all`
    already computes for earlier levels — no new ingestion is required.
    """

    repo_name: str
    repo_structure: RepoStructure
    parsed_files: tuple[ParsedFile, ...]
    source_map: dict[str, bytes]
    graph_builder: Any
    pagerank: dict[str, float]
    betweenness: dict[str, float]
    community: dict[str, int]
    sccs: tuple[Any, ...]
    git_meta_map: dict[str, dict] | None = None
    dead_code_by_file: dict[str, list[dict]] = field(default_factory=dict)
    decisions_all: tuple[dict, ...] = ()
    external_systems: tuple[dict, ...] = ()
    # Summaries of pages already generated at earlier levels (target_path → blurb).
    completed_page_summaries: dict[str, str] = field(default_factory=dict)
    # KG-derived signals for onboarding pages.
    kg_layers: tuple[dict, ...] = ()
    kg_tour_steps: tuple[dict, ...] = ()
    # Topology-driven guided-tour stops (ordered), each referencing an
    # already-generated page. Empty when no tour could be built.
    tour_stops: tuple[dict, ...] = ()
    # Layers ordered top→bottom by dependency direction (the grouping spine).
    layer_order: tuple[str, ...] = ()
    # The repository's own words for its own subsystems, ranked by how many of
    # its documents name them and gated on the code using them. Empty when the
    # run had no repository path to read, when the repository documents
    # nothing, or when nothing it documents was built — all three are logged
    # where the mining happens, because an empty tuple here cannot say which.
    house_terms: tuple[HouseTerm, ...] = ()
    # What the structural side calls the parts of the system: one string per
    # module group, its title followed by its summary. The corroborating
    # artifact for a mined term — a group is cut from the dependency graph and
    # named from the code, so a term appearing in one was arrived at twice,
    # independently. Empty on every run that emits no page needing it, because
    # building it costs a store read.
    module_corroboration: tuple[str, ...] = ()
