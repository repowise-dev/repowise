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

# How many question-named symbol bodies get_answer inlines in `symbol_bodies`.
# The hydrator already reads these bodies live for synthesis; surfacing them
# in the response collapses the get_answer -> get_symbol drill-down on
# "how does X work" / "explain method Y" questions (the agent gets X's body in
# the same call). Bounded so the body block can't dominate the cached prefix.
_INLINE_BODY_MAX_SYMBOLS = 2
# Line cap for an inlined symbol_bodies block. Larger than the synthesis
# excerpt (_MATCHED_SYMBOL_SOURCE_LINES) because this body is for the agent,
# not the LLM prompt: a docstring-heavy definition (e.g. get_symbol) burns the
# 40-line synthesis cap on docstring and truncates the actual logic, forcing
# the get_symbol follow-up the field exists to remove. 120 serves the whole
# body of ~99% of symbols in one shot; the rest carry a continuation token.
_INLINE_BODY_MAX_LINES = 120

# Synthesis-body depth for the top question-relevant symbols. The default
# _MATCHED_SYMBOL_SOURCE_LINES (40) excerpt cuts a docstring-heavy definition
# off before its answer-bearing logic reaches the LLM, so synthesis hedges
# ("not in the excerpts") on the very symbol whose full 120-line body the
# response then inlines in symbol_bodies — the excerpt the LLM saw and the body
# the agent gets were different depths. Read the top few matched symbols at the
# inline-body depth so what synthesis reasons over matches what the response
# serves. Bounded so a class-name flood (every sibling method "matches" through
# the parent's qualified name) can't balloon the synthesis prompt; the rest keep
# the cheap 40-line excerpt.
_SYNTH_FULL_SOURCE_LINES = _INLINE_BODY_MAX_LINES
_SYNTH_FULL_BODY_MAX_SYMBOLS = 2

# Answer-by-union (homonym exact-name lookup). When a question names a symbol
# with N>=2 defs that no qualifier disambiguates (`_severity_for` x 4), get_answer
# inlines the UNION of their bodies instead of a best_guesses pointer list — the
# pointer list is exactly what triggers the agent's get_symbol/get_context drill.
# Bodies render greedily under this char budget (mirrors the get_context budget
# philosophy); defs that don't fit are listed file:line with a "call get_symbol,
# do NOT Read" redirect. First def always renders even if it alone exceeds budget.
_HOMONYM_UNION_CHAR_BUDGET = 12000
# Line cap per union body — same rationale as _INLINE_BODY_MAX_LINES.
_HOMONYM_UNION_BODY_MAX_LINES = 120
# Ceiling on how many same-named defs a *prose* question may answer-by-union.
# The union is for a small set of genuine parallel implementations of one concept
# (``_severity_for`` has 4 across the biomarkers); past a handful, the name is a
# generic method implemented on many unrelated classes (``to_dict`` x33,
# ``from_dict`` x26, ``provider_name`` x12), and inlining every body as a
# confidence=high answer buries the actual question. So a prose question that
# merely *mentions* such a name (measured: "how does a wiki page get its
# provider_name during indexing?" dumped 12 unrelated provider stubs) falls
# through to synthesis, which grounds in the file the question is really about.
# An explicit symbol lookup (a bare name, where prose does not dominate) still
# unions at any count — that caller asked for every definition. The gap between a
# genuine union (<=4 seen) and a generic method (>=12 seen) is wide, so this is
# not tuned to an exact count.
_HOMONYM_UNION_PROSE_DEF_CEILING = 6

# Data-shape grounding. "what fields does each entry in <blob> contain" is
# answered directly by mining the field set from source instead of gating to a
# best_guesses pointer list (the pointer list is exactly what triggers the
# agent's Read/get_symbol drill). Two grounding sources, precision-ordered:
#   * a documented {...} shape in a docstring/comment near the identifier
#     (authoritative -> confidence high);
#   * consistent key accesses (VAR.get("f") / VAR["f"]) on the value bound from
#     the identifier (access-mined -> confidence medium).
# Every reported field is a quoted token lifted verbatim from source, so the
# path cannot synthesise a field with no source backing (no confidently-wrong
# shapes). Returns nothing (falls through to normal retrieval) unless a shape
# is genuinely grounded.
# Cap on files scanned for the shape. A specific blob name can be referenced
# across dozens of files (every consumer), and the one file that *documents* the
# shape need not sort first, so the cap is generous - source files are small and
# the doc scan must not miss the documenting file. Non-test files are scanned
# first (fixtures document nothing), so the cap mostly trims trailing tests.
_DATA_SHAPE_MAX_FILES = 30  # cap the identifier grep fan-out
_DATA_SHAPE_DOC_WINDOW = 6  # lines below an identifier mention to scan for a {...}
_DATA_SHAPE_ACCESS_WINDOW = 40  # lines below a binding to mine VAR key accesses
_DATA_SHAPE_MIN_FIELDS = 2  # never answer from a single-field shape (too weak to trust)
_DATA_SHAPE_MIN_IDENT_LEN = 6  # identifier must be specific, not a bare generic name
_DATA_SHAPE_GREP_TIMEOUT_S = 6.0

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

