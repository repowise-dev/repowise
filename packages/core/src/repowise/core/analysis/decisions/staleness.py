"""How far the code a decision governs has moved since the decision was recorded."""

from __future__ import annotations

from datetime import UTC, datetime


def _as_aware_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    SQLite drops timezone information from ``DateTime(timezone=True)`` columns,
    but Repowise writes those values as UTC. Treat naive values as UTC so they
    can be compared with git metadata timestamps, which are already aware UTC.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce_dt(value: datetime | str) -> datetime:
    """Parse an ISO string into a datetime, passing datetimes through.

    Both shapes reach staleness: the ORM hands back datetimes, while the git
    metadata map carries whatever was persisted, which for SQLite is a string.
    """
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def compute_staleness(
    decision_created_at: datetime,
    affected_files: list[str],
    git_meta_map: dict[str, dict],
    decision_text: str = "",
) -> float:
    """Fraction of *affected_files* that have changed since the record's birth.

    A fact about the code, not a judgement about the record: 0.0 means
    nothing it governs has moved, 1.0 means all of it has. No tuned
    constants and no reading of commit messages. *decision_text* is
    accepted and unused so existing call sites keep working.

    A file with no git metadata **after** the caller's gap fill counts as
    changed: the record names something the repository does not track, so
    it cannot be shown to still hold.
    """
    if not affected_files:
        # No scope, so the question cannot be asked. Callers distinguish
        # this from a genuine 0.0 by the empty file list, and
        # `decision health` reports it as unscoped rather than fresh.
        return 0.0

    created = _as_aware_utc(_coerce_dt(decision_created_at)) if decision_created_at else None
    changed = 0
    for fp in affected_files:
        meta = git_meta_map.get(fp)
        if meta is None:
            changed += 1  # named but not tracked, so cannot be shown to hold
            continue
        last_commit = meta.get("last_commit_at")
        if not last_commit or created is None:
            continue
        if _as_aware_utc(_coerce_dt(last_commit)) > created:
            changed += 1

    return round(changed / len(affected_files), 3)


def last_code_change(
    affected_files: list[str],
    git_meta_map: dict[str, dict],
) -> datetime | None:
    """When the code a decision governs last moved, or None.

    Reduces the same inputs :meth:`compute_staleness` reads: that counts
    how many files moved since the record was born, this reports when the
    most recent of them moved. None where the question cannot be answered,
    meaning no scope or no git metadata for anything the record names.
    """
    dates: list[datetime] = []
    for file_path in affected_files:
        meta = git_meta_map.get(file_path)
        last_commit = meta.get("last_commit_at") if meta else None
        if last_commit:
            dates.append(_as_aware_utc(_coerce_dt(last_commit)))
    return max(dates) if dates else None
