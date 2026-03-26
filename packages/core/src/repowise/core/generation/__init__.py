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
    SccPageContext,
    SymbolSpotlightContext,
    RepoOverviewContext,
)
from .job_system import Checkpoint, JobStatus, JobSystem
from .models import (
    ConfidenceDecayResult,
    DeadCodeConfig,
    FreshnessStatus,
    GENERATION_LEVELS,
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
from .page_generator import PageGenerator, SYSTEM_PROMPTS

__all__ = [
    # models
    "PageType",
    "GENERATION_LEVELS",
    "FreshnessStatus",
    "GenerationConfig",
    "GitConfig",
    "DeadCodeConfig",
    "GeneratedPage",
    "ConfidenceDecayResult",
    "compute_page_id",
    "compute_freshness",
    "compute_source_hash",
    "decay_confidence",
    "compute_confidence_decay_with_git",
    # context assembler
    "ContextAssembler",
    "FilePageContext",
    "SymbolSpotlightContext",
    "ModulePageContext",
    "SccPageContext",
    "RepoOverviewContext",
    "ArchitectureDiagramContext",
    "ApiContractContext",
    "InfraPageContext",
    "DiffSummaryContext",
    # page generator
    "PageGenerator",
    "SYSTEM_PROMPTS",
    # job system
    "Checkpoint",
    "JobStatus",
    "JobSystem",
]
