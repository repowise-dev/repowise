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

This ``__init__`` is a lazy re-export shim and imports nothing at module
scope. Public names resolve on first attribute access via PEP 562, so
``from repowise.core.ingestion.git_indexer import GitIndexer`` still works
while ``import repowise.core.ingestion.git_indexer.function_blame`` no longer
pays for the whole package (and GitPython) through the ``__init__``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...lazy_exports import lazy_exports

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from ._constants import (
        GIT_HISTORY_VERSION,
        GIT_HISTORY_VERSION_KEY,
        HOTSPOT_HALFLIFE_DAYS,
        git_history_stale,
        is_fix_commit,
    )
    from .agent_provenance import (
        AgentProvenance,
        AgentProvenanceClassifier,
        classifier_from_repo_config,
    )
    from .backfill import BACKFILL_PHASE, backfill_full_tier
    from .co_change import CoChangeWalk, compute_co_changes_and_entropy
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
        RenameTrail,
        _CommitRec,
        _extract_rename_paths,
        _parse_commit_record,
        _should_skip_index,
    )
    from .tiers import GitIndexTier

# Loaded on first use: the indexer stack pulls in GitPython and the blame /
# co-change machinery, and importing any ``git_indexer.*`` submodule runs this
# file, so every read path that only wants one helper (e.g. the health
# biomarkers importing ``function_blame``) paid for all of it.
__getattr__, __dir__ = lazy_exports(
    __name__,
    {
        **dict.fromkeys(
            (
                "GIT_HISTORY_VERSION",
                "GIT_HISTORY_VERSION_KEY",
                "HOTSPOT_HALFLIFE_DAYS",
                "git_history_stale",
                "is_fix_commit",
            ),
            "._constants",
        ),
        **dict.fromkeys(
            (
                "AgentProvenance",
                "AgentProvenanceClassifier",
                "classifier_from_repo_config",
            ),
            ".agent_provenance",
        ),
        **dict.fromkeys(("BACKFILL_PHASE", "backfill_full_tier"), ".backfill"),
        **dict.fromkeys(("CoChangeWalk", "compute_co_changes_and_entropy"), ".co_change"),
        **dict.fromkeys(
            (
                "compute_percentiles",
                "count_active_contributors",
                "detect_original_path",
                "get_blame_ownership",
                "is_significant_commit",
                "meets_hotspot_floors",
            ),
            ".enrich",
        ),
        "index_file": ".file_history",
        "build_fix_events": ".fix_events",
        **dict.fromkeys(("SHAPE_KINDS", "classify_fix_shape"), ".fix_shape"),
        **dict.fromkeys(("build_identity_resolver", "canonicalize_author_email"), ".identity"),
        "GitIndexer": ".indexer",
        **dict.fromkeys(
            (
                "FixCommit",
                "FixWalk",
                "PriorDefects",
                "collect_fix_commits",
                "compute_prior_defects",
            ),
            ".prior_defects",
        ),
        **dict.fromkeys(
            (
                "_FIELD_SEP",
                "_LOG_FORMAT",
                "_RECORD_SEP",
                "GitIndexSummary",
                "RenameTrail",
                "_CommitRec",
                "_extract_rename_paths",
                "_parse_commit_record",
                "_should_skip_index",
            ),
            ".records",
        ),
        "GitIndexTier": ".tiers",
    },
    globals(),
)

__all__ = [
    "BACKFILL_PHASE",
    "GIT_HISTORY_VERSION",
    "GIT_HISTORY_VERSION_KEY",
    "HOTSPOT_HALFLIFE_DAYS",
    "SHAPE_KINDS",
    # Re-exported for ``git_commit_index`` which builds the shared commit index.
    "_FIELD_SEP",
    "_LOG_FORMAT",
    "_RECORD_SEP",
    "AgentProvenance",
    "AgentProvenanceClassifier",
    "CoChangeWalk",
    "FixCommit",
    "FixWalk",
    "GitIndexSummary",
    "GitIndexTier",
    "GitIndexer",
    "PriorDefects",
    "RenameTrail",
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
    "compute_co_changes_and_entropy",
    "compute_percentiles",
    "compute_prior_defects",
    "count_active_contributors",
    "detect_original_path",
    "get_blame_ownership",
    "git_history_stale",
    "index_file",
    "is_fix_commit",
    "is_significant_commit",
    "meets_hotspot_floors",
]
