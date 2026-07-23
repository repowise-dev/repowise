"""Data models for the repowise generation engine.

These models represent generated wiki pages, configuration, and freshness
tracking.  They are intentionally independent of ingestion models so the
import graph stays one-directional:

    ingestion.models ← generation.models ← context_assembler ← page_generator
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from repowise.core.reasoning import ReasoningMode, normalize_reasoning

# ---------------------------------------------------------------------------
# PageType and generation levels
# ---------------------------------------------------------------------------

PageType = Literal[
    "api_contract",
    "symbol_spotlight",
    "file_page",
    "scc_page",
    "module_page",
    "layer_page",
    "repo_overview",
    "architecture_diagram",
    "infra_page",
    # Phase 3: onboarding collection (subkind in metadata).
    "onboarding",
]

# Maps PageType → generation level (0 = first, 8 = last).
# Onboarding runs last so it can reference module/file pages already in the
# wiki and so its prompts see the freshest signal bundle.
GENERATION_LEVELS: dict[str, int] = {
    "api_contract": 0,
    "symbol_spotlight": 1,
    "file_page": 2,
    "scc_page": 3,
    "module_page": 4,
    "layer_page": 5,
    "repo_overview": 6,
    "architecture_diagram": 6,
    "infra_page": 7,
    "onboarding": 8,
}

FreshnessStatus = Literal["fresh", "stale", "expired", "unknown"]


# ---------------------------------------------------------------------------
# GenerationConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationConfig:
    """Configuration for the generation engine.

    Attributes:
        max_tokens:               Max tokens in LLM completion.
        temperature:              Sampling temperature (0.3 for consistent docs).
        token_budget:             Context tokens fed to LLM (not output).
        max_concurrency:          asyncio.Semaphore size for parallel calls.
        embed_concurrency:        asyncio.Semaphore size for vector-store writes.
                                  Defaults to max_concurrency.
        reasoning:                Provider-level reasoning intent.
        cache_enabled:            In-memory SHA256 prompt deduplication.
        staleness_threshold_days: Days before a page is considered stale.
        expiry_threshold_days:    Days before a page is considered expired.
        top_symbol_percentile:    Top N% by PageRank → symbol_spotlight.
        jobs_dir:                 Directory for job checkpoint JSON files.
    """

    max_tokens: int = 16384
    temperature: float = 0.3
    token_budget: int = 48000
    max_concurrency: int = 12
    embed_concurrency: int | None = None
    reasoning: ReasoningMode = "auto"
    cache_enabled: bool = True
    staleness_threshold_days: int = 7
    expiry_threshold_days: int = 30
    # ---- Page selection (enforced by generation.selection) -----------
    # Nothing below the concept tree costs tokens any more, and the concept
    # partition is a total cover of the production files, so there is no
    # budget to divide: ``select_pages`` takes every candidate that clears
    # its bucket's floor. What survives here are the floors themselves.
    #
    # ``coverage_pct`` and ``max_pages_pct`` no longer reach selection. They
    # are still carried because the CLI flag, the config round-trip and the
    # server's ranked-generate request body all still write them, and those
    # surfaces are retired together in their own change rather than piecemeal
    # here. Nothing reads them to decide what gets a page.
    coverage_pct: float = 0.20
    max_pages_pct: float = 0.20
    #
    # The fraction of public symbols, highest PageRank first, that get a
    # ``symbol_spotlight``. A spotlight repeats what its file's page already
    # says, so taking all of them buries the pages that say something new.
    # This bound used to be a side effect of the budget's 0.15 share.
    top_symbol_percentile: float = 0.20
    file_page_top_percentile: float = 0.10
    file_page_min_symbols: int = 1
    skip_trivial_files: bool = True
    dedupe_near_clones: bool = True
    # Phase 3: emit the curated Onboarding collection at level 8. Each
    # subkind defines its own gate; slots whose gates fail are silently
    # skipped (no UI nav entry either).
    enable_onboarding: bool = True
    # When True, file_page generation runs a vector-store search (one
    # embedder round-trip per page) to inject related-page snippets into
    # the prompt. On cheap models the extra latency is often more costly
    # than the marginal quality lift — turn off to skip the search.
    # See also rag_min_store_size below for the auto-bypass on small stores.
    enable_rag_context: bool = True
    # RAG search is bypassed entirely until the vector store has at least
    # this many pages. The first wave of file_page generation runs against
    # an empty / nearly-empty store anyway, so the search is a wasted
    # round-trip until enough content is indexed to return useful hits.
    rag_min_store_size: int = 10
    # Phase 2: harvest candidate architectural decisions from Tier-1 LLM page
    # generation (file pages). On by default, escapable via
    # ``--no-harvest-decisions``. The model is instructed to emit a decision
    # block only on a genuine hit, so the output-token cost lands only on files
    # that carry a decision; harvested candidates pass the same substring gate
    # as every other source before storage.
    harvest_decisions: bool = True
    # ---- In-loop self-repair (hallucinated symbol refs) ----------------
    # When the post-generation validator flags at least this many backtick
    # identifiers that do not exist in the documented file, the tier-1 file
    # page is re-generated ONCE with the invalid refs named in a corrective
    # note, and the cleaner of the two drafts is kept. 0 disables the retry.
    # Pages reused from a prior run are never retried (validated back then).
    repair_warning_threshold: int = 2
    jobs_dir: str = ".repowise/jobs"
    large_file_source_pct: float = 0.4  # use structural summary when source tokens > budget * this
    language: str = "en"
    # Wiki documentation style (voice/density). Resolved to a StyleSpec by
    # ``generation.styles.resolve_style``. "comprehensive" (default) is inert and
    # reproduces the pre-style-feature output exactly. A style change folds into
    # each page's source_hash, so `repowise update` regenerates affected pages in
    # the new style. See generation/styles/ and WIKI_STYLES_PLAN.md.
    wiki_style: str = "comprehensive"
    # ---- Prose on the synthesis pages ---------------------------------
    # When True, the page types whose value is synthesis (module_page,
    # repo_overview, architecture_diagram, onboarding) render a thin
    # structural stub instead of being written by a model: no provider call,
    # no tokens, no key. This is what a keyless ``repowise init`` produces.
    # Every other page type is rendered from structure either way, so this
    # flag decides how much a page says and never whether it exists: a keyed
    # and a keyless index of the same commit have the same page set, and
    # their file layers are byte-identical.
    #
    # One axis, in other words. Adding a key later fills in the writing on
    # exactly these four types and changes nothing else.
    deterministic: bool = False

    # ---- Incremental regeneration: file-level pages only ---------------
    # Levels 3 to 8 (cycles, modules, layers, repo overview, architecture
    # diagram, infra, onboarding) describe the repository, not a file. They
    # render from the graph and from ``parsed_files``, and an incremental run
    # holds only the files that changed, so letting them run would overwrite a
    # whole-repo page with a view of one commit: a codebase map with no
    # directories, a module page claiming one file. Their inputs are also
    # unchanged by most commits, so the work is wasted as well as wrong.
    # Set by every incremental path (deterministic and LLM), which regenerates
    # the changed files' pages and leaves the repo-wide ones for a full run.
    #
    # This is the coarse, structural sibling of ``generate_all``'s per-call
    # ``only_page_ids``: it stops the level ladder entirely rather than
    # building levels 3-8 and filtering them to an empty set, so an incremental
    # run does no repo-wide work at all. ``only_page_ids`` is the general form
    # (emit an arbitrary subset from the complete repo view) and is what
    # ``repowise generate`` uses to refresh those repo-wide pages on demand.
    file_pages_only: bool = False

    def __post_init__(self) -> None:
        if self.embed_concurrency is None:
            object.__setattr__(self, "embed_concurrency", self.max_concurrency)
        object.__setattr__(self, "reasoning", normalize_reasoning(self.reasoning))


# ---------------------------------------------------------------------------
# GeneratedPage
# ---------------------------------------------------------------------------


@dataclass
class GeneratedPage:
    """A single wiki page produced by the generation engine.

    Attributes:
        page_id:          Deterministic ID: "{page_type}:{target_path}".
        page_type:        One of the PageType literals.
        title:            Human-readable page title.
        content:          Raw markdown content from the LLM.
        source_hash:      SHA256 of the user_prompt (used for freshness).
        model_name:       LLM model identifier (e.g. "claude-sonnet-4-6").
        provider_name:    Provider identifier (e.g. "anthropic", "mock").
        input_tokens:     Prompt tokens consumed.
        output_tokens:    Completion tokens produced.
        cached_tokens:    Tokens served from provider cache.
        generation_level: Numeric generation level (0-7).
        target_path:      File/module/SCC this page documents.
        created_at:       ISO-8601 UTC timestamp.
        updated_at:       ISO-8601 UTC timestamp.
        confidence:       Decay score (1.0 = fresh, 0.0 = expired).
        freshness_status: Current freshness state.
        metadata:         Provider-specific or page-type-specific extras.
    """

    page_id: str
    page_type: str  # PageType literal
    title: str
    content: str
    source_hash: str
    model_name: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    generation_level: int
    target_path: str
    created_at: str  # ISO-8601 UTC
    updated_at: str  # ISO-8601 UTC
    confidence: float = 1.0
    freshness_status: str = "fresh"  # FreshnessStatus literal
    metadata: dict[str, object] = field(default_factory=dict)
    # Cross-run reuse KEY (not a plain file hash): SHA256 of the documented
    # file's raw-bytes hash folded with the generation fingerprint (template,
    # system prompt, language, style, harvest flag — see
    # PageGenerator._reuse_content_hash). Empty for pages not built from a
    # single file (module/overview/architecture). Unlike source_hash it is
    # stable across runs for an unchanged file + unchanged settings, so
    # cross-run reuse can key on it even when the rendered prompt (RAG
    # context) drifts.
    content_hash: str = ""
    # 1-3 sentence purpose blurb extracted from the rendered content. Used by
    # MCP get_context as the default narrative payload (content is gated behind
    # include=["full_doc"]).
    summary: str = ""
    # Where this page sits in the wiki. Left unset by generators that do not
    # place their pages; the tree builder fills them in before persistence.
    # See the matching columns on the Page model for what each one means.
    parent_page_id: str | None = None
    display_order: int = 0
    section_number: str | None = None
    structural_key: str | None = None

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens


# ---------------------------------------------------------------------------
# ConfidenceDecayResult
# ---------------------------------------------------------------------------


@dataclass
class ConfidenceDecayResult:
    """Result of applying confidence decay to a GeneratedPage."""

    page_id: str
    old_confidence: float
    new_confidence: float
    freshness_status: str  # FreshnessStatus literal
    days_since_update: int


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def compute_page_id(page_type: str, target_path: str) -> str:
    """Return a deterministic page ID: '{page_type}:{target_path}'."""
    return f"{page_type}:{target_path}"


# Page types identified by their members or by a curated id rather than by a
# file path. Two things key on this and they must not drift apart: generation
# stamps ``structural_key`` on these pages, and the persist layer sweeps them,
# because an identity that can move strands the old row as a duplicate on the
# next index. A new page type of this shape belongs here and nowhere else.
STRUCTURALLY_KEYED_PAGE_TYPES: tuple[str, ...] = ("module_page", "layer_page", "scc_page")

# The page types a model writes as prose. Every other type renders from
# structure and is permanently ``provider_name='template'``, so "does this page
# have prose yet" is only a meaningful question for these four. A stub is one of
# these still stamped ``template``; a written one carries a real provider. The
# CLI keeps its own mirror in ``generate_cmd/engine.py``.
MODEL_WRITTEN_PAGE_TYPES: frozenset[str] = frozenset(
    {"module_page", "repo_overview", "architecture_diagram", "onboarding"}
)


def member_structural_key(members: Iterable[str], *, prefix: str) -> str:
    """Return a stable identity for a page defined by the files it covers.

    A page that groups files has no name of its own, so anything derived from
    its position in a list, or from a title someone might rewrite, moves
    between runs. A moved id means the update path deletes and recreates the
    page instead of updating it, losing its history, and leaves the old row
    behind as a duplicate.

    Hashing the sorted member paths ties the identity to the one thing that
    actually says which page this is. It survives a re-ordering of the
    members, an unrelated group appearing or disappearing, a change of
    grouping algorithm, and any amount of re-titling.

    Adding or removing a member deliberately does change the key: the page now
    covers a different thing, so the old identity should be retired rather
    than quietly reused.
    """
    digest = hashlib.sha256("\n".join(sorted(members)).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


def scc_page_slug(members: list[str]) -> str:
    """Return the ``target_path`` for a cycle's ``scc_page``, keyed by contents.

    The original case for :func:`member_structural_key`: a cycle is identified
    by its members and nothing else.
    """
    return member_structural_key(members, prefix="scc")


def _parse_datetime(ts: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp to a timezone-aware datetime."""
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def compute_freshness(
    page: GeneratedPage,
    current_source_hash: str,
    config: GenerationConfig,
    as_of: datetime | None = None,
) -> str:
    """Determine the freshness status of a page.

    Args:
        page:                The page to evaluate.
        current_source_hash: SHA256 of the current user_prompt.
        config:              GenerationConfig with threshold settings.
        as_of:               Reference datetime (defaults to now UTC).

    Returns:
        FreshnessStatus: "fresh", "stale", or "expired".
    """
    if as_of is None:
        as_of = datetime.now(UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    updated = _parse_datetime(page.updated_at)
    days = (as_of - updated).total_seconds() / 86400.0

    # Expiry takes priority
    if days >= config.expiry_threshold_days:
        return "expired"

    # Hash mismatch → stale
    if page.source_hash != current_source_hash:
        return "stale"

    # Age threshold
    if days >= config.staleness_threshold_days:
        return "stale"

    return "fresh"


def decay_confidence(
    page: GeneratedPage,
    config: GenerationConfig,
    as_of: datetime | None = None,
) -> ConfidenceDecayResult:
    """Apply linear confidence decay based on page age.

    Confidence decays linearly from 1.0 to 0.0 over expiry_threshold_days.

    Args:
        page:   The page to evaluate.
        config: GenerationConfig with threshold settings.
        as_of:  Reference datetime (defaults to now UTC).

    Returns:
        ConfidenceDecayResult with old/new confidence and freshness status.
    """
    if as_of is None:
        as_of = datetime.now(UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    updated = _parse_datetime(page.updated_at)
    days = (as_of - updated).total_seconds() / 86400.0
    days_since = int(days)

    # Linear decay: 1.0 → 0.0 over expiry_threshold_days
    new_confidence = max(0.0, 1.0 - days / config.expiry_threshold_days)

    if days >= config.expiry_threshold_days:
        freshness: str = "expired"
    elif days >= config.staleness_threshold_days:
        freshness = "stale"
    else:
        freshness = "fresh"

    return ConfidenceDecayResult(
        page_id=page.page_id,
        old_confidence=page.confidence,
        new_confidence=new_confidence,
        freshness_status=freshness,
        days_since_update=days_since,
    )


def compute_source_hash(text: str) -> str:
    """Return the SHA-256 hex digest of *text* (used as source_hash)."""
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Git-informed confidence decay (Phase 5.5)
# ---------------------------------------------------------------------------


def compute_confidence_decay_with_git(
    base_decay: float,
    relationship: str,
    git_meta: dict | None,
    commit_message: str | None,
) -> float:
    """Apply git modifiers multiplicatively on base decay.

    Args:
        base_decay: Base decay factor (e.g. 0.85 for direct).
        relationship: "direct", "1hop", or "2hop".
        git_meta: Git metadata dict for the file (may be None).
        commit_message: The commit message that triggered the change (may be None).

    Returns:
        Modified decay factor.
    """
    result = base_decay

    if git_meta:
        is_hotspot = git_meta.get("is_hotspot", False)
        is_stable = git_meta.get("is_stable", False)

        # Hotspot: decays faster
        if is_hotspot:
            if relationship == "direct":
                result *= 0.94
            elif relationship == "1hop":
                result *= 0.95

        # Stable: decays slower
        if is_stable and relationship == "direct":
            result *= 1.03

    if commit_message:
        msg_lower = commit_message.lower()
        # Large changes: hard decay
        if any(kw in msg_lower for kw in ("rewrite", "refactor", "migrate")):
            if relationship == "direct":
                result *= 0.71
            elif relationship == "1hop":
                result *= 0.84
        # Cosmetic changes: soft decay
        elif any(kw in msg_lower for kw in ("typo", "lint", "format")) and relationship == "direct":
            result *= 1.12

    return result
