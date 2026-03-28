"""repowise generation engine — public exports.

This package converts ParsedFile objects and graph metrics into wiki pages via
Jinja2-templated prompts and BaseProvider.generate().

Import direction (strictly one-way):
    ingestion.models ← generation.models ← context_assembler ← page_generator
"""

from .context_assembler import (
    ApiContractContext,
    ArchitectureDiagramContext,
    ContextAssembler,
    DiffSummaryContext,
    FilePageContext,
    InfraPageContext,
    ModulePageContext,
    RepoOverviewContext,
    SccPageContext,
    SymbolSpotlightContext,
)
from .editor_files import (
    ClaudeMdGenerator,
    DecisionSummary,
    EditorFileData,
    EditorFileDataFetcher,
    HotspotFile,
    KeyModule,
    TechStackItem,
)
from .job_system import Checkpoint, JobStatus, JobSystem
from .models import (
    GENERATION_LEVELS,
    ConfidenceDecayResult,
    DeadCodeConfig,
    FreshnessStatus,
    GeneratedPage,
    GenerationConfig,
    GitConfig,
    PageType,
    compute_confidence_decay_with_git,
    compute_freshness,
    compute_page_id,
    compute_source_hash,
    decay_confidence,
)
from .page_generator import SYSTEM_PROMPTS, PageGenerator

__all__ = [
    "GENERATION_LEVELS",
    "SYSTEM_PROMPTS",
    "ApiContractContext",
    "ArchitectureDiagramContext",
    "Checkpoint",
    "ClaudeMdGenerator",
    "ConfidenceDecayResult",
    "ContextAssembler",
    "DeadCodeConfig",
    "DecisionSummary",
    "DiffSummaryContext",
    "EditorFileData",
    "EditorFileDataFetcher",
    "FilePageContext",
    "FreshnessStatus",
    "GeneratedPage",
    "GenerationConfig",
    "GitConfig",
    "HotspotFile",
    "InfraPageContext",
    "JobStatus",
    "JobSystem",
    "KeyModule",
    "ModulePageContext",
    "PageGenerator",
    "PageType",
    "RepoOverviewContext",
    "SccPageContext",
    "SymbolSpotlightContext",
    "TechStackItem",
    "compute_confidence_decay_with_git",
    "compute_freshness",
    "compute_page_id",
    "compute_source_hash",
    "decay_confidence",
]