# Agreement-aware dominance. The dominance ratio above is computed on
# RRF-fused scores, and RRF *compresses*: a page both retrievers rank #1
# (2/60) barely outscores one they rank #2 (2/61) — ratio ~1.017, far below
# _DOMINANCE_RATIO — so the numeric gate calls the *most* confident retrieval
# (both retrievers agree on the top page) "non-dominant". These knobs let the
# grade read the per-source ranks directly: when FTS and vector independently
# put the SAME page at (or within one rank of) the top and the runner-up is
# meaningfully weaker, that consensus is treated as dominance the ratio can't
# see. Conservative by design — agreement only LIFTS a retrieval to high; the
# six demotion gates still pull it back if the synthesised text is ungrounded.
# The top hit must rank no lower than this in EACH retriever (0-indexed, so
# 1 == "#1 or #2 in both").
_AGREEMENT_TOP_RANK_MAX = 1
# The runner-up must trail the top by at least this many ranks in at least one
# source (when it was found by both). 1 == "top is strictly ahead somewhere".
_AGREEMENT_RANK_GAP = 1

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
    "do not include",
    "does not include",
    "not included in the",
    "can't enumerate",
    "cannot enumerate",
    "can't determine",
    "i can't",
    "i cannot",
    "not shown in",
    "not shown here",
    "material shown",
    "not visible in",
    "unable to determine",
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
# to ground in (vs. one-line summary that's too thin to act on). 1500 chars
# is enough for a page's opening section plus a code reference; at 600 the
# excerpt stopped mid-context and agents fell back to native exploration
# anyway (context-tool bench transcripts, 2026-07-17). Three hits at 1500
# chars is ~1.1k tokens — well under any MCP output budget.
_GATED_EXCERPT_CHARS = 1500
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

# Deterministic template pages (the Phase G coverage tail) are factual but
# thin — they exist so every source file is retrievable, not to out-argue a
# rich LLM page. Multiplicative, not absolute: a deterministic page that is
# genuinely the best hit (its file has no LLM page) still surfaces, preserving
# the coverage the tail adds; the factor only breaks ties toward the LLM page.
_DETERMINISTIC_DOWNWEIGHT = 0.9

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
# v4: confidence calibration gates (value-grounding, citation-source,
# expanded hedge markers). Cached v3 confidence values predate the gates
# and can carry confidently-asserted ungrounded values.
# v5: `symbol_bodies` field — inline full bodies of question-named symbols so
# the consumer skips the get_symbol follow-up. Cached v4 payloads lack it.
# v6: frame-grounding gate — why-answers naming a mechanism term absent from
# the retrieval corpus downgrade high→medium. Cached v5 payloads predate the
# gate and can carry a confidently-asserted, conflated "because X" frame.
# v7: concept anchoring - number-bearing why/value questions anchor the file
# whose comment justifies the number and surface that comment as code_rationale
# even on the high path. Cached v6 payloads predate the anchor + surfacing.
# v8: always-synthesize — retrievals that used to abstain (no dominant page) now
# carry synthesized prose + best_guesses/code_rationale evidence at medium/low
# instead of an empty pointer list. Cached v8 gated (empty-answer) rows must
# bypass so the new answered-with-evidence contract reaches callers.
# v9: agreement-aware confidence — the top hit is graded "dominant/high" when
# both retrievers independently rank it at/near the top, not only when its
# RRF-fused score numerically dominates. RRF compresses scores, so retriever
# agreement (the most confident case) previously graded medium/low. Cached
# pre-v9 rows carry the compressed-ratio grade and must bypass.
# v10: (reserved for the agreement rollout above.)
# v11: answer-grounding calibration — (1) a mechanism/"how" question that merely
# names an indexed symbol no longer short-circuits to an exact_symbol body dump
# (Fix 1); (2) the claim-support gate now covers how-questions, not only why
# (Fix 2); (3) strong answer-grounding earns high on a non-dominant retrieval
# (Fix 4). Cached pre-v11 rows carry the old union-hijack / dominance-only grade
# and must bypass so the recalibrated confidence reaches callers.
_ANSWER_SCHEMA_VERSION = 11

# Hard TTL on answer-cache rows. Commit-based invalidation (the payload's
# stamped ``_indexed_commit`` vs the repo's current head) is the primary
# freshness gate, but rows written before commit stamping existed — or for
# repos without git metadata — need a backstop so they can't serve forever.
_ANSWER_CACHE_TTL_DAYS = 14

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
