"""Tuning constants and prompt templates for get_answer.

All of get_answer's knobs live here so the retrieval / synthesis / confidence
modules read like policy applied to data. None are repo-specific: they are
properties of BM25-style retrieval with a coverage re-ranker.
"""

from __future__ import annotations

import logging
import os

from repowise.server.mcp_server._query_terms import STOPWORDS

_log = logging.getLogger("repowise.mcp.answer")

# Top hits enriched with WikiSymbol context. Enriching every hit bloats the
# cached prompt prefix on multi-turn sessions; the agent usually cites top-1.
_ENRICH_TOP_N_HITS = 2
# Symbols per enriched file: more for the top hit, where the answer usually lives.
_MAX_SYMBOLS_TOP_HIT = 10
_MAX_SYMBOLS_PER_HIT = 4

# Symbols whose name matches a question identifier are promoted, get a longer
# docstring and a source excerpt, so the LLM sees the body and not just a stub.
_MATCHED_SYMBOL_DOC_CHARS = 400
_MATCHED_SYMBOL_SOURCE_LINES = 40

# Which symbols survive the per-file cap: scored against the question's content
# terms (name > signature > docstring) so a large file's relevant symbol is
# reachable. Changes which symbols fill the budget, not how many. With no signal
# every score is 0 and the sort falls back to `start_line`.
_RELEVANCE_NAME_WEIGHT = 3
_RELEVANCE_SIG_WEIGHT = 2
_RELEVANCE_DOC_WEIGHT = 1
# Score the docstring's opening prose only, so a long one can't out-score a
# precise name on word count alone.
_RELEVANCE_DOC_CHARS = 400
# The top few relevance-scored symbols get a body excerpt even without an
# identifier match, so prose questions see code. Bounded: this adds prompt text.
_RELEVANT_EXCERPT_MAX_SYMBOLS = 2

# Question-named symbol bodies inlined in `symbol_bodies`, which saves the
# get_symbol follow-up. Bounded so the body block can't dominate the cached prefix.
_INLINE_BODY_MAX_SYMBOLS = 2
# Line cap for an inlined body. Larger than the synthesis excerpt because a
# docstring-heavy definition would otherwise truncate before its logic; bodies
# past the cap carry a continuation token.
_INLINE_BODY_MAX_LINES = 120

# `candidates[].defines`: the definitions each ranked file contains, so a named
# file is not a Grep the agent has to run. Names and line numbers only; the goal
# is substance per served path, not more chars. The budget is spent in rank
# order so the best file is always described, and the per-candidate cap stops a
# large module from consuming it before the second candidate.
_DEFINES_PER_CANDIDATE = 6
_DEFINES_CHAR_BUDGET = 1500
# Matches _CANDIDATE_LIMIT: querying beyond what `candidates` can emit is wasted.
_DEFINES_MAX_FILES = 20

# The top few matched symbols are read at the inline-body depth so synthesis
# reasons over the same body the response serves, instead of hedging on a
# 40-line cut. Bounded so a class-name flood can't balloon the prompt.
_SYNTH_FULL_SOURCE_LINES = _INLINE_BODY_MAX_LINES
_SYNTH_FULL_BODY_MAX_SYMBOLS = 2

# Answer-by-union: when a question names a symbol with N>=2 undisambiguated defs,
# inline the union of their bodies rather than a pointer list that triggers a
# drill-down. Bodies render greedily under this budget; the rest are listed
# file:line. The first def always renders even if it alone exceeds the budget.
_HOMONYM_UNION_CHAR_BUDGET = 12000
_HOMONYM_UNION_BODY_MAX_LINES = 120
# Ceiling on defs a *prose* question may union. A few defs are parallel
# implementations of one concept; many are a generic method on unrelated classes
# (`to_dict`), and unioning them buries the question, so prose falls through to
# synthesis. A bare-name lookup still unions at any count. The gap between the two
# cases is wide, so this is not tuned to an exact count.
_HOMONYM_UNION_PROSE_DEF_CEILING = 6

