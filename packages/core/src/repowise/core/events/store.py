"""Record a run's snapshot, diff it into events, keep history bounded, read events.

:func:`record_snapshot` runs once per index run, after every store is final.
It snapshots the components, and when the newest stored run is the index run
just before this one and carries the same stamp, it stores the typed
occurrences between the two and hands them to the configured sinks. A stamp
change (a new analyzer, parser, git-history or contract-extraction version)
starts a fresh baseline instead: figures computed under different rules are
not comparable, so their difference is not an event. The contract kinds
compare against the last run that measured contracts instead, since a run
between them may have had none available (:func:`_contract_pairs`).

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

from .diff import contract_occurrences, diff_snapshots
from .model import EVENT_SCHEMA_VERSION, KINDS, EngineEvent, event_id, severity_for

logger = logging.getLogger(__name__)

#: Stamp prefix of a gap marker: an index run with no snapshot.
GAP_STAMP = "gap:"
#: Kinds read from the contracts the workspace's cross-repo pass extracts.
CONTRACT_KINDS = frozenset({"contract_added", "contract_broken"})
#: Earlier runs read when looking for a component's last measured contracts.
#: Ceiling: past it a contract change waits for the next measured pair.
#: Upgrade path: a column on the run saying its contracts were measured.
CONTRACT_LOOKBACK_RUNS = 100
#: ``session.info`` keys of the events waiting for the caller's commit.
_PENDING = "repowise.events.pending"
_LISTENING = "repowise.events.listening"
#: Config errors already logged by this process; each is named once.
_WARNED: set[str] = set()


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

    The events reach the sinks once the caller's session commits
    (:func:`_deliver_after_commit`); a transaction that rolls back delivers
    nothing.
    """
    from repowise.core.persistence.models import ComponentSnapshot, Repository, SnapshotRun

    from .snapshot import collect_facts, current_stamp

    repo = await session.get(Repository, repository_id)
    if repo is None or not repo.local_path:
        return []
    root = Path(repo.local_path)
    settings = load_event_settings(root)
    for error in settings.errors:
        if error not in _WARNED:
            _WARNED.add(error)
            logger.warning("%s (kept the default)", error)
    if not settings.enabled:
        await record_gap(session, repository_id, "disabled", now=now)
        return []
    stamp = current_stamp()
    head = repo.head_commit
    previous = await _newest_run(session, repository_id)
    if previous is not None and head and previous.commit_sha == head and previous.stamp == stamp:
        return []

    facts = await collect_facts(session, repository_id, root, head_commit=head)
    taken_at = now or datetime.now(UTC)
    run = SnapshotRun(repository_id=repository_id, taken_at=taken_at, commit_sha=head, stamp=stamp)
    session.add(run)
    await session.flush()
    session.add_all(
        ComponentSnapshot(run_id=run.id, component=name, facts_json=json.dumps(f, sort_keys=True))
        for name, f in facts.items()
    )

    pairs: list[tuple[Any, Any]] = []
    # A gap marker's stamp never equals a real one, so a run after it is a baseline.
    if previous is not None and previous.stamp == stamp:
        before = await run_facts(session, previous.id)
        kinds = settings.kinds - CONTRACT_KINDS
        pairs = [(previous, o) for o in diff_snapshots(before, facts) if o[0] in kinds]
    pairs += await _contract_pairs(session, run, facts, settings.kinds & CONTRACT_KINDS)
    events = await _store_events(session, repo, run, pairs, facts, taken_at)
    await compact(session, repository_id, settings, now=taken_at)
    await session.flush()
    _deliver_after_commit(session, events, settings, root)
    return events


