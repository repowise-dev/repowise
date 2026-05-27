"""Tuning constants and prompt templates for get_answer.

All of get_answer's knobs live here so the retrieval / synthesis / confidence
modules read like policy applied to data, and the data is tunable in one place.
None of these are repo-specific — they are properties of BM25-style retrieval
with a coverage re-ranker, not of any particular codebase.
"""

from __future__ import annotations

# How many top retrieval hits to enrich with WikiSymbol context. Enriching
# every hit produces large responses that bloat the cached prompt prefix on
# multi-turn agent sessions without changing the answer — the agent typically
# cites the top-1 file. Top-2 captures the primary navigation need with a
# bounded payload.
_ENRICH_TOP_N_HITS = 2
# How many symbols per enriched file. Bounded to keep the context block from
# growing unboundedly on dense files. We allocate more slots to the top hit
# (where the answer usually lives) and fewer to secondary hits.
_MAX_SYMBOLS_TOP_HIT = 10
_MAX_SYMBOLS_PER_HIT = 4

# When a retrieved file contains symbols whose name matches an identifier
# from the question, we promote those to the top of the symbol list for that
# file, pass a longer docstring, and attach a source excerpt so the LLM
# actually sees the method body — not just a stub docstring. Without this,
# specific-method questions get hedged answers even on dominant retrievals.
_MATCHED_SYMBOL_DOC_CHARS = 400
_MATCHED_SYMBOL_SOURCE_LINES = 40

# Sort priority by symbol kind. Classes first because "what does X do" /
# "which class inherits from Y" questions resolve at the class level. Then
# top-level functions, then methods (which usually only matter once the
# class context is established).
_KIND_PRIORITY = {"class": 0, "interface": 0, "function": 1, "method": 2}
# Per-symbol docstring truncation. Keeps the context block bounded — the
# first sentence is typically sufficient and trailing prose mostly contributes
# cache-write cost on follow-up turns.
_MAX_SYMBOL_DOC_CHARS = 120

# Confidence gate for synthesis. When the top retrieval hit is NOT clearly
# dominant relative to the second-best hit, skip LLM synthesis and return
# ranked snippets only. This forces the agent to ground in source rather than
# trust a possibly-wrong frame. Generic, repo-agnostic, no question parsing.
# Failure modes addressed:
#   (a) wrong-target retrieval where top-1 and top-2 are both plausible;
#   (b) synthesis hallucination on tangential top hits.
_DOMINANCE_RATIO = 1.2
_COVERAGE_THRESHOLD = 0.66

# Hedge-phrase markers that indicate the LLM refused to synthesize even though
# retrieval was dominant. When the answer contains any of these, we downgrade
# confidence to "low" and drop the retrieval payload — the hits aren't useful
# to a consumer that has already been told to go read the source, and letting
# them ride through the conversation cache inflates multi-turn cost.
_HEDGE_MARKERS = (
    "do not contain",
    "does not contain",
    "is not contained",
    "are not contained",
    "not contain sufficient",
    "not contain enough",
    "is not covered",
    "not covered in the",
    "not covered by the",
    "you should inspect",
    "you should consult",
    "consult the source",
    "inspect the source",
    "cannot be determined",
    "cannot determine",
    "is not clear",
    "insufficient information",
    "not enough information",
    "without more context",
    "without additional context",
    "didn't surface",
    "did not surface",
    "was not surfaced",
    "was not found in",
)

# The dominance ratio threshold (top_score / second_score >= 1.2x) separates
# reliable retrievals from ambiguous ones. This is a property of BM25-style
# retrieval with a coverage re-ranker on top, not of any particular repository;
# tune if a deployment shows systematic over- or under-gating.

# When the gate triggers and we drop synthesis, fetch this many chars of
# real page content per top hit so the agent has substantive raw material
# to ground in (vs. one-line summary that's too thin to act on).
_GATED_EXCERPT_CHARS = 600
_GATED_RETURN_HITS = 3

# Path-prefix domain heuristics — down-weight cross-domain retrievals so a
# clearly backend question doesn't anchor on a same-vocabulary UI file (and
# vice versa). The penalty is multiplicative, not absolute, so a strongly
# matching cross-domain file can still survive on raw signal; the goal is
# to break ties when retrieval is otherwise ambiguous, not to censor results.
_UI_PATH_PREFIXES = (
    "packages/ui/",
    "packages/web/",
    "frontend/",
    "website/",
)
_BACKEND_PATH_PREFIXES = (
    "packages/server/",
    "packages/core/",
    "packages/cli/",
    "backend/",
    "modal_app/",
)
# Tokens that flag a question as being about a specific domain. Kept small
# and conservative — ambiguous questions (both lists hit, or neither) fall
# through to no penalty rather than misclassify.
_UI_QUESTION_TOKENS = frozenset(
    {
        "ui",
        "frontend",
        "component",
        "react",
        "tsx",
        "jsx",
        "render",
        "css",
        "tailwind",
        "view",
        "dashboard",
        "button",
        "modal",
        "page",
        "browser",
        "client-side",
    }
)
_BACKEND_QUESTION_TOKENS = frozenset(
    {
        "backend",
        "server",
        "api",
        "endpoint",
        "route",
        "indexer",
        "ingest",
        "ingestion",
        "pipeline",
        "database",
        "db",
        "schema",
        "migration",
        "orchestrat",
        "mcp",
        "fastapi",
        "sqlalchemy",
        "subprocess",
        "worker",
        "cli",
        "command",
        "sql",
    }
)
# Penalty factor applied to cross-domain hits. 0.5 is strong enough to
# overtake a same-domain near-tie but small enough that a dominant cross-
# domain hit (real top score outlier) still survives.
_DOMAIN_PENALTY = 0.5

