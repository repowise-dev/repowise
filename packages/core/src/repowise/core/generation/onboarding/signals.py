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
