"""Data containers for editor-file generators.

These frozen dataclasses decouple DB fetching from template rendering.
All fields use basic Python types so they can be constructed directly in tests
without any DB or filesystem dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TechStackItem:
    name: str
    version: str | None
    category: str  # "language" | "framework" | "database" | "infra"


@dataclass(frozen=True)
class KeyModule:
    name: str  # display name, e.g. "src/api"
    purpose: str  # short description (~80 chars)
    file_count: int
    owner: str | None


@dataclass(frozen=True)
class HotspotFile:
    path: str
    churn_percentile: float
    commit_count_90d: int
    owner: str | None


@dataclass(frozen=True)
class DecisionSummary:
    title: str
    status: str  # active | deprecated | superseded | proposed
    rationale: str  # first ~100 chars of decision.rationale


@dataclass(frozen=True)
class EditorFileData:
    repo_name: str
    indexed_at: str  # date only: "2026-03-28"
    indexed_commit: str  # short SHA of HEAD at index time, e.g. "a1b2c3d"
    architecture_summary: str  # 2-4 sentences from repo_overview page
    key_modules: list[KeyModule] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    tech_stack: list[TechStackItem] = field(default_factory=list)
    hotspots: list[HotspotFile] = field(default_factory=list)
    decisions: list[DecisionSummary] = field(default_factory=list)
    build_commands: dict[str, str] = field(default_factory=dict)
    avg_confidence: float = 0.0
