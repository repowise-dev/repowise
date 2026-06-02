"""Reconstruct phase *inputs* from persisted DB rows for a resumed run.

The graph is rehydrated by the existing :func:`rehydrate_graph_builder`
(re-exported here for a single resume import surface). This module adds the
git side: turning persisted ``git_metadata`` rows back into the
``git_meta_map`` dict that the analysis phases consume.

Caveat — the transient per-line ``BlameIndex`` is intentionally never
persisted (see ``phases/git.py: drop_transient_git_signals``), so a
rehydrated ``git_meta_map`` carries every durable signal but no
``blame_index``. Blame-dependent biomarkers treat its absence as "no
signal" — which is exactly the case for FAST/ESSENTIAL-tier runs that
never computed blame in the first place.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import structlog

from ..upgrade import rehydrate_graph_builder

logger = structlog.get_logger(__name__)

__all__ = [
    "rehydrate_dead_code_report",
    "rehydrate_decision_report",
    "rehydrate_git_meta_map",
    "rehydrate_graph_builder",
]


def _git_metadata_to_dict(row: Any) -> dict[str, Any]:
    """Project a ``GitMetadata`` ORM row to the dict shape analysis expects.

    Uses column introspection so every persisted signal is carried faithfully
    and the keys match the column names the fresh git indexer writes (the
    upsert path sets attributes by matching dict keys to columns). JSON
    columns stay as their stored strings — consumers (e.g. the duplication
    co-change scorer) ``json.loads`` them on demand.
    """
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        name = col.name
        if name in ("id", "repository_id"):
            continue
        out[name] = getattr(row, name)
    return out


async def rehydrate_git_meta_map(session: Any, repo_id: str) -> dict[str, dict[str, Any]]:
    """Rebuild ``{file_path -> git metadata dict}`` from persisted rows.

    Returns an empty dict when nothing was persisted (so a caller can detect
    "git was never indexed" and fall back to recomputing).
    """
    from repowise.core.persistence import get_all_git_metadata

    try:
        rows = await get_all_git_metadata(session, repo_id)
    except Exception as exc:
        logger.debug("rehydrate_git_meta_failed", error=str(exc))
        return {}
    return {path: _git_metadata_to_dict(row) for path, row in rows.items()}


# ---------------------------------------------------------------------------
# Analysis reports (generation INPUT only — never re-persisted)
# ---------------------------------------------------------------------------
#
# These thin views carry exactly the fields the page generator reads off a
# dead-code / decision report (see ``page_generator/helpers.py``:
# ``build_dead_code_map`` / ``build_decision_maps``) — nothing more. They are
# deliberately *not* the analysis dataclasses: a resumed run feeds them into
# generation but must never write them back (the persisted rows are already
# authoritative), so they skip ``deletable_lines`` / ``by_source`` and the
# other persist/summary-only fields a faithful round-trip would need.


async def rehydrate_dead_code_report(session: Any, repo_id: str) -> Any:
    """Rebuild a generation-shaped dead-code report from persisted findings.

    Returns a namespace with a ``findings`` list (empty when the completed
    analysis legitimately found none). Raises on a DB error so the caller can
    fall back to recomputing.
    """
    from repowise.core.persistence.crud import get_dead_code_findings

    rows = await get_dead_code_findings(session, repo_id, status="open")
    findings = [
        SimpleNamespace(
            file_path=r.file_path,
            symbol_name=r.symbol_name,
            symbol_kind=r.symbol_kind,
            kind=r.kind,  # stored as the clean string label, e.g. "unused_export"
            reason=r.reason,
            confidence=r.confidence,
            safe_to_delete=r.safe_to_delete,
        )
        for r in rows
    ]
    return SimpleNamespace(findings=findings)


async def rehydrate_decision_report(session: Any, repo_id: str) -> Any:
    """Rebuild a generation-shaped decision report from persisted records.

    Returns a namespace with a ``decisions`` list (empty when none were
    recorded). Raises on a DB error so the caller can fall back to recomputing.
    """
    from repowise.core.persistence.crud import list_decisions

    rows = await list_decisions(session, repo_id, limit=100_000)
    decisions = []
    for r in rows:
        try:
            affected = json.loads(r.affected_files_json or "[]")
        except Exception:
            affected = []
        decisions.append(
            SimpleNamespace(
                title=r.title,
                decision=r.decision,
                rationale=r.rationale,
                source=r.source,
                confidence=r.confidence,
                evidence_file=r.evidence_file,
                affected_files=affected,
            )
        )
    return SimpleNamespace(decisions=decisions)
