"""The overview's ``next_actions`` block: the same stored actions the UI ranks."""

from __future__ import annotations

import logging
from typing import Any

from repowise.core.analysis.actions.engine import HEAD
from repowise.core.persistence.crud.analysis.actions import load_actions_view

logger = logging.getLogger(__name__)

#: Rows per horizon. The UI previews five; an agent orienting needs the head,
#: and ``total`` says how much sits behind it.
_PER_HORIZON = HEAD

_FIELDS = ("id", "tier", "title", "impact", "done_when")


def _compact(action: dict[str, Any]) -> dict[str, Any]:
    row = {k: action.get(k) for k in _FIELDS}
    row["target"] = {k: v for k, v in (action.get("target") or {}).items() if v is not None}
    return row


def compact_actions_view(view: dict[str, Any]) -> dict[str, Any]:
    """The head of each horizon plus its totals, from a ``load_actions_view`` result."""
    block: dict[str, Any] = {"anchor": view.get("anchor")}
    for name, horizon in (view.get("horizons") or {}).items():
        block[name] = {
            "total": horizon.get("total", 0),
            "by_tier": horizon.get("by_tier", {}),
            "actions": [_compact(a) for a in horizon.get("actions", [])[:_PER_HORIZON]],
        }
    unavailable = sorted(view.get("unavailable") or {})
    if unavailable:
        block["unavailable"] = unavailable
    return block


async def _build_next_actions(session: Any, repository: Any) -> tuple[dict[str, Any], str | None]:
    """``(block, None)``, or ``({}, reason)`` when the actions could not be read.

    The loader already tolerates a store missing from an older index; this
    guard is for anything else, so the overview never fails on its account.
    The savepoint keeps a failed read from poisoning the session the rest of
    the overview still uses.
    """
    try:
        async with session.begin_nested():
            view = await load_actions_view(session, repository.id)
        return compact_actions_view(view), None
    except Exception as exc:
        logger.warning("get_overview: next actions unavailable: %s", exc)
        return {}, f"Next actions could not be read from this index ({type(exc).__name__})."
