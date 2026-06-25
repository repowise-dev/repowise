"""Opt-in LLM enrichment of deterministic refactoring plans.

The deterministic layer (``refactoring/``) produces structured
``RefactoringSuggestion`` plans with zero LLM calls. This subpackage is the
strictly-optional second step: on demand, it hands one plan plus the real
source spans it references to an LLM and gets back the named, refactored code
and a unified diff. It is never imported by the indexing hot path — only by the
edges (CLI ``--generate-code``, the web ``/generate-code`` endpoint, the MCP
tool) when a human explicitly asks for code.
"""

from __future__ import annotations

from .enrich import (
    EnrichmentResult,
    SourceSpan,
    build_enrichment_provider,
    enrich_suggestion,
    llm_enrichment_enabled,
)

__all__ = [
    "EnrichmentResult",
    "SourceSpan",
    "build_enrichment_provider",
    "enrich_suggestion",
    "llm_enrichment_enabled",
]
