"""Ledger the decisions each ``get_why`` reply delivered.

Runs on the reply the agent actually received, outside every budget pass, so a
record shed for size is not counted as delivered. Kept out of the ``tool_why``
modules: what a reply carried is read off its final shape, whichever mode built
it.

A stdio connection is one agent session, so the server-minted connection id
stands in for the session and a record is counted once per connection. With no
connection (a direct call), each call counts on its own. Both are prefixed so
they never read as a transcript session id.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_RECORD_ID = re.compile(r"^[0-9a-f]{32}$")


def delivered_decision_ids(result: Any) -> list[str]:
    """Ids of the decision rows in *result*, in reply order.

    A served decision row is the dict that carries ``authority`` beside its
    ``id``; every mode stamps that on each row it emits, and lineage pointers
    and evidence refs carry an id without it.
    """
    out: dict[str, None] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            rid = node.get("id")
            if "authority" in node and isinstance(rid, str) and _RECORD_ID.match(rid):
                out.setdefault(rid)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(result)
    return list(out)


def record_why_delivery(tool: str, live: Any, result: Any) -> None:
    """Ledger what *result* delivered when *tool* is ``get_why``. Never raises."""
    if tool != "get_why" or live is None or not live.repo_root:
        return
    try:
        from repowise.server.mcp_server import _state

        # An unbound server (a test, an ad-hoc call) has no ledger of its own;
        # the budget layer's working-directory fallback must not pick one.
        if getattr(_state, "_repo_path", None) is None:
            return
        ids = delivered_decision_ids(result)
        if not ids:
            return
        from repowise.core.sessions.deliveries import record_why_deliveries

        session = f"mcp:{live.session_id}" if live.session_id else f"mcp-call:{live.event_id}"
        record_why_deliveries(live.repo_root, session, ids)
    except Exception:  # pragma: no cover - a ledger write never breaks a reply
        logger.debug("get_why delivery ledger write failed", exc_info=True)
