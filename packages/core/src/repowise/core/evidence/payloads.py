"""The runtime evidence route payloads, built from rows a host already read.

The server's ``/api/repos/{id}/evidence/*`` handlers read their rows from the
store and return these; a host that keeps evidence elsewhere builds the same
payloads (the ``server.schemas.runtime_evidence`` models) from its own rows.
Nothing here reads a store.

- :func:`status_payload`: the ``status`` route (newest run per workload,
  freshness, whether anything is ranked and the command that moves it on);
- :func:`runs_payload`: the ``runs`` route (every stored run, newest first);
- :func:`measured_payload`: the ``measured`` route from a ``measured_costs``
  view (or ``None``) and the repository's ``measured_status`` token;
- :func:`functions_payload` / :func:`function_payload`: the ``functions``
  route from ``symbol_hotness`` rows;
- :func:`owner_history_payload`: a measured function's owner and last change
  from its ``git_function_blame`` row and the newest commit's row.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import timedelta
from typing import Any

from repowise.core.ingestion.symbol_identity import file_of_symbol

from .status import evidence_status, run_status


def _command(status: str) -> str | None:
    # Deferred: the persistence package imports this one's siblings.
    from repowise.core.persistence.crud.analysis.evidence import unranked_command

    return unranked_command(status)


def status_payload(runs: Iterable[Any], status: str, head_commit: str | None) -> dict[str, Any]:
    """The ``status`` route: *runs* are every stored run (``list_evidence_runs``),
    *status* the ``measured_status`` token, *head_commit* the indexed commit."""
    return {
        "status": status,
        "command": _command(status),
        **evidence_status(runs, head_commit),
    }


def runs_payload(runs: Iterable[Any], head_commit: str | None) -> dict[str, Any]:
    """The ``runs`` route: each stored run's status against *head_commit*, in
    the order given (newest first from ``list_evidence_runs``)."""
    return {
        "reference_commit": head_commit,
        "runs": [run_status(run, head_commit) for run in runs],
    }


def measured_payload(view: Mapping[str, Any] | None, status: str) -> dict[str, Any]:
    """The ``measured`` route from a ``measured_costs`` *view*.

    With no view, *status* (the repository's ``measured_status``) says why;
    ``measured`` then reads ``not_ranked``: something is ranked, just not in
    the asked run (an older run of its workload, or one that ranks nothing).
    """
    if view is None:
        if status == "measured":
            status = "not_ranked"
        return {
            "basis": "unknown",
            "status": status,
            "command": _command(status),
            "measured": None,
            "measured_in_tests": None,
            "benchmarks": None,
        }
    return {
        "basis": "measured",
        "status": "measured",
        "command": None,
        "measured": view.get("measured"),
        "measured_in_tests": view.get("measured_in_tests"),
        "benchmarks": view.get("benchmarks"),
    }


def owner_history_payload(blame: Any, commit: Any | None) -> dict[str, Any]:
    """A function's owner and last change from its ``git_function_blame`` row.

    The owner wrote most of the function's current lines (``line_pct`` of
    them). The last change is the newest commit blame credits a current line
    to, with *commit* its ``git_commits`` row (``None`` when the commit is
    older than the indexed window: the sha alone, then). The change number
    comes from the commit subject. Built only from a function's own row: no
    row, no owner (never the file's).
    """
    from repowise.core.forges import change_number

    try:
        shas = json.loads(blame.commit_shas_json or "[]")
    except (TypeError, ValueError):
        shas = []
    last = None
    if isinstance(shas, list) and shas:
        subject = (commit.subject or None) if commit is not None else None
        at = commit.committed_at if commit is not None else None
        if at is not None:
            # The committer's own calendar day: ``committed_at`` is a UTC instant.
            at += timedelta(minutes=getattr(commit, "committed_offset_minutes", None) or 0)
        last = {
            "sha": str(shas[0]),
            "date": at.date().isoformat() if at is not None else None,
            "subject": subject,
            "pr": change_number(subject) if subject else None,
        }
    return {
        "owner": (
            {"name": blame.owner_name, "line_pct": blame.owner_line_pct}
            if blame.owner_name
            else None
        ),
        "last_change": last,
    }


def functions_payload(rows: Mapping[str, Any]) -> dict[str, Any]:
    """The ``functions`` route from ``{symbol_id: symbol_hotness row}``, by id."""
    return {"functions": [function_payload(row) for _, row in sorted(rows.items())]}


def function_payload(row: Any) -> dict[str, Any]:
    """One ``symbol_hotness`` row as the route lists it."""
    return {
        "symbol_id": row.symbol_id,
        "file_path": file_of_symbol(row.symbol_id),
        # Tiers above ranked are not calibrated, so no surface claims them.
        "tier": "ranked" if row.tier in ("ranked", "lead_candidate") else "measured",
        "rank": row.rank,
        "self_share": row.self_share,
        "total_share": row.total_share,
        "share_low": row.share_low,
        "share_high": row.share_high,
        "samples": row.samples,
        "run_samples": row.run_samples,
        "run_id": row.run_id,
        "run_count": row.run_count,
        "workload_name": row.workload_name,
        "workload_class": row.workload_class,
        "value_kind": row.value_kind,
        "ordering_only": row.ordering_only,
        "freshness": row.freshness,
        "evidence_commit_sha": row.evidence_commit_sha,
        "measured_at": row.measured_at,
        "repo_self_share": row.repo_self_share,
        "wait_share": row.wait_share,
        "library_share": row.library_share,
        "run_unlisted": row.run_unlisted,
        "inside": _json_list(getattr(row, "inside_json", None)),
        "drivers": _json_list(getattr(row, "drivers_json", None)),
    }


def _json_list(text: str | None) -> list[Any] | None:
    """A stored JSON list column; None when empty or unreadable."""
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, list) and value else None


__all__ = [
    "function_payload",
    "functions_payload",
    "measured_payload",
    "owner_history_payload",
    "runs_payload",
    "status_payload",
]
