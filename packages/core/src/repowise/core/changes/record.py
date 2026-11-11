"""The Change Record as versioned JSON, and its in-toto Statement export.

The native record is primary: every surface (CLI, REST, web) serves this one
shape, and ``schema_version`` moves when a field changes meaning. The in-toto
export wraps the same record as the predicate of a Statement v1 whose
subjects are the change's commits, keyed by the ``gitCommit`` digest. It
claims no SLSA level.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from repowise.core.attribution import AGENT, HUMAN_ATTESTED, UNKNOWN

SCHEMA_VERSION = 1
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://repowise.dev/ChangeRecord/v1"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:  # SQLite drops tzinfo; the column is UTC
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _loads(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def provenance_block(
    label: str | None, agent: int | None, known: int | None, commits: int
) -> dict[str, Any] | None:
    """Three-state provenance with the counts and coverage behind it."""
    if label not in (AGENT, HUMAN_ATTESTED, UNKNOWN) or known is None or agent is None:
        return None
    return {
        "label": label,
        "agent_commits": agent,
        "human_attested_commits": known - agent,
        "unknown_commits": max(commits - known, 0),
        "coverage": round(known / commits, 4) if commits else None,
    }


def change_record(row: Any, *, attribution: bool = True) -> dict[str, Any]:
    """The native record for one stored ``changes`` row.

    *attribution* false (``attribution.enabled: false``) leaves provenance
    out even if an older index stored it.
    """
    commits = _loads(row.commit_shas_json) or []
    return {
        "schema_version": SCHEMA_VERSION,
        "id": row.change_id,
        "identity": {"kind": row.identity_kind, "value": row.identity_value},
        "shape": row.shape,
        "forge": row.forge,
        "forge_ref": row.forge_ref,
        "title": row.title,
        "merged_at": _iso(row.merged_at),
        "head_sha": row.head_sha,
        "author": {"name": row.author_name, "email": row.author_email},
        "commits": commits,
        "files": _loads(row.files_json) or [],
        "files_changed": row.files_changed,
        "lines_added": row.lines_added,
        "lines_deleted": row.lines_deleted,
        "category": row.category,
        "provenance": (
            provenance_block(
                row.provenance, row.provenance_agent, row.provenance_known, len(commits)
            )
            if attribution
            else None
        ),
        "metadata": {
            "source": row.metadata_source,
            "reviewers": _loads(row.reviewers_json),
            "approvals": row.approvals,
            "labels": _loads(row.labels_json),
            "linked_issues": _loads(row.linked_issues_json),
        },
    }


def in_toto_statement(record: dict[str, Any]) -> dict[str, Any]:
    """*record* as an in-toto Statement v1: one subject per commit, head first."""
    shas = list(dict.fromkeys([record["head_sha"], *record.get("commits", [])]))
    return {
        "_type": IN_TOTO_STATEMENT_TYPE,
        "subject": [{"name": sha, "digest": {"gitCommit": sha}} for sha in shas],
        "predicateType": PREDICATE_TYPE,
        "predicate": record,
    }


__all__ = [
    "IN_TOTO_STATEMENT_TYPE",
    "PREDICATE_TYPE",
    "SCHEMA_VERSION",
    "change_record",
    "in_toto_statement",
    "provenance_block",
]
