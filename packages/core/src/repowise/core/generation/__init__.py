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
from .api_contract_detector import detect_code_api_contracts
from .interlinking import (
    LinkIndex,
    WikiLink,
    attach_wiki_links_and_backlinks,
    resolve_wiki_links,
)
from .page_generator import SYSTEM_PROMPTS, PageGenerator
from .selection import (
    BucketAllocation,
    ModuleGroup,
    Selection,
    SelectionInputs,
    select_pages,
    summarize_selection,
)

__all__ = [
    "BucketAllocation",
    "GENERATION_LEVELS",
    "LinkIndex",
    "ModuleGroup",
    "SYSTEM_PROMPTS",
    "Selection",
    "SelectionInputs",
    "WikiLink",
    "attach_wiki_links_and_backlinks",
    "resolve_wiki_links",
    "select_pages",
    "summarize_selection",
    "ApiContractContext",
    "ArchitectureDiagramContext",
    "Checkpoint",
    "ClaudeMdGenerator",
    "ConfidenceDecayResult",
    "ContextAssembler",
    "DeadCodeConfig",
    "DecisionSummary",
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
    "detect_code_api_contracts",
]
