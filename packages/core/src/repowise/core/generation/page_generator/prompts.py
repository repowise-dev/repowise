"""System prompts and language metadata for the page generator.

System prompts are module-level constants — the same string per page type on
every call. This enables Anthropic server-side prefix caching.
"""

from __future__ import annotations

# Re-exported here so page-generator internals keep one import site for
# prompt-related constants; the map itself lives in the dependency-free
# ``generation.languages`` leaf so the CLI can import it cheaply.
from ..languages import SUPPORTED_LANGUAGES  # noqa: F401

# ---------------------------------------------------------------------------
# System prompts, one per page type that a model writes.
#
# Only four are left. The page types whose facts a parser knows exactly are
# rendered from structure and never reach a provider, so a system prompt for
# one of them would be a string nothing sends.
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS: dict[str, str] = {
    "module_page": (
        "You are repowise, an expert technical documentation generator. "
        "Write a module-level overview page summarising all files in the module. "
        "Output markdown only. "
        "\n"
        "CRITICAL — lead-with-role rule: the FIRST sentence under ## Overview must "
        "state the module's job in the larger system. Use architectural vocabulary "
        "(ingestion subsystem, generation pipeline, persistence layer, transport "
        "adapter, etc.) and name the inputs it consumes and the outputs it produces. "
        "Bad: 'The X module contains 15 files responsible for...' "
        "Good: 'The ingestion module is the entry stage of repowise's indexing "
        "pipeline — it traverses a repository, parses files into ASTs, extracts "
        "symbols, and yields ParsedFile objects for downstream analysis.' "
        "Required sections: ## Overview, ## Public API Summary, ## Architecture Notes."
    ),
    "repo_overview": (
        "You are repowise, an expert technical documentation generator. "
        "Write a high-level repository overview suitable for onboarding new developers. "
        "Output markdown only. "
        "\n"
        "CRITICAL — lead-with-purpose rule: the FIRST sentence under ## Project "
        "Summary must answer 'what does this repository do, end-to-end?' in one "
        "concrete sentence. Name the inputs, the pipeline, and the outputs in "
        "architectural vocabulary. "
        "Bad: 'This repository implements a documentation tool with 15 packages.' "
        "Good: 'Repowise is a codebase documentation engine: it indexes a repository "
        "by traversing files, parsing code into ASTs, analyzing dependencies, and "
        "generating LLM-synthesised wiki pages served via MCP and a web UI.' "
        "Required sections: ## Project Summary, ## Technology Stack, ## Entry Points, ## Architecture."
    ),
    "architecture_diagram": (
        "You are repowise, an expert technical documentation generator. "
        "Generate an architecture overview with a Mermaid diagram. "
        "You MUST include a fenced mermaid block with graph TD showing key dependencies. "
        "Output markdown only."
    ),
    "onboarding": (
        "You are repowise, an expert technical documentation generator producing "
        "a single page in a curated Onboarding collection that a new contributor "
        "or LLM agent reads first. "
        "Write concise, navigable prose grounded in the structured signals supplied. "
        "Do not invent file paths, symbol names, or rationale that is not in the context. "
        "Output markdown only — follow the exact section structure the user prompt prescribes."
    ),
}
