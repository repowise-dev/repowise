"""Data models for the repowise generation engine.

These models represent generated wiki pages, configuration, and freshness
tracking.  They are intentionally independent of ingestion models so the
import graph stays one-directional:

    ingestion.models ← generation.models ← context_assembler ← page_generator
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

import structlog

from repowise.core.reasoning import ReasoningMode, normalize_reasoning

logger = structlog.get_logger(__name__)

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
DEFAULT_MAX_TOKENS = 16384
DEFAULT_SOURCE_EVIDENCE_TOKEN_BUDGET = 8000


def _source_evidence_page_keys() -> set[str]:
    """Return synthesis page keys that have a model-written consumer."""
    from .onboarding.slots import ONBOARDING_ORDER, PROMOTED_SLOTS

    promoted = set(PROMOTED_SLOTS.values())
    return {"repo_overview"} | {
        f"onboarding/{slot}" for slot in ONBOARDING_ORDER if slot not in promoted
    }


def _retired_page_keys() -> set[str]:
    """Config keys that named a page which has since been retired.

    A key here was valid in a release the user has already run, so it cannot be
    treated as a typo: raising would turn an upgrade into a failed generation
    for a config that was correct when it was written.  Derived from the
    retirement table rather than listed again, so a slot cannot be retired
    without this following.

    An onboarding page's config key is its ``target_path``, which is the half
    of the page id after the type.
    """
    from .page_redirects import RETIRED_IDS

    return {page_id.split(":", 1)[1] for page_id in RETIRED_IDS if page_id.startswith("onboarding:")}


def _normalize_evidence_files(
    raw_files: Mapping[str, Any], *, label: str
) -> dict[str, tuple[str, ...]]:
    """Validate a page-key -> paths mapping, framing errors with *label*.

    Shared by ``from_repo_config`` (label ``generation_context.files``, the key
    the user wrote) and ``__post_init__`` (label ``source_evidence_files``, the
    internal field for direct construction). The two labels are the whole point
    of the parameter: the same rules, reported against the surface the caller
    touched.

    Sharing one validator also unifies key normalization: both paths now
    ``str(...).strip()`` a key before the membership check. ``from_repo_config``
    always did; direct construction did not, so a padded key like
    ``" repo_overview "`` is now trimmed and accepted where it used to raise.

    A key naming a *retired* page is dropped with a warning rather than raised
    on. The strictness here is aimed at a typo, which is only ever a mistake;
    a retired key is a config that was correct when it was written, and an
    upgrade that turns it into a failed generation punishes the user for a
    decision this project made.
    """
    valid_page_keys = _source_evidence_page_keys()
    retired_page_keys = _retired_page_keys()
    result: dict[str, tuple[str, ...]] = {}
    for raw_page_key, raw_paths in raw_files.items():
        page_key = str(raw_page_key).strip()
        if page_key in retired_page_keys:
            logger.warning(
                "generation_context_key_retired",
                label=label,
                page_key=page_key,
                detail="page has been retired; the entry is ignored, remove it to silence this",
            )
            continue
        if page_key not in valid_page_keys:
            raise ValueError(
                f"{label} keys must name repo_overview or a "
                "model-written onboarding slot; project_overview is configured "
                "as repo_overview"
            )
        if not isinstance(raw_paths, (list, tuple)) or not all(
            isinstance(path, str) and path.strip() for path in raw_paths
        ):
            raise ValueError(f"{label}.{page_key} must be a list of file paths")
        result[page_key] = tuple(path.strip() for path in raw_paths)
    return result


class _FrozenEvidenceFiles(Mapping[str, tuple[str, ...]]):
    """Small immutable mapping that preserves the frozen config contract.

    The config is ``frozen=True`` and hash-tested, so the stored value must be
    hashable and read-only. Backed by a ``MappingProxyType`` over a plain dict:
    O(1) lookup and no in-place mutation, still hashable because ``__hash__``
    runs over ``frozenset(items())`` rather than the proxy. The ``__setattr__``
    guard blocks rebinding ``_items``. ``__reduce__`` stays because a
    ``mappingproxy`` is not picklable on its own; it rebuilds from a plain dict,
    which also serves ``copy``/``deepcopy``. ``__slots__`` and the explicit
    ``__copy__``/``__deepcopy__`` are gone — ``__reduce__`` covers all three.
    """

    def __init__(self, values: Mapping[str, tuple[str, ...]] | None = None) -> None:
        object.__setattr__(
            self,
            "_items",
            MappingProxyType({key: tuple(paths) for key, paths in (values or {}).items()}),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("source_evidence_files is immutable")

    def __getitem__(self, key: str) -> tuple[str, ...]:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return NotImplemented
        return dict(self.items()) == dict(other.items())

    def __hash__(self) -> int:
        return hash(frozenset(self.items()))

    def __reduce__(self) -> tuple[type[_FrozenEvidenceFiles], tuple[dict[str, tuple[str, ...]]]]:
        return type(self), (dict(self),)


# ---------------------------------------------------------------------------
# GenerationConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationConfig:
    """Configuration for the generation engine.

    Use :meth:`to_dict` for a plain, rehydratable public snapshot. Direct
    ``dataclasses.asdict`` output is not a public serialization contract because
    frozen nested values may retain their immutable implementation types.

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

    max_tokens: int = DEFAULT_MAX_TOKENS
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
    #
    # Lowered 0.20 -> 0.10 on the evidence of a large monorepo: a 5.3k-file
    # repo produced 4,996 spotlights inside a 14,027-page wiki. Doubling down
    # on the strongest decile keeps the pages that say something new and drops
    # the ones that restate their file's page. Small repos are unaffected in
    # practice: the selector floors the bucket at one, so a repo with few
    # public symbols still gets a spotlight.
    top_symbol_percentile: float = 0.10
    # The most ``file_page`` pages a run will emit, highest importance score
    # first. Three states, resolved in selection/selector.py:
    #
    #   None (default) -> the volume policy decides. Untouched below
    #                     FILE_PAGE_AUTO_CEILING, held at it above, which is
    #                     about one repo in a hundred.
    #   0              -> explicitly unlimited: one page per eligible file,
    #                     however many that is.
    #   N > 0          -> cap at N.
    #
    # It exists for the top of the size distribution. A 10.8k-file monorepo
    # produced 8,756 file pages inside a 14,027-page wiki, and a file page
    # averages ~8.8 KB with its metadata, so that tail alone is ~77 MB of wiki
    # whose entries mostly restate what their concept page already says. Capping
    # file pages does not reduce model spend -- file pages are rendered from
    # structure and cost no tokens -- it reduces pages, bytes, embedding calls
    # and retrieval dilution.
    max_file_pages: int | None = None
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
    # New fields stay at the end to preserve GenerationConfig's positional
    # constructor contract for direct-library callers.
    # Repository-source excerpts appended to model-written synthesis prompts.
    # The normal context budget above still owns per-file structural assembly;
    # this independent cap keeps high-level evidence bounded and predictable.
    source_evidence_token_budget: int = DEFAULT_SOURCE_EVIDENCE_TOKEN_BUDGET
    # Page key -> explicit repository-relative files to add. Supported keys are
    # ``repo_overview`` and ``onboarding/<slot>``.
    source_evidence_files: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the supported plain, rehydratable configuration snapshot."""
        snapshot = {item.name: deepcopy(getattr(self, item.name)) for item in fields(self)}
        snapshot["source_evidence_files"] = {
            page_key: tuple(paths) for page_key, paths in self.source_evidence_files.items()
        }
        return snapshot

    @classmethod
    def from_repo_config(
        cls,
        config: Mapping[str, Any],
        **overrides: Any,
    ) -> GenerationConfig:
        """Build a generation config with the repo's documentation output limit.

        ``max_tokens`` is the persisted user-facing setting. Keeping its parsing
        here gives CLI, server, and core entry points one owner for translating
        repo configuration into the provider request budget.
        """
        raw_max_tokens = config.get("max_tokens", DEFAULT_MAX_TOKENS)
        if isinstance(raw_max_tokens, bool):
            raise ValueError("max_tokens must be a positive integer")
        if isinstance(raw_max_tokens, int):
            max_tokens = raw_max_tokens
        elif isinstance(raw_max_tokens, str) and raw_max_tokens.strip().isdigit():
            max_tokens = int(raw_max_tokens)
        else:
            raise ValueError("max_tokens must be a positive integer")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")

        values: dict[str, Any] = {"max_tokens": max_tokens}
        raw_evidence = config.get("generation_context")
        if raw_evidence is not None:
            if not isinstance(raw_evidence, Mapping):
                raise ValueError("generation_context must be a mapping")

            raw_budget = raw_evidence.get("token_budget", DEFAULT_SOURCE_EVIDENCE_TOKEN_BUDGET)
            if isinstance(raw_budget, bool) or not isinstance(raw_budget, int) or raw_budget < 0:
                raise ValueError("generation_context.token_budget must be a non-negative integer")
            values["source_evidence_token_budget"] = raw_budget

            raw_files = raw_evidence.get("files", {})
            if not isinstance(raw_files, Mapping):
                raise ValueError("generation_context.files must be a mapping")
            values["source_evidence_files"] = _normalize_evidence_files(
                raw_files, label="generation_context.files"
            )

        values.update(overrides)
        return cls(**values)

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or self.max_tokens <= 0
        ):
            raise ValueError("max_tokens must be a positive integer")
        if self.embed_concurrency is None:
            object.__setattr__(self, "embed_concurrency", self.max_concurrency)
        if (
            isinstance(self.source_evidence_token_budget, bool)
            or not isinstance(self.source_evidence_token_budget, int)
            or self.source_evidence_token_budget < 0
        ):
            raise ValueError("source_evidence_token_budget must be a non-negative integer")
        if not isinstance(self.source_evidence_files, Mapping):
            raise ValueError("source_evidence_files must be a mapping")
        evidence_files = _normalize_evidence_files(
            self.source_evidence_files, label="source_evidence_files"
        )
        object.__setattr__(
            self,
            "source_evidence_files",
            _FrozenEvidenceFiles(evidence_files),
        )
        object.__setattr__(self, "reasoning", normalize_reasoning(self.reasoning))


# ---------------------------------------------------------------------------
# GeneratedPage
# ---------------------------------------------------------------------------

# What a page's ``confidence`` says, and why there are exactly three values.
#
# The column stood at a constant 1.0 on every page a run produced. A constant
# cannot gate anything: retrieval could not weight by it, the reader UI's
# low-confidence banner had never once rendered, and a wiki where a provider
# outage left hundreds of stubs looked exactly as trustworthy as a complete
# one. These three values are the distinctions that are actually available at
# the moment a page is written, and no more than that — a finer scale would be
# a number with nothing behind it.
#
# The axis is *trust*, not completeness. A page can be thin and still be
# entirely true, and the two questions have separate carriers: whether a model
# has written a page yet is ``provider_name`` (see ``MODEL_WRITTEN_PAGE_TYPES``
# and the reader's upgrade affordance), and confidence stays out of it.
# Collapsing the two is what this comment block previously got wrong; see
# ``STUB_PAGE_CONFIDENCE``.

#: A page whose statements all came from the parse, the import graph or git
#: history, with no model in the loop: file pages, symbol spotlights, and the
#: deterministic renderings of the four model-written types produced by a
#: keyless run. There is nothing on the page to be unsure about.
TEMPLATE_PAGE_CONFIDENCE = 1.0

#: A page a model wrote from assembled material. It is grounded in that
#: material and checked against it, but it is a summary of the code rather
#: than an extraction from it, so it is not the same claim a template page
#: makes. Nothing gates on the difference; it is reported, not enforced.
MODEL_PAGE_CONFIDENCE = 0.8

#: The structural stub substituted for a model page whose provider call
#: **failed**, and only that. Paired with :data:`STUB_FALLBACK_ERROR`, which
#: carries the error; :func:`is_stub_fallback` is the predicate.
#:
#: This deliberately no longer covers the keyless run. Both cases render the
#: same template, so stamping both 0.3 read as "0.3 is what a template module
#: page is worth". A keyless run produces *every* model-written page that way,
#: so the reader's banner landed on the repository overview and all of its
#: subsystem pages at once, on precisely the wikis with nothing wrong with
#: them: an index built without a key is that shape by design, and the product
#: told its reader to distrust all of it.
#:
#: What survives is the case the number was introduced for: a page that was
#: meant to carry prose and lost it to an outage. That page is a stand-in for
#: something the run intended and failed to produce, and it is the one state a
#: reader cannot infer from the page itself. The keyless rendering stands in
#: for nothing. It is the finished deterministic page, and it takes
#: :data:`TEMPLATE_PAGE_CONFIDENCE`, exactly as ``_model_free_onboarding_page``
#: already argued for the subkinds it renders without a model.
STUB_PAGE_CONFIDENCE = 0.3


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
        confidence:       How far this page's statements can be trusted, set
                          at generation from how the page was written.  See
                          the three constants below.
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

# Metadata key holding the provider error a page fell back to its stub over.
# Present only on a stub the run substituted for a model page whose provider
# call raised, which is not the same thing as a stub a deterministic run meant
# to write. The distinction is what lets the level runner record the page as
# failed while still handing back a row.
STUB_FALLBACK_ERROR = "stub_fallback_error"


def is_stub_fallback(page: Any) -> bool:
    """True when *page* is a stub standing in for a failed provider call."""
    return STUB_FALLBACK_ERROR in (getattr(page, "metadata", None) or {})


def count_stub_fallbacks(pages: Iterable[Any]) -> int:
    """How many of *pages* the model never actually wrote.

    Every writer of a ``GenerationJob`` row needs this same split, because a
    stub is in ``generated_pages`` like any other page: counting the list is
    what would let a run that lost half its pages report a clean sweep. Four
    call sites deriving it separately is four chances for one of them to drift
    back into counting a failure as a success, which is the bug itself.
    """
    return sum(1 for page in pages if is_stub_fallback(page))


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