# Floor on raw top-hit score for "high" confidence. Below this the answer
# may be technically dominant but built on weak retrieval — downgrade to
# "medium" so the agent verifies. Tuned against observed BM25 ranges on
# the wiki corpus where useful hits routinely score >1.5.
_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5

# Schema version stamped on every cached payload. Bump whenever the response
# shape changes in a way that would mislead a consumer reading an old cached
# entry (new top-level fields, semantics of existing fields). On cache reads
# we treat any payload at a lower version as a miss and re-synthesise — the
# alternative (returning stale-shape payloads) silently bypasses every
# improvement to the tool and was the failure mode that hid the entire
# get_answer rework behind a cache hit during testing.
# v3: retrieval pipeline overhaul (hybrid FTS+vector, PageRank bias, graph
# expansion, structured prelude, decision fusion). Cached v2 payloads were
# synthesised over weaker retrieval — bumping forces re-synthesis so the
# upgrade actually reaches callers without waiting for cache expiry.
_ANSWER_SCHEMA_VERSION = 3

# Intersection-retrieval connectives. If a question contains any of these
# (case-insensitive whole-word), it's likely a relational/multi-entity
# question. We split the question on the connective, run two FTS passes,
# and boost any page that appears in BOTH result sets — the page at the
# intersection is much more likely to be the actual answer than a page
# at the top of either single-side query.
# This is grammar, not domain — the same list applies to any English-language
# code question, independent of the repository or codebase.
_RELATIONAL_CONNECTIVES = (
    " between ",
    " from ",
    " across ",
    " through ",
    " with ",
    " and ",
    " versus ",
    " vs ",
)

# Term-coverage re-ranker tuning. Multiplies BM25 by (FLOOR + (1-FLOOR)*coverage)
# where coverage = (# distinct query terms present in hit) / (# query terms).
# FLOOR=0.5 → single-concept questions (coverage≈1.0) are unaffected;
# multi-constraint questions where a hit covers 1/3 of terms get scored at 0.67
# of their raw BM25 (vs 1.0 for a hit covering 3/3). Conjunctive coverage
# becomes a tie-breaker rather than a hard filter.
_COVERAGE_FLOOR = 0.5
# English stopwords — minimal list, just enough to keep "what is the" from
# dominating coverage. Not language-specific, not repo-specific.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "and",
        "or",
        "but",
        "not",
        "no",
        "do",
        "does",
        "did",
        "done",
        "have",
        "has",
        "had",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "can",
        "could",
        "should",
        "would",
        "may",
        "might",
        "will",
        "shall",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "their",
        "our",
        "if",
        "then",
        "than",
        "so",
        "such",
        "there",
        "here",
        "about",
        "into",
        "through",
        "between",
        "across",
        "over",
        "under",
        "up",
        "down",
        "out",
        "off",
        "via",
    }
)
# Cap on bytes read from source per symbol when we recover a real signature
# from disk (multi-line def with type annotations). Anything longer than this
# gets truncated; the agent can call get_symbol for the full body.
_MAX_RICH_SIG_LINES = 4

_SYSTEM_PROMPT = (
    "You are a code-aware retrieval assistant. You are given a developer "
    "question plus excerpts from a project wiki — file summaries, symbol "
    "signatures with docstrings, and (for symbols whose name matches the "
    "question) the actual source body. Answer thoroughly and concretely, "
    "citing source files by relative path inline like (path/to/file.py) "
    "and line numbers when you have them. Prefer a structured answer "
    "(headings / bullets / short code block citing the symbol) over a "
    "paragraph when the question asks about mechanism or architecture. "
    "Aim for 150–400 words — enough to cover the asked aspects without "
    "padding. If a [question-match] symbol's source body is provided, "
    "you have enough material to answer — ground in that body. Only "
    "hedge (say 'inspect the source' / 'the excerpts do not contain…') "
    "when there is genuinely no relevant signature, docstring, or source "
    "body in the excerpts. Never invent file paths."
)

_USER_TEMPLATE = """\
Question: {question}

Project wiki excerpts (top {n} retrieval hits):

{context}

Answer thoroughly (150–400 words). Cite file paths inline and line
numbers when the excerpt provides them. Prefer a structured layout
(headings, bullets, short code block from the source body) on
mechanism / architecture questions. Only hedge if no signature,
docstring, or source body in the excerpts is relevant.
"""
