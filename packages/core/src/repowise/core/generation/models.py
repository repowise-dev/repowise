"""Data models for the repowise generation engine.

These models represent generated wiki pages, configuration, and freshness
tracking.  They are intentionally independent of ingestion models so the
import graph stays one-directional:

    ingestion.models ← generation.models ← context_assembler ← page_generator
"""

from __future__ import annotations

import hashlib
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

    max_tokens: int = 20000
    temperature: float = 0.3
    token_budget: int = 48000
    max_concurrency: int = 12
    embed_concurrency: int | None = None
    reasoning: ReasoningMode = "auto"
    cache_enabled: bool = True
    staleness_threshold_days: int = 7
    expiry_threshold_days: int = 30
    # ---- Coverage budget (enforced by generation.selection) ----------
    # ``coverage_pct`` is the single knob users care about: the fraction
    # of repo files that should produce a wiki page. The selection
    # subsystem (``generation.selection``) is the *single source of
    # truth* — it scores every candidate, allocates the budget across
    # buckets via the share fields below, and returns an allow-set that
    # both page_generator and cost_estimator honor verbatim. There is
    # no longer an absolute cap — the percentage scales linearly.
    coverage_pct: float = 0.20
    file_page_share: float = 0.50
    symbol_spotlight_share: float = 0.15
    module_page_share: float = 0.10
    api_contract_share: float = 0.08
    infra_page_share: float = 0.05
    scc_share: float = 0.04
    # ``max_pages_pct`` is kept as a deprecated alias for backwards
    # compatibility — older tests and CLI flows still read it. The
    # selector picks ``coverage_pct`` when set and falls back here.
    max_pages_pct: float = 0.20
    # Legacy percentile knobs are retained for callers that want fine
    # control but no longer drive page selection on their own.
    top_symbol_percentile: float = 0.20
    file_page_top_percentile: float = 0.10
    file_page_min_symbols: int = 1
    skip_trivial_files: bool = True
    dedupe_near_clones: bool = True
    # Module-page grouping source. "curated" (default) groups by the wiki
    # modules the KG curation pass derives (stable path ids, human names,
    # right-sized groups) and silently falls back to "community" when no
    # curated modules are available (curation off, degraded, or no KG
    # artifact), so it is always safe. "community" groups by raw graph
    # communities (the pre-curation behavior, kept as the escape hatch),
    # "top_dir" by top-level directory.
    # min_module_size is the floor below which a group doesn't get its own
    # page (its files still appear under file_page).
    module_grouping: Literal["community", "top_dir", "curated"] = "curated"
    min_module_size: int = 3
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
    # ---- Tiered doc generation (large-repo scale) ---------------------
    # Caps the number of file pages that receive full LLM generation.
    # The top ``tier1_top_n`` selected file pages by PageRank are
    # generated by the LLM (tier-1, the existing path); the remaining
    # selected file pages are rendered from a deterministic Jinja
    # template and embedded for search — with no LLM call (tier-2).
    # ``None`` (default) preserves the prior behaviour exactly: every
    # selected file page is a full tier-1 LLM page.
    tier1_top_n: int | None = None
    # ---- Deterministic coverage tail (Phase G) ------------------------
    # After the budget picks its LLM (tier-1/2) file pages, every REMAINING
    # parsed source file gets a cheap, zero-LLM "deterministic" page (the same
    # template renderer as tier-2), so the whole codebase is retrievable by
    # concept search instead of only the ~20% the budget covers. Proven in
    # dogfood to lift retrieval recall (raw-vector recall@5 0.47 -> 0.67) with
    # no regressions once the tail is importance-floored (test files and pure
    # __init__.py re-exports are always excluded — they only dilute retrieval).
    # On by default; free (no tokens), costs only index size + embeddings.
    tier2_tail_enabled: bool = True
    # Optional cap on how many tail pages to emit (highest-signal first by
    # score). None = every floored candidate. Use to bound index size.
    tier2_tail_cap: int | None = None
    # Optional directory allow-list (repo-relative prefixes). None = all dirs.
    tier2_tail_dirs: tuple[str, ...] | None = None

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
# Git and Dead Code Config (Phase 5.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitConfig:
    """Configuration for git intelligence features."""

    enabled: bool = True
    commit_limit: int = 500
    co_change_min_count: int = 3
    blame_enabled: bool = True
    prompt_commit_count: int = 10
    depth_auto_upgrade: bool = True


@dataclass(frozen=True)
class DeadCodeConfig:
    """Configuration for dead code detection."""

    enabled: bool = True
    detect_unreachable_files: bool = True
    detect_unused_exports: bool = True
    detect_unused_internals: bool = True
    detect_zombie_packages: bool = True
    min_confidence: float = 0.4
    safe_to_delete_threshold: float = 0.7
    dynamic_patterns: tuple[str, ...] = (
        "*Plugin",
        "*Handler",
        "*Adapter",
        "*Middleware",
        "register_*",
        "on_*",
    )
    analyze_on_update: bool = True


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
