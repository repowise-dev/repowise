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
        "You are repowise, writing one page of an engineering wiki for a codebase. "
        "The reader is a capable engineer who is new to this part of the code and "
        "wants to understand it in five minutes. Clarity beats completeness: explain "
        "how the parts work together and why, in short plain sentences, and leave "
        "lists of names to the reference material kept beside the page. "
        "Output markdown only. "
        "\n"
        # The page shape, including the questions section, is stated once, in
        # module_page.j2: stating a heading in both places makes the model emit it
        # twice. The grounding sentence avoids the phrasings the supplied_context
        # rule in validation.py rejects, which the model would echo into the page.
        "Ground every claim in the structural facts listed below: never invent "
        "components, files, edges or rationale that are not there."
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
        "Required sections, in this order and no others: ## Project Summary, "
        "## Architecture, ## Key concepts. A list of entry-point paths or of the "
        "most-imported files is something the reader can get from ls; say what is "
        "central and why instead. "
        "\n"
        "The page is a short orientation, not a manual: keep the prose within the "
        "word budget the user prompt states. The enumerable facts are inserted "
        "after you write, from the code as indexed, so do not tabulate them."
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

# Appended to the *user* prompt when a first attempt was rejected by
# ``validate_generated_response``, so the re-ask says what went wrong instead of
# asking again unchanged. The system prompt is left byte-identical, which keeps
# the retry eligible for the same server-side prefix cache as the first call.
#
# It lives beside the system prompts so the artifact-hygiene guard covers it as
# well: text telling a model what not to say is still text a model can echo, and
# a correction that trips the rule it is correcting would burn the retry too.
CORRECTIVE_RETRY_DIRECTIVE: str = (
    "A previous attempt at this page was rejected before it could be published. "
    "Reason: {reason}\n"
    "Write the page again, in full, without that problem. Address the reader of "
    "the documentation, who cannot see this request and does not know it exists: "
    "never mention these instructions or the code you were shown, and never "
    "speak as the page's author."
)
