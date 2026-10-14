"""Record a run's snapshot, diff it into events, keep history bounded, read events.

:func:`record_snapshot` runs once per index run, after every store is final.
It snapshots the components, and when the newest stored run is the index run
just before this one and carries the same stamp, it stores the typed
occurrences between the two and hands them to the configured sinks. A stamp
change (a new analyzer, parser, git-history or contract-extraction version)
starts a fresh baseline instead: figures computed under different rules are
not comparable, so their difference is not an event.

An index run that takes no snapshot (events switched off, or the snapshot
failed) leaves a gap marker, so the next snapshot is a baseline rather than a
diff across runs nobody saw.

Settings, under ``events:`` in ``.repowise/config.yaml``::

    events:
      enabled: true            # snapshots and events at all
      retention_days: 365      # runs and events older than this are dropped
      compact_after_days: 30   # older runs keep one per ISO week
      kinds: {new_hotspot: true}
      sinks: [jsonl, stdout]   # names in the event-sink seam
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from .diff import diff_snapshots
from .model import EVENT_SCHEMA_VERSION, KINDS, EngineEvent, event_id, severity_for

logger = logging.getLogger(__name__)

#: Stamp prefix of a gap marker: an index run with no snapshot.
GAP_STAMP = "gap:"


@dataclass(frozen=True)
class EventSettings:
    enabled: bool = True
    retention_days: int = 365
    compact_after_days: int = 30
    kinds: frozenset[str] = frozenset(k.name for k in KINDS.values() if k.default_on)
    sinks: tuple[str, ...] = ()
    #: What was wrong in the block; each wrong value kept its default.
    errors: tuple[str, ...] = ()


_KEYS = frozenset({"enabled", "retention_days", "compact_after_days", "kinds", "sinks"})


def load_event_settings(repo_root: Path | str) -> EventSettings:
    """The repo's ``events:`` settings; a missing or invalid value keeps its
    default and is named in ``errors``."""
    from repowise.core.repo_config import load_repo_config

    try:
        raw = load_repo_config(repo_root).get("events")
    except Exception:
        raw = None
    if raw is None:
        return EventSettings()
    if not isinstance(raw, dict):
        return EventSettings(errors=("events: expected a mapping",))
    base = EventSettings()
    errors = [f"events.{k}: unknown key" for k in sorted(set(raw) - _KEYS, key=str)]
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append("events.enabled: expected true or false")
        enabled = True
    kinds = set(base.kinds)
    raw_kinds = raw.get("kinds") or {}
    if not isinstance(raw_kinds, dict):
        errors.append("events.kinds: expected a mapping of kind to true or false")
        raw_kinds = {}
    for name, on in raw_kinds.items():
        if name not in KINDS:
            errors.append(f"events.kinds.{name}: unknown kind; known: {', '.join(sorted(KINDS))}")
        elif not isinstance(on, bool):
            errors.append(f"events.kinds.{name}: expected true or false")
        else:
            (kinds.add if on else kinds.discard)(name)
    sinks = raw.get("sinks")
    if sinks is not None and not isinstance(sinks, list):
        errors.append("events.sinks: expected a list of sink names")
    return EventSettings(
        enabled=enabled,
        retention_days=_positive(raw, "retention_days", base.retention_days, errors),
        compact_after_days=_positive(raw, "compact_after_days", base.compact_after_days, errors),
        kinds=frozenset(kinds),
        sinks=tuple(str(s) for s in sinks) if isinstance(sinks, list) else (),
        errors=tuple(errors),
    )


def _positive(raw: dict, key: str, default: int, errors: list[str]) -> int:
    value = raw.get(key)
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    errors.append(f"events.{key}: expected a positive whole number of days")
    return default


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _public_url(url: str | None) -> str:
    """*url* without credentials, so a token in a remote URL never leaves."""
    from urllib.parse import urlsplit, urlunsplit

    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if "@" in parts.netloc:
        parts = parts._replace(netloc=parts.netloc.rsplit("@", 1)[1])
    return urlunsplit(parts)


async def record_snapshot(
    session: Any, repository_id: str, *, now: datetime | None = None
) -> list[EngineEvent]:
    """Snapshot the repository's components and return the events stored for it.

    A run at the commit and stamp of the newest run is skipped (an update that
    found nothing to do, or ``--working-tree`` on the same commit: uncommitted
    work is not history).

    Ceiling: sinks are called before the caller commits, because this runs
    inside the index run's session. An event id is keyed on the previous run,
    so a retry after a failed commit re-delivers the same id and a consumer
    dedupes on it. Upgrade path: a per-sink cursor read after commit.
    """
    from repowise.core.attribution import attribution_enabled
    from repowise.core.persistence.models import ComponentSnapshot, Repository, SnapshotRun

    from .snapshot import collect_facts, current_stamp

    repo = await session.get(Repository, repository_id)
    if repo is None or not repo.local_path:
        return []
    root = Path(repo.local_path)
    settings = load_event_settings(root)
    for error in settings.errors:
        logger.warning("%s (kept the default)", error)
    if not settings.enabled:
        await record_gap(session, repository_id, "disabled", now=now)
        return []
    stamp = current_stamp()
    head = repo.head_commit
    previous = await _newest_run(session, repository_id)
    if previous is not None and head and previous.commit_sha == head and previous.stamp == stamp:
        return []

    facts = await collect_facts(
        session, repository_id, root, head_commit=head, attribution=attribution_enabled(root)
    )
    taken_at = now or datetime.now(UTC)
    run = SnapshotRun(repository_id=repository_id, taken_at=taken_at, commit_sha=head, stamp=stamp)
    session.add(run)
    await session.flush()
    session.add_all(
        ComponentSnapshot(run_id=run.id, component=name, facts_json=json.dumps(f, sort_keys=True))
        for name, f in facts.items()
    )

    events: list[EngineEvent] = []
    # A gap marker's stamp never equals a real one, so a run after it is a baseline.
    if previous is not None and previous.stamp == stamp:
        before = await _run_facts(session, previous.id)
        events = await _store_events(
            session, repo, run, previous, before, facts, settings, taken_at
        )
    await compact(session, repository_id, settings, now=taken_at)
    await session.flush()
    if events:
        await _deliver(events, settings, root)
    return events


async def _newest_run(session: Any, repository_id: str) -> Any:
    from repowise.core.persistence.models import SnapshotRun

    return await session.scalar(
        select(SnapshotRun)
        .where(SnapshotRun.repository_id == repository_id)
        .order_by(SnapshotRun.taken_at.desc(), SnapshotRun.id.desc())
        .limit(1)
    )


async def record_gap(
    session: Any, repository_id: str, reason: str, *, now: datetime | None = None
) -> None:
    """Mark an index run that took no snapshot, so the next one is a baseline."""
    from repowise.core.persistence.models import SnapshotRun

    newest = await _newest_run(session, repository_id)
    if newest is None or newest.stamp.startswith(GAP_STAMP):
        return  # nothing to diff against yet, or the gap is already marked
    session.add(
        SnapshotRun(
            repository_id=repository_id,
            taken_at=now or datetime.now(UTC),
            commit_sha=None,
            stamp=f"{GAP_STAMP}{reason}"[:64],
        )
    )
    await session.flush()


async def _run_facts(session: Any, run_id: str) -> dict[str, dict[str, Any]]:
    from repowise.core.persistence.models import ComponentSnapshot

    rows = await session.execute(
        select(ComponentSnapshot.component, ComponentSnapshot.facts_json).where(
            ComponentSnapshot.run_id == run_id
        )
    )
    return {name: json.loads(raw) for name, raw in rows}


async def _store_events(
    session: Any,
    repo: Any,
    run: Any,
    previous: Any,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    settings: EventSettings,
    taken_at: datetime,
) -> list[EngineEvent]:
    from repowise.core.persistence.models import Change, EngineEventRecord

    occurrences = [o for o in diff_snapshots(before, after) if o[0] in settings.kinds]
    if not occurrences:
        return []
    url = _public_url(repo.url)
    vcs: dict[str, Any] = {}
    if url:
        vcs["vcs.repository.url.full"] = url
    if run.commit_sha:
        change_id = await session.scalar(
            select(Change.change_id).where(
                Change.repository_id == repo.id, Change.head_sha == run.commit_sha
            )
        )
        if change_id:
            vcs["vcs.change.id"] = change_id
    events = [
        EngineEvent(
            # Keyed on the previous run, not only the commits: a pair seen
            # again (A to B, back to A, to B) or a run with no commit is a new
            # occurrence, while a retry of the same run keeps its id.
            id=event_id(
                repo.id, previous.id, previous.commit_sha, run.commit_sha, kind, component, subject
            ),
            kind=kind,
            repo_id=repo.id,
            component=component,
            severity=severity_for(after[component].get("criticality")),
            occurred_at=taken_at,
            evidence=evidence,
            stamp=run.stamp,
            commit_sha=run.commit_sha,
            base_commit_sha=previous.commit_sha,
            source=url,
            vcs=vcs,
        )
        for kind, component, subject, evidence in occurrences
    ]
    seen = set(
        await session.scalars(
            select(EngineEventRecord.id).where(EngineEventRecord.id.in_([e.id for e in events]))
        )
    )
    events = [e for e in events if e.id not in seen]
    session.add_all(
        EngineEventRecord(
            id=e.id,
            repository_id=e.repo_id,
            kind=e.kind,
            component=e.component,
            severity=e.severity,
            occurred_at=e.occurred_at,
            commit_sha=e.commit_sha,
            base_commit_sha=e.base_commit_sha,
            stamp=e.stamp,
            payload_json=json.dumps(
                {"evidence": dict(e.evidence), "vcs": dict(e.vcs), "source": e.source},
                sort_keys=True,
            ),
            schema_version=e.schema,
        )
        for e in events
    )
    return events


async def _deliver(events: Sequence[EngineEvent], settings: EventSettings, root: Path) -> None:
    from .sinks import event_sinks

    for name in settings.sinks or (event_sinks.default or "jsonl",):
        try:
            await event_sinks.get(name).deliver(events, repo_root=root)
        except Exception as exc:  # the store keeps them; `repowise events` replays
            logger.warning("event sink %s failed: %s", name, exc)


async def compact(
    session: Any, repository_id: str, settings: EventSettings, *, now: datetime
) -> None:
    """Drop runs and events past retention; keep one run per ISO week past
    ``compact_after_days``, and no gap markers there. The newest run is always
    kept: the next diff reads it."""
    from repowise.core.persistence.models import ComponentSnapshot, EngineEventRecord, SnapshotRun

    now = _aware(now)
    drop_before = now - timedelta(days=settings.retention_days)
    weekly_before = now - timedelta(days=settings.compact_after_days)
    newest = await _newest_run(session, repository_id)
    runs = (
        await session.execute(
            select(SnapshotRun.id, SnapshotRun.taken_at, SnapshotRun.stamp)
            .where(
                SnapshotRun.repository_id == repository_id,
                SnapshotRun.taken_at < weekly_before,
            )
            .order_by(SnapshotRun.taken_at.desc(), SnapshotRun.id.desc())
        )
    ).all()
    doomed: list[str] = []
    kept_weeks: dict[tuple[int, int], str] = {}
    for run_id, taken_at, stamp in runs:
        if newest is not None and run_id == newest.id:
            continue
        taken_at = _aware(taken_at)
        week = taken_at.isocalendar()[:2]
        if taken_at < drop_before or stamp.startswith(GAP_STAMP) or week in kept_weeks:
            doomed.append(run_id)
        else:
            kept_weeks[week] = run_id  # newest first, so the week's last run
    if kept_weeks:
        await _mark_compacted(session, kept_weeks.values())
    for i in range(0, len(doomed), 500):
        chunk = doomed[i : i + 500]
        await session.execute(delete(ComponentSnapshot).where(ComponentSnapshot.run_id.in_(chunk)))
        await session.execute(delete(SnapshotRun).where(SnapshotRun.id.in_(chunk)))
    await session.execute(
        delete(EngineEventRecord).where(
            EngineEventRecord.repository_id == repository_id,
            EngineEventRecord.occurred_at < drop_before,
        )
    )


async def _mark_compacted(session: Any, run_ids: Iterable[str]) -> None:
    from sqlalchemy import update

    from repowise.core.persistence.models import SnapshotRun

    await session.execute(
        update(SnapshotRun).where(SnapshotRun.id.in_(list(run_ids))).values(compacted=True)
    )


def event_from_row(row: Any) -> EngineEvent:
    payload = json.loads(row.payload_json or "{}")
    return EngineEvent(
        id=row.id,
        kind=row.kind,
        repo_id=row.repository_id,
        component=row.component,
        severity=row.severity,
        occurred_at=_aware(row.occurred_at),
        evidence=payload.get("evidence") or {},
        stamp=row.stamp,
        commit_sha=row.commit_sha,
        base_commit_sha=row.base_commit_sha,
        source=payload.get("source") or "",
        vcs=payload.get("vcs") or {},
        schema=row.schema_version or EVENT_SCHEMA_VERSION,
    )


async def list_events(
    session: Any,
    repository_id: str,
    *,
    since: datetime | None = None,
    kinds: Sequence[str] = (),
    limit: int = 200,
) -> list[EngineEvent]:
    """Stored events, oldest first, from *since* on."""
    from repowise.core.persistence.models import EngineEventRecord

    query = select(EngineEventRecord).where(EngineEventRecord.repository_id == repository_id)
    if since is not None:
        query = query.where(EngineEventRecord.occurred_at >= since)
    if kinds:
        query = query.where(EngineEventRecord.kind.in_(list(kinds)))
    query = query.order_by(EngineEventRecord.occurred_at, EngineEventRecord.id).limit(limit)
    return [event_from_row(r) for r in await session.scalars(query)]