# Data-shape grounding: "what fields does each entry in <blob> contain" is
# answered by mining the field set from source, precision-ordered:
#   * a documented {...} shape near the identifier (confidence high);
#   * consistent VAR.get("f") / VAR["f"] accesses on the bound value (medium).
# Every field is a token lifted verbatim from source, so no shape is invented;
# nothing is returned unless a shape is genuinely grounded.
# The file cap is generous because the documenting file need not sort first;
# non-test files are scanned first, so the cap mostly trims trailing tests.
_DATA_SHAPE_MAX_FILES = 30  # cap the identifier grep fan-out
_DATA_SHAPE_DOC_WINDOW = 6  # lines below an identifier mention to scan for a {...}
_DATA_SHAPE_ACCESS_WINDOW = 40  # lines below a binding to mine VAR key accesses
_DATA_SHAPE_MIN_FIELDS = 2  # never answer from a single-field shape (too weak to trust)
_DATA_SHAPE_MIN_IDENT_LEN = 6  # identifier must be specific, not a bare generic name
_DATA_SHAPE_GREP_TIMEOUT_S = 6.0

# Classes first: "what does X do" / "which class inherits Y" resolve at class level.
_KIND_PRIORITY = {"class": 0, "interface": 0, "function": 1, "method": 2}
# The first sentence is usually enough; trailing prose is cache-write cost.
_MAX_SYMBOL_DOC_CHARS = 120

# Dominance, lower tier: how far the top hit must outscore the runner-up, as a
# multiple, when neither score is strong enough for the absolute gap below.
# Dominance feeds the confidence grade, retrieval rating and ambiguity caveat;
# it no longer decides whether to synthesise.
_DOMINANCE_RATIO = 1.2
_COVERAGE_THRESHOLD = 0.66

# Dominance, upper tier: between two excellent scores a close ratio is expected
# (6.0 vs 5.4 is a clear win), so above the floor dominance is an absolute gap.
# ``is_dominant`` is the sole reader of all three knobs.
_DOMINANCE_ABS_SCORE_FLOOR = 3.0
_DOMINANCE_ABS_GAP = 0.5

# Agreement-aware dominance. RRF compresses scores, so a page both retrievers
# rank #1 barely outscores one they rank #2 and the ratio calls the most
# confident retrieval "non-dominant". When the retrievers independently put the
# same page at (or near) the top and the runner-up is weaker, that consensus
# counts as dominance. Agreement only lifts; the demotion gates still apply.
# Max 0-indexed rank of the top hit in EACH retriever (1 == "#1 or #2 in both").
_AGREEMENT_TOP_RANK_MAX = 1
# The same ceiling for the FTS+symbol pair a keyless index uses (it has no
# vector leg). Stricter on purpose: FTS and the symbol leg read overlapping text,
# so their agreement is nearer one lexical match counted twice than two
# independent retrievers, and the symbol leg is already priced at a third weight.
_SYMBOL_AGREEMENT_TOP_RANK_MAX = 0
# The runner-up must trail by at least this many ranks in at least one source.
_AGREEMENT_RANK_GAP = 1

# Phrases that mean the LLM declined to answer despite dominant retrieval.
# A match downgrades confidence to "low" and drops the retrieval payload, since
# a consumer told to read the source gains nothing from it in the cache.
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

# Page content chars attached per top hit, for synthesis and the pointer payload.
# Enough for a page's opening section plus a code reference; much less stops
# mid-context and agents fall back to native exploration.
_GATED_EXCERPT_CHARS = 1500

# How many hits the low-confidence payload hands back to the agent.
_GATED_RETURN_HITS = 3

# Top hits that get page content attached. All 5 retrieved hits reach synthesis,
# and a hit with no prose can only be answered from symbol names. This is the
# feature's cost knob; hits below the cut fall back to one-line summaries.
_PAGE_EXCERPT_HITS = 5

# Path-prefix domain heuristics: down-weight cross-domain hits so a backend
# question doesn't anchor on a same-vocabulary UI file (and vice versa). The
# penalty is multiplicative, to break ties rather than censor strong matches.
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
# Question tokens that flag a domain. Kept small; an ambiguous question (both
# lists hit, or neither) gets no penalty rather than a misclassification.
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
# Strong enough to overtake a same-domain near-tie, weak enough that a dominant
# cross-domain hit survives.
_DOMAIN_PENALTY = 0.5

