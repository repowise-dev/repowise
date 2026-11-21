"""The overview's ``next_actions`` block: the same stored actions the UI ranks."""

from __future__ import annotations

import logging
from typing import Any

from repowise.core.analysis.actions.model import NEXT_ACTIONS_HEAD

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
    # A repository-wide target has no path; an empty one is not sent.
    row["target"] = {k: v for k, v in (action.get("target") or {}).items() if v not in (None, "")}
    return row


def without_evidence(view: dict[str, Any], ids: frozenset[str]) -> dict[str, Any]:
    """*view* less the actions that rest on *ids*, each horizon recounted.

    The agent surface carries no runtime evidence yet, so an action on a
    cause minted from a measurement alone stays on the people's surfaces.
    Ceiling: a horizon keeps the actions it stored, so one taken out is not
    replaced by the next.
    """
    if not ids:
        return view
    horizons = {}
    for name, horizon in (view.get("horizons") or {}).items():
        actions = horizon.get("actions") or []
        gone = [a for a in actions if ids.intersection(a.get("evidence_ids") or ())]
        if not gone:
            horizons[name] = horizon
            continue
        by_tier = dict(horizon.get("by_tier") or {})
        for action in gone:
            by_tier[action["tier"]] = by_tier.get(action["tier"], 0) - 1
        horizons[name] = {
            **horizon,
            "actions": [a for a in actions if not ids.intersection(a.get("evidence_ids") or ())],
            "total": horizon.get("total", 0) - len(gone),
            "by_tier": {k: v for k, v in by_tier.items() if v > 0},
        }
    return {**view, "horizons": horizons}


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
    # ``unavailable`` stays the sorted name list older readers expect;
    # ``unavailable_reasons`` says why each one is missing.
    setup = view.get("setup") or {}
    if setup.get("open"):
        # Only while steps are left, and only what an agent acts on: how many,
        # whether to offer the pass, and since when it could have.
        block["setup"] = {
            k: v for k, v in setup.items() if k in ("open", "offer", "first_call_at") and v
        }
    reasons = view.get("unavailable") or {}
    if reasons:
        block["unavailable"] = sorted(reasons)
        block["unavailable_reasons"] = dict(sorted(reasons.items()))
    return block


async def _build_next_actions(
    session: Any, repository: Any, *, full: bool = False, alias: str | None = None
) -> tuple[dict[str, Any], str | None]:
    """``(block, None)``, or ``({}, reason)`` when the actions could not be read.

    The loader already tolerates a store missing from an older index; this
    guard is for anything else, so the overview never fails on its account.
    The savepoint keeps a failed read from poisoning the session the rest of
    the overview still uses.
    """
    # Imported here so ``compact_actions_view`` stays free of persistence.
    from repowise.core.persistence.crud.analysis.actions import load_actions_view
    from repowise.core.persistence.crud.analysis.performance import measured_hotspot_ids
    from repowise.server.mcp_server._helpers import _cross_repo_cut

    try:
        async with session.begin_nested():
            setup = await setup_facts(session, repository)
            view = await load_actions_view(
                session, repository.id, setup=setup, **_cross_repo_cut(alias)
            )
            hidden = await measured_hotspot_ids(session, repository.id)
        return compact_actions_view(without_evidence(view, hidden), full=full), None
    except Exception as exc:
        logger.warning("get_overview: next actions unavailable: %s", exc)
        return {}, f"Next actions could not be read from this index ({type(exc).__name__})."


async def setup_facts(session: Any, repository: Any) -> Any | None:
    """The live setup facts when the checkout is on this machine, else ``None``.

    This call is an agent call, so the pass is offered from it on even before
    the ledger records it. ``get_health`` reads the same facts to open a
    setup step by id. The rewrite hook lives in the agent's settings,
    which the server does not read, so the distill step stays out here.
    """
    from pathlib import Path

    from repowise.core.persistence.crud.analysis.setup_facts import (
        load_setup_facts,
        with_current_call,
    )
    from repowise.server.mcp_server._meta import served_root

    # The served checkout, none for a context with no source: the stored path
    # names where the index was built, which such a server cannot read.
    served = served_root(repository)
    root = Path(served or "")
    if not served or not root.is_dir():
        return None
    try:
        return with_current_call(await load_setup_facts(session, repository.id, root))
    except Exception as exc:  # the rest of the actions must not fail on its account
        logger.warning("get_overview: setup steps unavailable: %s", exc)
        return None
