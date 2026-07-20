"""Git history indexer for the repowise ingestion pipeline.

Mines git history into the git_metadata table. Uses gitpython for git
operations and parallelises per-file git log calls.

Non-blocking: if git is unavailable or the repo has no history, a warning is
logged and an empty summary returned. All downstream features degrade
gracefully.

The indexer is tiered (:class:`GitIndexTier`): ``FULL`` (default) preserves the
historical behaviour exactly, while ``ESSENTIAL`` skips the two expensive
signals — per-file ``git blame`` and the repo-wide co-change walk — for fast
large-repo indexing. :func:`~.backfill.backfill_full_tier` promotes an
ESSENTIAL index to FULL afterwards.

This package was split out of a single 1k-line module; the public import path
(``repowise.core.ingestion.git_indexer``) is unchanged.
"""

from __future__ import annotations

from ._constants import HOTSPOT_HALFLIFE_DAYS, is_fix_commit
from .agent_provenance import (
    AgentProvenance,
    AgentProvenanceClassifier,
    classifier_from_repo_config,
)
from .backfill import BACKFILL_PHASE, backfill_full_tier
from .co_change import compute_co_changes, compute_co_changes_and_entropy
from .enrich import (
    compute_percentiles,
    count_active_contributors,
    detect_original_path,
    get_blame_ownership,
    is_significant_commit,
    meets_hotspot_floors,
)
from .file_history import index_file
from .fix_events import build_fix_events
from .fix_shape import SHAPE_KINDS, classify_fix_shape
from .identity import build_identity_resolver, canonicalize_author_email
from .indexer import GitIndexer
from .prior_defects import (
    FixCommit,
    FixWalk,
    PriorDefects,
    collect_fix_commits,
    compute_prior_defects,
)
from .records import (
    _FIELD_SEP,
    _LOG_FORMAT,
    _RECORD_SEP,
    GitIndexSummary,
    _CommitRec,
    _extract_rename_paths,
    _parse_commit_record,
    _should_skip_index,
)
from .tiers import GitIndexTier

__all__ = [
    "BACKFILL_PHASE",
    "HOTSPOT_HALFLIFE_DAYS",
    "SHAPE_KINDS",
    # Re-exported for ``git_commit_index`` which builds the shared commit index.
    "_FIELD_SEP",
    "_LOG_FORMAT",
    "_RECORD_SEP",
    "AgentProvenance",
    "AgentProvenanceClassifier",
    "FixCommit",
    "FixWalk",
    "GitIndexSummary",
    "GitIndexTier",
    "GitIndexer",
    "PriorDefects",
    "_CommitRec",
    "_extract_rename_paths",
    "_parse_commit_record",
    "_should_skip_index",
    "backfill_full_tier",
    "build_fix_events",
    "build_identity_resolver",
    "canonicalize_author_email",
    "classifier_from_repo_config",
    "classify_fix_shape",
    "collect_fix_commits",
    "compute_co_changes",
    "compute_co_changes_and_entropy",
    "compute_percentiles",
    "compute_prior_defects",
    "count_active_contributors",
    "detect_original_path",
    "get_blame_ownership",
    "index_file",
    "is_fix_commit",
    "is_significant_commit",
    "meets_hotspot_floors",
]
