"""repowise generation engine — public exports.

This package converts ParsedFile objects and graph metrics into wiki pages via
Jinja2-templated prompts and BaseProvider.generate().

Import direction (strictly one-way):
    ingestion.models ← generation.models ← context_assembler ← page_generator

Exports resolve lazily (PEP 562): importing a light leaf module such as
``generation.report`` from the index-only update path no longer pays for
``context_assembler``/``page_generator``/``editor_files`` — they load on first
attribute access instead.
"""

from importlib import import_module
from typing import Any

# Public name → defining submodule. Names import lazily on first access so
# light consumers (e.g. the index-only update path importing ``.report``)
# don't pull the heavy assembler/generator stack via this ``__init__``.
_EXPORTS = {
    "ApiContractContext": ".context_assembler",
    "ArchitectureDiagramContext": ".context_assembler",
    "ContextAssembler": ".context_assembler",
    "FilePageContext": ".context_assembler",
    "InfraPageContext": ".context_assembler",
    "ModulePageContext": ".context_assembler",
    "RepoOverviewContext": ".context_assembler",
    "SccPageContext": ".context_assembler",
    "SymbolSpotlightContext": ".context_assembler",
    "ClaudeMdGenerator": ".editor_files",
    "DecisionSummary": ".editor_files",
    "EditorFileData": ".editor_files",
    "EditorFileDataFetcher": ".editor_files",
    "HotspotFile": ".editor_files",
    "KeyModule": ".editor_files",
    "TechStackItem": ".editor_files",
    "Checkpoint": ".job_system",
    "JobStatus": ".job_system",
    "JobSystem": ".job_system",
    "GENERATION_LEVELS": ".models",
    "ConfidenceDecayResult": ".models",
    "FreshnessStatus": ".models",
    "GeneratedPage": ".models",
    "GenerationConfig": ".models",
    "PageType": ".models",
    "compute_confidence_decay_with_git": ".models",
    "compute_freshness": ".models",
    "compute_page_id": ".models",
    "compute_source_hash": ".models",
    "decay_confidence": ".models",
    "detect_code_api_contracts": ".api_contract_detector",
    "LinkIndex": ".interlinking",
    "WikiLink": ".interlinking",
    "attach_wiki_links_and_backlinks": ".interlinking",
    "resolve_wiki_links": ".interlinking",
    "attach_related_pages": ".related_pages",
    "CascadeMode": ".cascade",
    "CascadeResult": ".cascade",
    "PageDependencies": ".cascade",
    "build_page_dependencies": ".cascade",
    "expand_cascade": ".cascade",
    "PageRecord": ".page_selection",
    "PageSelectionIntent": ".page_selection",
    "PageSelectionResult": ".page_selection",
    "resolve_page_selection": ".page_selection",
    "SUPPORTED_LANGUAGES": ".languages",
    "SYSTEM_PROMPTS": ".page_generator",
    "PageGenerator": ".page_generator",
    "BucketAllocation": ".selection",
    "ModuleGroup": ".selection",
    "Selection": ".selection",
    "SelectionInputs": ".selection",
    "select_pages": ".selection",
    "summarize_selection": ".selection",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value  # cache: subsequent access skips __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
