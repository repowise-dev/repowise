"""What under ``.repowise/`` an update reuses from the previous run.

For tools that keep an index between runs somewhere else (a cache, an archive,
a CI artifact) and restore it before ``repowise update``: carry these and the
update starts warm. Everything is optional; a missing file is a cache miss or a
rebuild, never an error. Names are relative to ``.repowise/``. Committed inputs
(``config.yaml``, ``decisions.yaml``, ``health-rules.json``) come with the checkout
and are not listed.
"""

from __future__ import annotations

from repowise.core.analysis.dead_code.token_cache import _CACHE_FILENAME as _DEAD_CODE_TOKENS
from repowise.core.analysis.decisions.rationale_harvest import _FILENAME as _RATIONALE_HARVEST
from repowise.core.analysis.doc_drift.extract_cache import _CACHE_FILENAME as _DOC_DRIFT_EXTRACT
from repowise.core.analysis.health.duplication.pair_index import (
    _INDEX_FILENAME as _DUPLICATION_PAIRS,
)
from repowise.core.analysis.health.duplication.token_cache import (
    _CACHE_FILENAME as _DUPLICATION_TOKENS,
)
from repowise.core.analysis.health.walk_cache import _CACHE_FILENAME as _HEALTH_WALK
from repowise.core.analysis.knowledge_graph import KNOWLEDGE_GRAPH_FILENAME
from repowise.core.ingestion.dynamic_hints.scan_cache import _CACHE_NAME as _DYNAMIC_HINT_SCANS
from repowise.core.ingestion.framework_edges.scan_cache import _CACHE_FILENAME as _FRAMEWORK_SCAN
from repowise.core.ingestion.git_commit_index import _DEEP_CACHE_GLOB, _WINDOW_CACHE_NAME
from repowise.core.ingestion.git_indexer.fix_shape_cache import _CACHE_NAME as _FIX_SHAPE
from repowise.core.ingestion.git_indexer.fix_walk_cache import _CACHE_NAME as _FIX_WALK
from repowise.core.ingestion.graph._centrality_cache import _CACHE_FILENAME as _CENTRALITY
from repowise.core.ingestion.parse_cache import _CACHE_FILENAME as _PARSE
from repowise.core.ingestion.source_reader import DOCUMENT_CACHE_DIRNAME
from repowise.core.persistence.database import DB_FILENAME, LANCEDB_DIRNAME, STATE_FILENAME
from repowise.core.pipeline.cleanup_debt import CLEANUP_DEBT_FILENAME
from repowise.core.pipeline.incremental.graph_seed import _CACHE_NAME as _GRAPH_SEED
from repowise.core.pipeline.incremental.resolution_cache import _FILENAME as _RESOLVED_IMPORTS
from repowise.core.precedent.store import EPISODES_DIRNAME

__all__ = ["CARRIED_DIRS", "CARRIED_FILES", "CARRIED_GLOBS", "SEALED_FILES"]

#: Pickles written through :mod:`repowise.core.cache_seal`. They load only under
#: the HMAC key that sealed them (``REPOWISE_CACHE_KEY_PATH``, else the user
#: config dir), so a carrier restoring them on another machine carries the key too.
#: The entries of ``document-cache/`` (:data:`CARRIED_DIRS`) are sealed the same way.
SEALED_FILES: tuple[str, ...] = (
    _PARSE,
    _CENTRALITY,
    _GRAPH_SEED,
    _RESOLVED_IMPORTS,
    _FRAMEWORK_SCAN,
    _DUPLICATION_TOKENS,
    _DUPLICATION_PAIRS,
    _HEALTH_WALK,
    _DEAD_CODE_TOKENS,
    _DOC_DRIFT_EXTRACT,
)

#: Single files directly under ``.repowise/``. ``wiki.db`` is a live SQLite
#: database in WAL mode: snapshot it with SQLite's backup API (one file, no
#: ``-wal``/``-shm``), since a plain copy can miss commits still in the WAL.
CARRIED_FILES: tuple[str, ...] = (
    DB_FILENAME,
    STATE_FILENAME,
    KNOWLEDGE_GRAPH_FILENAME,
    CLEANUP_DEBT_FILENAME,
    _RATIONALE_HARVEST,
    _WINDOW_CACHE_NAME,
    _FIX_WALK,
    _FIX_SHAPE,
    _DYNAMIC_HINT_SCANS,
    *SEALED_FILES,
)

#: Glob patterns for file families directly under ``.repowise/``.
CARRIED_GLOBS: tuple[str, ...] = (_DEEP_CACHE_GLOB,)

#: Directories carried whole. ``episodes/`` holds a SQLite database (same copy
#: rule as ``wiki.db``); ``document-cache/`` entries are sealed pickles whose seal
#: binds the absolute checkout path, so they hit only when that path is unchanged.
CARRIED_DIRS: tuple[str, ...] = (EPISODES_DIRNAME, DOCUMENT_CACHE_DIRNAME, LANCEDB_DIRNAME)
