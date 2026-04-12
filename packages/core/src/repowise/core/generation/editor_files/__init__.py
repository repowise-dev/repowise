"""Editor-file generators for repowise.

Provides generators that create and maintain AI-editor configuration files
(CLAUDE.md, cursor.md, etc.) from already-indexed codebase data.

No LLM calls are made — all content is derived from the repowise DB.
"""

from .claude_md import ClaudeMdGenerator, WorkspaceClaudeMdGenerator
from .data import (
    DecisionSummary,
    EditorFileData,
    HotspotFile,
    KeyModule,
    TechStackItem,
    WorkspaceEditorFileData,
    WorkspaceRepoSummary,
)
from .fetcher import EditorFileDataFetcher

__all__ = [
    # Generators
    "ClaudeMdGenerator",
    "WorkspaceClaudeMdGenerator",
    # Data containers
    "DecisionSummary",
    "EditorFileData",
    "HotspotFile",
    "KeyModule",
    "TechStackItem",
    "WorkspaceEditorFileData",
    "WorkspaceRepoSummary",
    # Fetcher
    "EditorFileDataFetcher",
]