async def refresh_contract_facts(session: Any, repository_id: str) -> list[EngineEvent]:
    """Measure the newest run's contracts once the workspace has extracted them.

    :func:`record_snapshot` runs before the cross-repo pass extracts contracts
    at the new commit, so that run stored them as unavailable. Called after
    extraction, this rewrites the same run's contract facts and stores its
    contract events (:func:`_contract_pairs`). Idempotent: event ids are keyed
    on the two runs compared. A run whose commit or
    stamp is no longer current, or contracts still unavailable, is left alone.
    """
    from repowise.core.persistence.models import Repository

    from .snapshot import contract_facts

    repo = await session.get(Repository, repository_id)
    if repo is None or not repo.local_path:
        return []
    root = Path(repo.local_path)
    settings = load_event_settings(root)
    if not settings.enabled:
        return []
    run = await _refreshable_run(session, repo)
    if run is None:
        return []
    measured = await contract_facts(session, repository_id, root, head_commit=run.commit_sha)
    if measured is None or not await _still_current(session, repository_id, run):
        return []
    after = await _fill_contracts(session, run, measured)
    pairs = await _contract_pairs(session, run, after, settings.kinds & CONTRACT_KINDS)
    events = await _store_events(session, repo, run, pairs, after, _aware(run.taken_at))
    await session.flush()
    _deliver_after_commit(session, events, settings, root)
    return events


async def _refreshable_run(session: Any, repo: Any) -> Any:
    """The newest run, when it is at the repo's commit under the current stamp."""
    from .snapshot import current_stamp

    run = await _newest_run(session, repo.id)
    if run is None or not run.commit_sha or run.commit_sha != repo.head_commit:
        return None
    return run if run.stamp == current_stamp() else None


async def _still_current(session: Any, repository_id: str, run: Any) -> bool:
    """Whether *run* is still the newest and the repo still at its commit:
    another update may have moved either during extraction."""
    from repowise.core.persistence.models import Repository

    head = await session.scalar(
        select(Repository.head_commit).where(Repository.id == repository_id)
    )
    newest = await _newest_run(session, repository_id)
    return head == run.commit_sha and newest is not None and newest.id == run.id


