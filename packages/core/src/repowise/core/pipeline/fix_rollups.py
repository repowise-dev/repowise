"""Attribute persisted fix events to symbols and roll them up onto git metadata.

The one impure half of the fix-history feature: :mod:`analysis.health.fix_attribution`
does the arithmetic, this module does the reads and writes. It runs after both
``fix_events`` and ``wiki_symbols`` are persisted — a full index (end of
``persist_git``) and an update (end of ``persist_incremental_fix_events``) — and
recomputes the whole repo's rollups from the stored rows each time.

Recomputing everything rather than patching the files an update touched is
deliberate. Decay means a rollup goes stale on its own with nothing changing in
the file, so "only files this update touched" would leave the rest of the repo
frozen at whatever mass it had when it was last edited. The pass is two indexed
queries and some arithmetic over a windowed table (~1k rows on this repo), which
is far cheaper than the drift would be to explain.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog

from repowise.core.analysis.health.fix_attribution import (
    SymbolSpan,
    attribute_ranges,
    attribution_kind,
    roll_up_file,
)

logger = structlog.get_logger(__name__)

__all__ = ["apply_fix_rollups"]

# Only fixes that changed production code count toward the magnet signal, the
# same filter #931 put on ``prior_defect_count`` — so the two numbers can never
# disagree about what a bug fix is.
_COUNTED_SHAPE = "code_fix"


async def apply_fix_rollups(session: Any, repo_id: str) -> int:
    """Recompute symbol attribution + ``GitMetadata`` fix rollups for *repo_id*.

    Returns the number of files whose rollup was written. Failure-isolated by
    the caller, like every other post-persist refresh: a rollup this run could
    not compute leaves the previous values in place rather than zeroing a
    surface.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import FixEvent, GitMetadata, WikiSymbol

    events = (
        (await session.execute(select(FixEvent).where(FixEvent.repository_id == repo_id)))
        .scalars()
        .all()
    )
    if not events:
        return 0

    paths = {e.file_path for e in events}

    symbols_by_path: dict[str, list[SymbolSpan]] = defaultdict(list)
    for symbol_id, file_path, start_line, end_line in (
        await session.execute(
            select(
                WikiSymbol.symbol_id,
                WikiSymbol.file_path,
                WikiSymbol.start_line,
                WikiSymbol.end_line,
            ).where(WikiSymbol.repository_id == repo_id, WikiSymbol.file_path.in_(paths))
        )
    ).all():
        symbols_by_path[file_path].append(
            SymbolSpan(symbol_id=symbol_id, start_line=start_line or 0, end_line=end_line or 0)
        )

    meta_rows = (
        (
            await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repo_id, GitMetadata.file_path.in_(paths)
                )
            )
        )
        .scalars()
        .all()
    )
    meta_by_path = {m.file_path: m for m in meta_rows}

    as_of = _as_of(events, meta_rows)

    # Pass 1: attribute each event to symbols, on the row itself.
    counted: dict[str, list[tuple[datetime | None, list[str]]]] = defaultdict(list)
    for event in events:
        symbol_ids = attribute_ranges(_old_ranges(event), symbols_by_path.get(event.file_path, []))
        meta = meta_by_path.get(event.file_path)
        event.symbol_ids_json = json.dumps(symbol_ids)
        event.attribution = attribution_kind(
            symbol_ids,
            _aware(event.committed_at),
            _aware(getattr(meta, "last_commit_at", None)),
        )
        if event.shape_kind == _COUNTED_SHAPE:
            counted[event.file_path].append((_aware(event.committed_at), symbol_ids))

    # Pass 2: roll the counted events up onto git metadata. Files with events but
    # no metadata row are skipped rather than created: git_metadata's key set is
    # owned by the git indexer.
    written = 0
    for file_path, meta in meta_by_path.items():
        rollup = roll_up_file(counted.get(file_path, []), as_of)
        meta.fix_mass = rollup.fix_mass
        meta.bug_magnet = rollup.bug_magnet
        meta.last_fix_at = rollup.last_fix_at
        meta.fix_symbol_counts_json = json.dumps(rollup.symbol_counts)
        written += 1

    await session.flush()
    logger.debug(
        "fix_rollups_applied",
        repo_id=repo_id,
        events=len(events),
        files=written,
        magnets=sum(1 for m in meta_by_path.values() if m.bug_magnet),
    )
    return written


def _old_ranges(event: Any) -> list[list[int]]:
    try:
        return json.loads(event.old_ranges_json or "[]")
    except (TypeError, ValueError):
        return []


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; the arithmetic needs them comparable."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _as_of(events: list[Any], meta_rows: list[Any]) -> datetime:
    """The "now" decay is measured from: the repo's newest commit.

    Not wall-clock time. Anchoring to the repo means two indexes of the same
    checkout produce the same rollups, which is what the update-parity check
    asserts, and it keeps a dormant repo's mass decaying rather than freezing at
    whatever it was when work stopped — the newest commit is genuinely how old
    that repo's newest code is.
    """
    stamps = [_aware(m.last_commit_at) for m in meta_rows]
    stamps += [_aware(e.committed_at) for e in events]
    known = [s for s in stamps if s is not None]
    return max(known) if known else datetime.now(UTC)
