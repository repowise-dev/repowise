"""The overview's ``next_actions`` block: the same stored actions the UI ranks."""

from __future__ import annotations

import logging
from typing import Any

from repowise.core.analysis.actions.engine import NEXT_ACTIONS_HEAD
from repowise.core.persistence.crud.analysis.actions import load_actions_view

logger = logging.getLogger(__name__)

#: Rows per horizon. The UI previews five; an agent orienting needs the head,
#: and ``total`` says how much sits behind it.
_PER_HORIZON = NEXT_ACTIONS_HEAD

_FIELDS = ("id", "tier", "title", "impact", "done_when")

#: The default row: what to do and where, with the impact cut to one line.
#: ``include=["actions"]`` restores ``done_when``, ``by_tier`` and the quarter.
_LEAN_FIELDS = ("id", "tier", "title", "impact")
_IMPACT_CHARS = 140


def _one_line(text: Any) -> Any:
    """The first sentence of *text*, cut at a word boundary to ``_IMPACT_CHARS``."""
    if not isinstance(text, str):
        return text
    flat = " ".join(text.split())
    head, dot, _ = flat.partition(". ")
    line = head + "." if dot else flat
    if len(line) <= _IMPACT_CHARS:
        return line
    return line[: _IMPACT_CHARS - 1].rsplit(" ", 1)[0].rstrip(";,") + "…"


def _compact(action: dict[str, Any], *, full: bool) -> dict[str, Any]:
    row = {k: action.get(k) for k in (_FIELDS if full else _LEAN_FIELDS)}
    if not full:
        row["impact"] = _one_line(row["impact"])
    row["target"] = {k: v for k, v in (action.get("target") or {}).items() if v is not None}
    return row


def compact_actions_view(view: dict[str, Any], *, full: bool = True) -> dict[str, Any]:
    """The head of each horizon plus its totals, from a ``load_actions_view`` result.

    ``full=False`` is the default overview's cut: rows for the nearest
    non-empty horizon only (the week, else the quarter, as ``repowise next``
    picks), and a bare total for the other.
    """
    horizons = view.get("horizons") or {}
    block: dict[str, Any] = {"anchor": view.get("anchor")} if full else {}
    lead = next((n for n, h in horizons.items() if h.get("actions")), None)
    for name, horizon in horizons.items():
        row: dict[str, Any] = {"total": horizon.get("total", 0)}
        if full:
            row["by_tier"] = horizon.get("by_tier", {})
        if full or name == lead:
            row["actions"] = [
                _compact(a, full=full) for a in horizon.get("actions", [])[:_PER_HORIZON]
            ]
        block[name] = row
    unavailable = sorted(view.get("unavailable") or {})
    if unavailable:
        block["unavailable"] = unavailable
    return block


async def _build_next_actions(
    session: Any, repository: Any, *, full: bool = False
) -> tuple[dict[str, Any], str | None]:
    """``(block, None)``, or ``({}, reason)`` when the actions could not be read.

    The loader already tolerates a store missing from an older index; this
    guard is for anything else, so the overview never fails on its account.
    The savepoint keeps a failed read from poisoning the session the rest of
    the overview still uses.
    """
    try:
        async with session.begin_nested():
            view = await load_actions_view(session, repository.id)
        return compact_actions_view(view, full=full), None
    except Exception as exc:
        logger.warning("get_overview: next actions unavailable: %s", exc)
        return {}, f"Next actions could not be read from this index ({type(exc).__name__})."
