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
    # The repository's documentation files (parsed Markdown plus the
    # reStructuredText ingestion does not parse), read once per run.
    doc_sources: dict[str, bytes] = field(default_factory=dict)
    # The walkthrough pages written for the run's main operations, in plan
    # order. How It Works is told from them. Empty when none are written yet.
    journeys: tuple[Any, ...] = ()
    # Top-level parts with their own page's description. Empty unless an
    # emitted subkind declared it needs them (a store read on a scoped run).
    parts: tuple[Any, ...] = ()
    # What a user types or calls (``surface.Surfaces``), read once per run.
    # ``None`` when the run did not build it; subkinds treat that as empty.
    surfaces: Any = None


def file_layer_map(signals: OnboardingSignals) -> dict[str, str]:
    """Map repository-relative file paths to KG layer names, when available."""
    out: dict[str, str] = {}
    for layer in getattr(signals, "kg_layers", ()):
        name = str(layer.get("name", "")).strip()
        if not name:
            continue
        for node_id in layer.get("nodeIds", []) or []:
            if isinstance(node_id, str) and node_id.startswith("file:"):
                out[node_id[len("file:") :]] = name
    return out