# Floor on the top-hit score for "high": below it a dominant answer rests on
# weak retrieval, so it grades "medium". Useful wiki hits routinely score >1.5.
_HIGH_CONFIDENCE_SCORE_FLOOR = 1.5

# Schema version stamped on every cached payload. Bump whenever the synthesised
# response shape or the meaning of a field (including `confidence` and `note`)
# changes: a cached row at a lower version is a miss and re-synthesises, since
# serving an old-shape row silently hides the change behind a cache hit.
# Degraded (no-provider / synthesis-failed) payloads are never cached, so changes
# confined to them need no bump; a needless bump costs every keyed install a
# round of provider spend.
_ANSWER_SCHEMA_VERSION = 17

# Backstop TTL for cache rows. Commit stamping is the primary freshness gate;
# this covers rows without a stamp (older rows, repos without git metadata).
_ANSWER_CACHE_TTL_DAYS = 14

# Intersection-retrieval connectives (case-insensitive whole-word). A question
# containing one is split on it, both sides are searched, and pages in BOTH
# result sets are boosted: the intersection is likelier the answer than either
# side's top. Grammar, not domain: it applies to any English code question.
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

# Term-coverage re-ranker: BM25 * (FLOOR + (1-FLOOR)*coverage), where coverage is
# the share of distinct query terms present in the hit. Single-concept questions
# are unaffected; on multi-constraint ones coverage is a tie-breaker, not a filter.
_COVERAGE_FLOOR = 0.5
# Shared with the prose-keyed symbol leg so the two lists cannot drift apart.
_STOPWORDS = STOPWORDS
# Line cap on a real signature recovered from disk; get_symbol has the rest.
_MAX_RICH_SIG_LINES = 4

# Synthesis sampling. Answers target 150-400 words, so for a non-reasoning model
# the token cap is headroom. A reasoning model spends the budget on hidden
# thinking first and can come back empty with a ``length`` finish_reason, so the
# env override lets such a model be given room. Low temperature: the answer
# must track the excerpts, not embellish.
_SYNTHESIS_MAX_TOKENS_ENV = "REPOWISE_SYNTHESIS_MAX_TOKENS"
_SYNTHESIS_MAX_TOKENS_DEFAULT = 1024


def _synthesis_max_tokens() -> int:
    """The synthesis token budget, from env or the hosted-model default.

    An unparseable or non-positive value warns and keeps the default instead
    of silently capping synthesis at 0.
    """
    raw = os.environ.get(_SYNTHESIS_MAX_TOKENS_ENV, "").strip()
    if not raw:
        return _SYNTHESIS_MAX_TOKENS_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        _log.warning("Ignoring unusable %s=%r", _SYNTHESIS_MAX_TOKENS_ENV, raw)
        return _SYNTHESIS_MAX_TOKENS_DEFAULT
    return value


_SYNTHESIS_MAX_TOKENS = _synthesis_max_tokens()
_SYNTHESIS_TEMPERATURE = 0.2

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
    "hedge about having enough material (say 'inspect the source' / "
    "'the excerpts do not contain…') when there is genuinely no relevant "
    "signature, docstring, or source body in the excerpts. Never assert "
    "exhaustiveness: state what the retrieved excerpts show, not that "
    "they are the complete set of sites. Unattributed exclusivity "
    "language ('entirely', 'the sole', 'the only place', 'depends only on') "
    "is not justified from a top-k slice — omit it unless the retrieved "
    "material itself (an assertion, a type constraint, or an explicit "
    "comment) says so. Never invent file paths."
)

_USER_TEMPLATE = """\
Question: {question}

Project wiki excerpts (top {n} retrieval hits):

{context}

Answer thoroughly (150–400 words). Cite file paths inline and line
numbers when the excerpt provides them. Prefer a structured layout
(headings, bullets, short code block from the source body) on
mechanism / architecture questions. Only hedge about having enough
material if no signature, docstring, or source body in the excerpts is
relevant. Do not assert that what you were shown is the complete set
of sites; qualify causal claims when a symbol's body was truncated.
"""


