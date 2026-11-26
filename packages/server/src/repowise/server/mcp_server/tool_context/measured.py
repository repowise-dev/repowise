"""get_context's ``measured`` line: what a stored profile run measured here.

One short string on a symbol or file card, only when ``symbol_hotness`` has
a row for the symbol (or for a function in the file). No row means no
evidence, and then the key is absent: unknown is never written as cold.

The line always names its basis (workload class and name), the function's
self time (the cost it is ranked by: built-ins and libraries it called
included, waits excluded) with its interval when the run counted samples, how
much of the run went to repo code itself, and the age; ``ordering only`` for a
deterministic tracer. A ``unit_test`` run
reads "in tests". The ``lead_candidate`` tier is not worded as a claim until
its thresholds are calibrated: it reads as ranked.

Stale evidence (the body changed since the run) is flagged, never ranked: the
line says the measurement predates the body, so the agent knows to measure
again rather than trust an old rank.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.symbol_identity import file_of_symbol
from repowise.core.persistence.crud import hotness_for_symbols

_log = logging.getLogger("repowise.mcp.context")

#: Where a card's measured target waits between the card and the batched read.
#: Popped by :func:`attach_measured`, so it never reaches a response.
MEASURED_TARGET = "_measured_target"

#: Functions a file card's line names; the rest are counted.
MAX_NAMED_IN_FILE = 3

_RANKED = ("ranked", "lead_candidate")


def mark_measured_target(
    data: dict[str, Any], target_type: str, file_path: str | None, symbol_id: str | None
) -> None:
    """Record what a symbol or file card's ``measured`` line would read."""
    if target_type == "symbol" and symbol_id:
        data[MEASURED_TARGET] = ("symbol", symbol_id)
    elif target_type == "file" and file_path:
        # Symbol ids carry forward slashes, whatever the caller typed.
        data[MEASURED_TARGET] = ("file", file_path.replace("\\", "/"))


async def attach_measured(
    session: AsyncSession,
    repository_id: str,
    cards: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> None:
    """Give each marked card its ``measured`` line, in one keyed read for the call.

    Best effort: a store without the table, or a failed read, leaves every
    card as it would be with no evidence. No savepoint: it would cost two
    statements on every call, so the caller runs this as the session's last
    read, where a failed statement cannot touch another read.
    """
    wanted = {
        name: card.pop(MEASURED_TARGET) for name, card in cards.items() if MEASURED_TARGET in card
    }
    if not wanted:
        return
    symbols = {key for kind, key in wanted.values() if kind == "symbol"}
    files = {key for kind, key in wanted.values() if kind == "file"}
    try:
        # Last read of the call on purpose: with no savepoint, a failed read
        # here must not affect any other read.
        rows = await hotness_for_symbols(session, repository_id, symbols, files=files)
    except SQLAlchemyError:
        # A store older than the table reads as no evidence; so does a failed read.
        _log.debug("get_context: measured read failed", exc_info=True)
        return
    if not rows:
        return
    now = now or datetime.now(UTC)
    for name, (kind, key) in wanted.items():
        line = _line_for(kind, key, rows, now)
        if line:
            cards[name]["measured"] = line


def _line_for(kind: str, key: str, rows: dict[str, Any], now: datetime) -> str | None:
    if kind == "symbol":
        return symbol_line(rows[key], now) if key in rows else None
    return file_line([r for sid, r in rows.items() if file_of_symbol(sid) == key], now)


def symbol_line(row: Any, now: datetime) -> str:
    """One symbol's line, e.g. ``rank 3 of repo code in entry_point "cli build"
    (12% self, 9-15%; repo code itself 40%), 2d old``."""
    if row.freshness == "stale":
        return f"stale: measured {_basis(row)} before the body changed, {_age(row, now)}"
    details = "; ".join(d for d in (_share(row), _repo_self(row)) if d)
    paren = f" ({details})" if details else ""
    return f"{_place(row)} {_basis(row)}{paren}, {_age(row, now)}{_earlier(row)}"


def file_line(rows: list[Any], now: datetime) -> str | None:
    """A file's line: the basis once, then up to :data:`MAX_NAMED_IN_FILE` of
    its functions from that run, strongest first; the rest are counted."""
    if not rows:
        return None
    fresh = sorted((r for r in rows if r.freshness != "stale"), key=_strongest)
    if not fresh:
        if len(rows) == 1:
            return "stale: 1 function measured before its body changed"
        return f"stale: {len(rows)} functions measured before their bodies changed"
    lead = fresh[0]
    in_lead = [r for r in fresh if r.run_id == lead.run_id]
    named = in_lead[:MAX_NAMED_IN_FILE]
    items = "; ".join(_file_item(r) for r in named)
    repo_self = _repo_self(lead)
    paren = f" ({repo_self})" if repo_self else ""
    line = f"{_basis(lead)}{paren}, {_age(lead, now)}{_earlier(lead)}: {items}"
    # Counted apart, so nothing from another run reads as measured in this one.
    for count, label in (
        (len(in_lead) - len(named), "more measured"),
        (len(fresh) - len(in_lead), "in other runs"),
        (len(rows) - len(fresh), "stale"),
    ):
        if count:
            line += f"; {count} {label}"
    return line


def _strongest(row: Any) -> tuple[Any, ...]:
    return (
        row.tier not in _RANKED,
        row.rank if row.rank is not None else float("inf"),
        row.symbol_id,
    )


def _file_item(row: Any) -> str:
    name = row.symbol_id.split("::", 1)[-1]
    share = _share(row)
    place = f"rank {row.rank}" if row.tier in _RANKED and row.rank else "not ranked"
    return f"{name} {place}" + (f" ({share})" if share else "")


def _place(row: Any) -> str:
    if row.tier in _RANKED and row.rank:
        return f"rank {row.rank} of repo code"
    if row.samples is not None:
        return f"measured, not ranked ({row.samples} samples)"
    return "measured, not ranked"


def _basis(row: Any) -> str:
    name = row.workload_name or "unnamed"
    if row.workload_class == "unit_test":
        return f'in tests "{name}"'
    if not row.workload_class:
        return f'in "{name}"'
    return f'in {row.workload_class} "{name}"'


def _share(row: Any) -> str | None:
    """The self share (the cost) with its interval; a tracer run orders and gives none."""
    if row.ordering_only:
        return "ordering only"
    if row.self_share is not None and row.share_low is not None and row.share_high is not None:
        return f"{_pct(row.self_share)} self, {_low(row.share_low)}-{_pct(row.share_high)}"
    if row.self_share is not None:
        return f"{_pct(row.self_share)} self"
    return None


def _repo_self(row: Any) -> str | None:
    """How much of the run went to repo code itself; absent for a run stored without it."""
    if row.repo_self_share is None:
        return None
    return f"repo code itself {_pct(row.repo_self_share)}"


def _age(row: Any, now: datetime) -> str:
    at = row.measured_at
    if at is None:
        return "age unknown"
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    return f"{max(0, (now - at).days)}d old"


def _earlier(row: Any) -> str:
    return ", earlier commit, same body" if row.freshness == "body_unchanged" else ""


def _low(value: float) -> str:
    """An interval's lower end without its sign, never rounded up past the estimate."""
    # Rounded first: 0.57 * 100 is 56.99999999999999 in binary floating point.
    return str(int(round(value * 100, 6)))


def _pct(value: float) -> str:
    pct = value * 100
    if 0 < pct < 1:
        return "<1%"
    return f"{round(pct)}%"