async def _fill_contracts(
    session: Any, run: Any, measured: dict[str, tuple[str, dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    """Write *measured* contracts into *run*'s component rows; its facts after."""
    from repowise.core.persistence.models import ComponentSnapshot

    after: dict[str, dict[str, Any]] = {}
    for row in await session.scalars(
        select(ComponentSnapshot).where(ComponentSnapshot.run_id == run.id)
    ):
        facts = json.loads(row.facts_json)
        definition, contracts = measured.get(row.component, (None, None))
        # A component re-drawn since the snapshot keeps what it stored.
        if definition == facts.get("definition") and facts.get("contracts") != contracts:
            facts["contracts"] = contracts
            row.facts_json = json.dumps(facts, sort_keys=True)
        after[row.component] = facts
    return after


def _measured(facts: dict[str, Any]) -> bool:
    contracts = facts.get("contracts")
    return isinstance(contracts, dict) and contracts.get("basis") == "measured"


async def _contract_pairs(
    session: Any, run: Any, after: dict[str, dict[str, Any]], kinds: frozenset[str]
) -> list[tuple[Any, Any]]:
    """``(compared run, occurrence)`` for the contract kinds of *run*.

    Each component with measured contracts is compared with the most recent
    earlier run of the same stamp that measured them under the same
    definition. Runs whose contracts were unavailable (an index-only update
    skips the cross-repo pass) are passed over, never read as every contract
    removed; a stamp change, a gap marker, or the component missing or
    re-drawn ends the search.
    """
    from repowise.core.persistence.models import SnapshotRun

    pending = {name: f for name, f in after.items() if _measured(f)}
    if not kinds or not pending:
        return []
    earlier = await session.scalars(
        select(SnapshotRun)
        .where(SnapshotRun.repository_id == run.repository_id, SnapshotRun.id != run.id)
        .order_by(SnapshotRun.taken_at.desc(), SnapshotRun.id.desc())
        .limit(CONTRACT_LOOKBACK_RUNS)
    )
    out: list[tuple[Any, Any]] = []
    for prev in earlier:
        if prev.stamp != run.stamp:
            break
        before = await run_facts(session, prev.id, components=pending)
        for name in list(pending):
            facts = before.get(name)
            if facts is None or facts.get("definition") != pending[name].get("definition"):
                del pending[name]
            elif _measured(facts):
                found = contract_occurrences(name, facts, pending.pop(name))
                out.extend((prev, o) for o in found if o[0] in kinds)
        if not pending:
            break
    return out


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


async def run_facts(
    session: Any, run_id: str, *, components: Iterable[str] | None = None
) -> dict[str, dict[str, Any]]:
    """The component facts one snapshot run stored, by component name; only
    *components* when given."""
    from repowise.core.persistence.models import ComponentSnapshot

    query = select(ComponentSnapshot.component, ComponentSnapshot.facts_json).where(
        ComponentSnapshot.run_id == run_id
    )
    if components is not None:
        query = query.where(ComponentSnapshot.component.in_(sorted(components)))
    rows = await session.execute(query)
    return {name: json.loads(raw) for name, raw in rows}


async def _store_events(
    session: Any,
    repo: Any,
    run: Any,
    pairs: list[tuple[Any, Any]],
    after: dict[str, dict[str, Any]],
    taken_at: datetime,
) -> list[EngineEvent]:
    """Store ``(compared run, occurrence)`` pairs of *run* as events."""
    from repowise.core.persistence.models import Change, EngineEventRecord

    if not pairs:
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
            # Keyed on the compared run, not only the commits: a pair seen
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
        for previous, (kind, component, subject, evidence) in pairs
    ]
    table = EngineEventRecord.__table__
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    # Insert-or-ignore on the id: an event another run or process already
    # stored is neither stored nor delivered twice.
    stmt = insert(table).on_conflict_do_nothing(index_elements=[table.c.id]).returning(table.c.id)
    stored: list[EngineEvent] = []
    for e in events:
        row = {
            "id": e.id,
            "repository_id": e.repo_id,
            "kind": e.kind,
            "component": e.component,
            "severity": e.severity,
            "occurred_at": e.occurred_at,
            "commit_sha": e.commit_sha,
            "base_commit_sha": e.base_commit_sha,
            "stamp": e.stamp,
            "payload_json": json.dumps(
                {"evidence": dict(e.evidence), "vcs": dict(e.vcs), "source": e.source},
                sort_keys=True,
            ),
            "schema_version": e.schema,
        }
        if (await session.execute(stmt.values(**row))).scalar() is not None:
            stored.append(e)
    return stored


def _deliver_after_commit(
    session: Any, events: Sequence[EngineEvent], settings: EventSettings, root: Path
) -> None:
    """Hand *events* to the sinks once the session's transaction commits.

    A rollback drops them; the retry stores and delivers the same ids. The
    commit hook runs in the async session's greenlet, so the sinks are
    awaited there.
    """
    from sqlalchemy import event

    if not events:
        return
    sync = session.sync_session
    sync.info.setdefault(_PENDING, []).append((list(events), settings, root))
    if not sync.info.get(_LISTENING):
        sync.info[_LISTENING] = True
        event.listen(sync, "after_commit", _on_commit)
        event.listen(sync, "after_rollback", lambda s: s.info.pop(_PENDING, None))


def _on_commit(sync_session: Any) -> None:
    from sqlalchemy.util import await_only

    for events, settings, root in sync_session.info.pop(_PENDING, []):
        try:
            await_only(_deliver(events, settings, root))
        except Exception as exc:  # the store keeps them; `repowise events` replays
            logger.warning("events not delivered: %s", exc)


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


async def snapshot_run_count(session: Any, repository_id: str) -> int:
    """Component snapshots stored for the repository, gap markers left out."""
    from sqlalchemy import func

    from repowise.core.persistence.models import SnapshotRun

    runs = await session.scalar(
        select(func.count()).where(
            SnapshotRun.repository_id == repository_id, ~SnapshotRun.stamp.startswith(GAP_STAMP)
        )
    )
    return int(runs or 0)


def empty_events_reason(runs: int, *, filtered: bool) -> tuple[str, str]:
    """Why an event list is empty, as ``(code, sentence)``.

    ``baseline_only``: fewer than two snapshots, so nothing could be compared
    yet; ``no_match``: events exist but none pass the filters asked;
    ``nothing_moved``: runs were compared and nothing crossed a threshold.
    """
    if runs < 2:
        return "baseline_only", (
            f"No events yet: {runs} component snapshot(s) stored. Events are what "
            "moved between two index runs in a row, so they start after the next "
            "`repowise update` on a new commit."
        )
    if filtered:
        return "no_match", f"No events match these filters across {runs} stored component snapshots."
    return "nothing_moved", f"No events across {runs} stored component snapshots."
