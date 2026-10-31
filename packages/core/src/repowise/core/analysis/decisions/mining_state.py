"""Per-source progress of the commit-history decision miners.

``repowise update`` advances one sync pointer for the whole run, so a source
that failed over ``base_ref..head`` has nothing left to retry from once the
pointer moves. This module keeps a small per-source ledger in ``state.json``:
the last commit a source mined successfully, and the outcome of its last run.
A source's next range starts at its own pointer rather than at the run's.

The ledger is deliberately best-effort. A missing or malformed entry reads as
"never run" and falls back to the run's base, which is the pre-existing
behaviour, so an older state file keeps working.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: ``state.json`` key holding ``{source: ledger entry}``.
DECISION_MINING_STATE_KEY = "decision_mining"


def _entries(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (state or {}).get(DECISION_MINING_STATE_KEY)
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(value, dict)}


def _entry(state: dict[str, Any], source: str) -> dict[str, Any]:
    """The mutable ledger entry for *source*, created in *state* when absent."""
    raw = state.get(DECISION_MINING_STATE_KEY)
    if not isinstance(raw, dict):
        raw = {}
        state[DECISION_MINING_STATE_KEY] = raw
    entry = raw.get(source)
    if not isinstance(entry, dict):
        entry = {}
        raw[source] = entry
    return entry


def source_base(state: dict[str, Any] | None, source: str, fallback: str | None) -> str | None:
    """The commit *source* should mine from, or *fallback* when it never succeeded."""
    commit = _entries(state).get(source, {}).get("last_success_commit")
    return commit if isinstance(commit, str) and commit else fallback


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def record_success(state: dict[str, Any] | None, source: str, commit: str | None) -> None:
    """Record that *source* mined through *commit* without failing."""
    if not isinstance(state, dict) or not commit:
        return
    at = _stamp()
    _entry(state, source).update(
        last_success_commit=commit,
        last_run_at=at,
        last_run_commit=commit,
        last_status="ok",
    )


def record_failure(
    state: dict[str, Any] | None,
    source: str,
    commit: str | None,
    error: str,
) -> None:
    """Record a failed *source*, keeping its last successful commit untouched."""
    if not isinstance(state, dict):
        return
    at = _stamp()
    _entry(state, source).update(
        last_run_at=at,
        last_run_commit=commit,
        last_status="failed",
        last_failure=str(error),
        last_failure_at=at,
        last_failure_commit=commit,
    )


def status_fields(entry: dict[str, Any] | None) -> dict[str, Any]:
    """The per-source fields ``decision status`` reports, all present when absent.

    Kept here rather than in the status report so the ledger's key names have
    one owner: the writer and the reader cannot drift.
    """
    entry = entry if isinstance(entry, dict) else {}
    return {
        "last_run": entry.get("last_run_at"),
        "last_run_commit": entry.get("last_run_commit"),
        "last_run_status": entry.get("last_status"),
        "last_failure": entry.get("last_failure"),
        "last_failure_at": entry.get("last_failure_at"),
        "last_failure_commit": entry.get("last_failure_commit"),
        "last_success_commit": entry.get("last_success_commit"),
    }


def status_entries(state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Ledger entries keyed by source, for the status report."""
    return _entries(state)
