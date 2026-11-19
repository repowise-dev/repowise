"""One member's headline figures, read straight from its ``wiki.db``.

The workspace store keeps these per repo (see :mod:`.store`), so the
workspace listing and graph read one row per repo instead of opening every
member index per request. Raw ``sqlite3`` without a session: one connection,
a few aggregates.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from repowise.core.analysis.external_systems.links import IMPORT_TARGET_LANGUAGES
from repowise.core.docs_mode import resolve_docs_mode

_log = logging.getLogger("repowise.workspace.repo_stats")

# The raw-sqlite form of ``crud.indexed_file``.
_INDEXED_FILE_SQL = "node_type = 'file' AND language NOT IN ({})".format(
    ", ".join(f"'{lang}'" for lang in IMPORT_TARGET_LANGUAGES)
)


def query_top_language(db_path: Path) -> str:
    """Return the most common language across graph_nodes in a repo's wiki.db."""
    if not db_path.exists():
        return "unknown"
    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT language, COUNT(*) AS cnt FROM graph_nodes "
                f"WHERE {_INDEXED_FILE_SQL} AND language IS NOT NULL AND language != '' "
                "GROUP BY language ORDER BY cnt DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else "unknown"
    except Exception:
        return "unknown"


def query_repo_stats(db_path: Path) -> dict[str, Any]:
    """Query basic stats from a repo's wiki.db using raw sqlite3.

    Returns a dict with repo_id, file_count, symbol_count, page_count,
    fresh_page_count, doc_coverage_pct, hotspot_count, health_score, status,
    docs_enabled, docs_skip_reason, and (when the row is read) indexed_at.
    All values default to sensible neutrals on error.

    ``health_score`` is None when the repo carries no health metrics, and
    stays None: no other figure here measures code health.
    """
    result: dict[str, Any] = {
        "repo_id": None,
        "file_count": 0,
        "symbol_count": 0,
        "page_count": 0,
        "fresh_page_count": 0,
        "doc_coverage_pct": 0.0,
        "hotspot_count": 0,
        "health_score": None,
        "status": "needs_index",
        "docs_enabled": False,
        "docs_skip_reason": None,
    }
    _read_docs_state(db_path, result)
    if not db_path.exists():
        if not db_path.parent.parent.is_dir():
            result["status"] = "missing_dir"
        return result
    result["status"] = "indexed"
    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            _read_counts(conn.cursor(), result)
    except Exception:
        _log.debug("Failed to query stats from %s", db_path, exc_info=True)

    result["health_score"] = read_repo_health_score(db_path)
    return result


def _read_docs_state(db_path: Path, result: dict[str, Any]) -> None:
    """Surface docs lifecycle from state.json so the UI shows a coherent
    picture even when only indexing (no docs) ran."""
    state_path = db_path.parent / "state.json"
    if not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        result["docs_enabled"] = resolve_docs_mode(state) != "none"
        result["docs_skip_reason"] = state.get("docs_skip_reason")
    except Exception:
        _log.debug("Unreadable %s", state_path, exc_info=True)


def _read_counts(c: sqlite3.Cursor, result: dict[str, Any]) -> None:
    from .update import synced_at_iso

    # repo id, and the sync time every other surface reports
    row = c.execute("SELECT id, updated_at FROM repositories LIMIT 1").fetchone()
    if row:
        result["repo_id"] = row[0]
        result["indexed_at"] = synced_at_iso(row[1])

    # The raw-sqlite form of crud.count_files.
    row = c.execute(f"SELECT COUNT(*) FROM graph_nodes WHERE {_INDEXED_FILE_SQL}").fetchone()
    result["file_count"] = row[0] if row else 0

    # The raw-sqlite form of crud.count_symbols: one store per repo here.
    row = c.execute("SELECT COUNT(*) FROM wiki_symbols").fetchone()
    result["symbol_count"] = int(row[0]) if row else 0

    # Pages, fresh pages, and mean page confidence * 100. The last is named
    # doc_coverage_pct on the wire; it is not a share of files documented.
    pages, fresh, confidence = c.execute(
        "SELECT COUNT(*), SUM(freshness_status = 'fresh'), AVG(confidence) FROM wiki_pages"
    ).fetchone()
    result["page_count"] = pages
    result["fresh_page_count"] = int(fresh or 0)
    result["doc_coverage_pct"] = round(float(confidence or 0.0) * 100, 1)

    # hotspot count — use the canonical is_hotspot flag, matching the rest
    # of the codebase (module_health, extract-demo-data). The earlier
    # churn_percentile >= 90 predicate never matched: churn_percentile is
    # stored on a 0.0-1.0 scale and only scaled to 0-100 at the API layer.
    try:
        row = c.execute("SELECT COUNT(*) FROM git_metadata WHERE is_hotspot = 1").fetchone()
        result["hotspot_count"] = row[0] if row else 0
    except sqlite3.OperationalError:
        _log.debug("No hotspot flags in this index")  # table or column may not exist


def read_repo_health_score(db_path: Path) -> float | None:
    """Return the canonical repo health score from a repo-local wiki.db."""
    if not db_path.exists():
        return None
    # Aggregate in SQL rather than reading every row. A workspace calls this once
    # per repo, and the fetchall() this replaces read ~6,000 rows per repo to
    # produce one number: 61.6ms against 33.2ms across six large repos, and the
    # row count grows with the repo. MAX(CAST(..), 1) reproduces the previous
    # `max(int(nloc or 0), 1)` weight, truncation included; verified identical on
    # 109 real databases.
    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            row = conn.execute(
                "SELECT SUM(score * w), SUM(w) FROM ("
                "  SELECT score, MAX(CAST(COALESCE(nloc, 0) AS INTEGER), 1) AS w"
                "  FROM health_file_metrics WHERE score IS NOT NULL"
                ")"
            ).fetchone()
    except Exception:
        return None

    if not row or not row[1]:
        return None

    weighted_sum, total_weight = row
    average = float(weighted_sum) / float(total_weight)
    return max(0.0, min(100.0, round(average * 10.0, 1)))


def member_stats(db_path: Path) -> dict[str, Any]:
    """:func:`query_repo_stats` plus ``top_language``: what the store keeps per repo."""
    return {**query_repo_stats(db_path), "top_language": query_top_language(db_path)}


def db_stamp(db_path: Path) -> str:
    """What moves when anything commits to *db_path*: the main file, plus the
    WAL while it holds frames. An empty WAL (a reader opened the file) is
    ignored, so reads never move it."""
    parts = []
    for suffix in ("", "-wal"):
        try:
            st = Path(f"{db_path}{suffix}").stat()
        except OSError:
            parts.append("-")
            continue
        parts.append(f"{st.st_mtime_ns}:{st.st_size}" if suffix == "" or st.st_size else "-")
    return "|".join(parts)


def stamped_member_stats(db_path: Path) -> tuple[str, dict[str, Any]] | None:
    """``(db stamp, member stats)``, or None when a write landed mid-read twice
    running (the repo is then read live until its next refresh)."""
    for _ in range(2):
        before = db_stamp(db_path)
        stats = member_stats(db_path)
        if db_stamp(db_path) == before:
            return before, stats
    return None
