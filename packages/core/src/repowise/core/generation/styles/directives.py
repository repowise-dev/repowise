"""Style directive text — the actual voice instructions for each built-in style.

Kept separate from the wiring in ``registry.py`` so the prose a style injects is
easy to read, diff, and tune in one place. Each constant is the ``user_directive``
(prepended to the LLM user prompt) or ``system_note`` (appended to the system
prompt) for a style.

These are the load-bearing content of the feature: editing a directive changes the
style's ``fingerprint`` (see ``spec.py``), which regenerates affected pages on the
next update. Keep them concrete and example-driven — the model follows showed
behaviour far better than abstract adjectives.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# caveman — token-condensed, AI-first
# ---------------------------------------------------------------------------

CAVEMAN_SYSTEM_NOTE = (
    "STYLE OVERRIDE — caveman: this page is read by an LLM agent, not a human. "
    "Optimise for facts-per-token. Be terse and dense, never comprehensive prose. "
    "Preserve accuracy and every required section heading; cut everything else."
)

CAVEMAN_DIRECTIVE = (
    "Write this page in CAVEMAN style — maximally token-condensed for an AI reader.\n"
    "- Keep every required `##` heading, but write the body as terse fragments, not sentences.\n"
    "- Drop articles (a/an/the), linking verbs (is/are/was), and filler words.\n"
    "- One fact per line. Prefer bullet lists and `key -> value` arrows over prose.\n"
    "- Use compact notation: `->` for flows-to/returns, `&` for and, `w/` for with.\n"
    "- No hedging, no restating the heading, no adjectives for flavour, no closing summary.\n"
    "- Keep code identifiers, file paths, and signatures verbatim — never abbreviate those.\n"
    "- Aim for ~60-75% fewer tokens than normal prose while preserving every fact.\n"
    "Example — instead of: 'This module is responsible for parsing the user's "
    "configuration files and validating them against the schema.'\n"
    "Write: 'Parses user config files -> validates vs schema.'"
)

# ---------------------------------------------------------------------------
# reference — API-manual, signature-dense, minimal narrative
# ---------------------------------------------------------------------------

REFERENCE_SYSTEM_NOTE = (
    "STYLE OVERRIDE — reference: write like an API reference manual. Lead with the "
    "contract (signatures, parameters, returns). Document the public surface "
    "exhaustively and precisely; omit motivation and tutorial narration."
)

REFERENCE_DIRECTIVE = (
    "Write this page in REFERENCE style — an API manual, not a narrative.\n"
    "- Keep every required `##` heading.\n"
    "- For each symbol: signature first, then a one-line purpose, then Parameters, "
    "Returns, and Raises as compact lists or tables.\n"
    "- Document the public surface exhaustively; mention internals only when they "
    "affect callers.\n"
    "- Prefer tables for parameters and returns. Keep types and default values exact.\n"
    "- Neutral, precise, present tense. No second person, no motivation prose, no marketing.\n"
    "- Do not invent symbols, parameters, or behaviour absent from the context."
)

# ---------------------------------------------------------------------------
# tutorial — narrative, beginner-friendly, teaches the codebase
# ---------------------------------------------------------------------------

TUTORIAL_SYSTEM_NOTE = (
    "STYLE OVERRIDE — tutorial: write for a developer new to this codebase. Teach, "
    "don't merely describe — build understanding step by step while staying strictly "
    "accurate to the supplied context."
)

TUTORIAL_DIRECTIVE = (
    "Write this page in TUTORIAL style — guided and beginner-friendly.\n"
    "- Keep every required `##` heading. Within each section, build understanding "
    "step by step: what it does, why it exists, how it fits the bigger picture.\n"
    "- Use plain language and short worked examples grounded only in the supplied context.\n"
    "- Define jargon on first use. Add a one-line 'In short:' takeaway where it helps.\n"
    "- Friendly second person ('you') is welcome. Stay accurate; do not pad or repeat.\n"
    "- Never invent APIs, file paths, or behaviour that is not present in the context."
)