# --- Feature flags -----------------------------------------------------------
# Every REPOWISE_ANSWER_* switch, read at call time so a test or eval arm can
# flip one between calls. Each is independently reversible so an A/B can switch
# one without the others.

# Default ON: synthesis runs for every retrieval and grading demotes confidence
# instead of abstaining. Falsey restores abstain-on-ambiguous.
_ALWAYS_SYNTHESIZE_ENV = "REPOWISE_ANSWER_ALWAYS_SYNTHESIZE"
# Retriever-agreement confidence lift (the FTS + vector pair).
_AGREEMENT_CONFIDENCE_ENV = "REPOWISE_ANSWER_AGREEMENT_CONFIDENCE"
# The keyless FTS + symbol pair. Its own flag so it can be reversed without
# switching off the FTS + vector half.
_SYMBOL_AGREEMENT_ENV = "REPOWISE_ANSWER_SYMBOL_AGREEMENT"
# Keep the exact_symbol union fast path from hijacking a "how does X work"
# question, whose answer often lives outside the named symbol's body.
_UNION_MECHANISM_DEFER_ENV = "REPOWISE_ANSWER_UNION_MECHANISM_DEFER"
# Require the answer's central mechanism symbol to be grounded in served source
# before a mechanism/how answer may be stamped high.
_CLAIM_SUPPORT_GATE_ENV = "REPOWISE_ANSWER_CLAIM_SUPPORT_GATE"
# Let the served body of the question-named symbol earn "high" on a
# non-dominant retrieval.
_EARN_HIGH_GROUNDING_ENV = "REPOWISE_ANSWER_EARN_HIGH_GROUNDING"
# Off by default: earning high from prose consistent with the material shown.
# This lift only fires over a weak retrieval (a strong dominant one is already
# high), and frame-term grounding shows the answer invented nothing, not that
# the material was the right material. The served-body lift above can show
# that (exact-name resolution, not ranking), which is why the two are split.
_EARN_HIGH_ON_WEAK_RETRIEVAL_ENV = "REPOWISE_ANSWER_EARN_HIGH_ON_WEAK_RETRIEVAL"

# Skip the answer cache (read and write). The cache keys on (repo, question), not
# flag state, so an A/B flipping the flags above would read the first arm's rows.
_DISABLE_CACHE_ENV = "REPOWISE_ANSWER_DISABLE_CACHE"

# Opt-in: strip re-read evidence from high-confidence answers, whose contract is
# "cite this, do not re-read". Only safe once high answers are reliable enough;
# otherwise the agent calls get_symbol and adds a round-trip.
_LEAN_HIGH_ENV = "REPOWISE_ANSWER_LEAN_HIGH"

# Re-read evidence stripped from a lean high answer. NOT stripped: answer,
# citations, confidence, retrieval_quality, fallback_targets, note, _meta.
_LEAN_HIGH_DROP_KEYS = ("symbol_bodies", "quotes", "flow_path", "best_guesses", "code_rationale")


def _flag_on(env_name: str) -> bool:
    """A REPOWISE_* feature flag: on by default, off for {0,false,no,off}."""
    return os.environ.get(env_name, "").strip().lower() not in {"0", "false", "no", "off"}


def _opt_in(env_name: str) -> bool:
    """A REPOWISE_* feature flag: off by default, on for {1,true,yes,on}."""
    return os.environ.get(env_name, "").strip().lower() in {"1", "true", "yes", "on"}


def _always_synthesize() -> bool:
    """Whether to always synthesize (default) or keep the legacy abstain gate."""
    return _flag_on(_ALWAYS_SYNTHESIZE_ENV)


def _agreement_confidence_enabled() -> bool:
    """Whether retriever-agreement lifts confidence (default) or pure ratio rules."""
    return _flag_on(_AGREEMENT_CONFIDENCE_ENV)


def _symbol_agreement_enabled() -> bool:
    """Whether a keyless index may read agreement from FTS + the symbol leg.

    Independently reversible from :func:`_agreement_confidence_enabled`.
    """
    return _flag_on(_SYMBOL_AGREEMENT_ENV)


def _cache_disabled() -> bool:
    return _opt_in(_DISABLE_CACHE_ENV)


def _lean_high() -> bool:
    return _opt_in(_LEAN_HIGH_ENV)
