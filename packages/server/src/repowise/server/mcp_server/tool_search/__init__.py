"""MCP Tool 5: search_codebase, hybrid codebase search.

``tool.py`` holds the registered tool; the sibling modules hold its stages.
Importing the package registers the tool. Every name the single module had
is re-exported here, and a stage calls a name tests patch through the package
(``_pkg.<name>``), never by bare name, or the patch stops reaching it.
"""

# ruff: noqa: F401

from __future__ import annotations

from sqlalchemy import select

from repowise.core.generation.structural_labels import is_structural_title
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    GitMetadata,
    Page,
)
from repowise.core.persistence.search import strip_leading_headings
from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.core.test_paths import is_test_path, is_test_related_path
from repowise.server.mcp_server._answer_pipeline import (
    _FILENAME_LEG_RRF_K,
    _RRF_K,
    _RRF_SCORE_SCALE,
    _SYMBOL_LEG_RRF_K,
    _safe_filename_search,
    _safe_symbol_search,
    leg_ranks,
)
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    register_post_enforce,
    register_post_shed,
)
from repowise.server.mcp_server._federation import Federation, merge_ranked
from repowise.server.mcp_server._helpers import (
    _VECTOR_TIMEOUT_ENV,
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    attach_ignored_arguments,
    filter_dicts_by_key,
    resolve_enum_argument,
    vector_search_timeout_s,
)
from repowise.server.mcp_server._hit_symbols import attach_hit_symbols
from repowise.server.mcp_server._line_hits import attach_line_hits
from repowise.server.mcp_server._meta import EXHAUSTIVE_SWEEP_HINT, semantic_search_state
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._page_paths import (
    FILE_ROW_TYPES,
    PAGELESS_FILE,
    add_row_paths,
    file_candidates,
    file_path_of,
    hit_file_path,
    pageless_path,
)
from repowise.server.mcp_server._query_shape import (
    _DECISION_DOWNWEIGHT,
    _MIN_RELEVANCE_SCORE,
    _VALID_MODES,
    NOT_THE_NAMED_SYMBOL,
    _canonical_symbol_query,
    _embedded_identifiers,
    _fetch_limit_for,
    _has_exact_symbol,
    _identifier_candidates,
    _is_why_shaped,
    _looks_like_code_name,
    _looks_like_exact_token,
    _mark_not_the_named_symbol,
    _names_a_path,
    _qual_norm,
    _resolve_mode,
    _symbol_matches_name,
    path_tokens,
)
from repowise.server.mcp_server._query_terms import content_terms
from repowise.server.mcp_server._references import path_identity, symbol_identity
from repowise.server.mcp_server._retrieval_rank import (
    boost_named_paths,
    rerank_pages_first,
)
from repowise.server.mcp_server.tool_search.ordering import (
    _FILE_WINDOW_OVERFETCH,
    _MERGED_SYMBOL_CAP,
    _collapse_by_file,
    _hybrid_window,
    _interleave_hybrid,
    _prose_dominates,
    _protect_exact_paths,
    _protect_exact_symbols,
    _protect_named_symbols,
)
from repowise.server.mcp_server.tool_search.ranking import (
    _CONFIG_PATH_TOKENS,
    _SYMBOL_TAIL_DIVISOR,
    _TEST_DOWNWEIGHT,
    _TEST_QUERY_RE,
    _VALID_KINDS,
    _classify_hit_kind,
    _dedup_decisions,
    _downweight_decisions,
    _downweight_test_pages,
    _filter_by_kind,
    _is_test_page,
    _is_test_query,
    _non_decision_fallback,
    _norm_decision_title,
    _rescue_all_decision_window,
    _reserve_symbol_tail,
    _sort_demoting_noise,
    _symbol_tail_reserve,
)
from repowise.server.mcp_server.tool_search.retrieval import (
    _federated_search,
    _fused_entry,
    _fused_retrieve,
    _neg_score,
    _per_repo,
    _safe_fts,
    _safe_vector,
    _search_single_repo,
    _symbol_leg_target,
    _wait_for_vector_store,
)
from repowise.server.mcp_server.tool_search.serve import (
    _FRESHNESS_TIEBREAK,
    _apply_freshness_boost,
    _assign_confidence,
    _attach_paths,
    _drop_derivable_page_ids,
    _drop_internal_ranking_fields,
    _grep_hint_for,
    _load_page_info,
    _page_language,
    _refresh_served_targets,
    _refresh_served_targets_final,
    _result_paths,
    _serve_snippets,
    _slim_served_rows,
)
from repowise.server.mcp_server.tool_search.structured import (
    _attach_match_notes,
    _exact_match_note,
    _hybrid_mode_results,
    _lead_candidates,
    _match_named,
    _merge_pools,
    _missing_named_symbols,
    _Pools,
    _protect_exact,
    _search_one_repo,
    _SearchPlan,
    _select_results,
    _single_repository,
    _structured_search,
    _symbol_fetch,
    _symbol_mode_results,
    _symbol_query_for,
    _tag_repo,
)
from repowise.server.mcp_server.tool_search.tool import (
    _concept_search,
    _indexed_names,
    _issue_files,
    _query_from_pattern,
    _rank_concept_pages,
    _resolve_kind_and_mode,
    search_codebase,
)
from repowise.server.mcp_server.tool_search_symbols import (
    _MAX_CANDIDATES,
    IssueFiles,
    file_path_index,
    indexed_names,
    issue_files,
    search_paths_single,
    search_symbols_single,
)
