"""Record MCP counterfactual savings into the unified ledger.

A thin, best-effort bridge from a measured tool call to a ``mcp:<tool>`` row in
the same ``savings`` ledger the CLI/hook surfaces use. Mirrors
:class:`~repowise.server.mcp_server._budget.collector.OmissionCollector`'s
lifecycle: the SQLite handle is opened per event and closed immediately, so a
long-running MCP server never holds a WAL handle that would contend with
hook-side writers between the (rare, small) writes.

Failure posture matches the rest of distill: a failed write degrades to a
silent no-op. Recording savings must never perturb a tool response.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from repowise.core.distill.store import (
    OMISSIONS_DB_FILENAME,
    OMISSIONS_DIRNAME,
    OmissionStore,
)

logger = logging.getLogger(__name__)


def record_mcp_saving(
    repo_root: str | Path | None,
    tool: str,
    replaced_tokens: int,
    delivered_tokens: int,
) -> bool:
    """Append one ``mcp:<tool>`` counterfactual row. Returns True on write.

    ``raw_tokens`` is the counterfactual raw-exploration cost the answer
    replaced; ``distilled_tokens`` is what the agent actually received (measured
    after response-budget truncation, so the truncation saving is folded into
    the delta). Rows with no net saving (``replaced <= delivered``) are skipped —
    they would not have earned a marker either.

    The store is resolved **repo-locally** (``<repo>/.repowise/omissions``) — the
    exact sidecar the Costs endpoint reads — and only when it already exists.
    We deliberately do *not* fall back to the ``~/.repowise`` home store: a row
    landing there would be invisible to the dashboard and would pollute a global
    store across unrelated repos. Never raises.
    """
    if replaced_tokens <= delivered_tokens or not repo_root:
        return False
    db_path = Path(repo_root) / ".repowise" / OMISSIONS_DIRNAME / OMISSIONS_DB_FILENAME
    if not db_path.is_file():
        # No repo-local sidecar → repo never opted in via `repowise init`.
        return False
    try:
        store = OmissionStore(db_path)
    except Exception:
        logger.debug("mcp savings store open failed", exc_info=True)
        return False
    try:
        store.record_saving(
            filter_name=tool,
            source=f"mcp:{tool}",
            command=None,
            raw_tokens=replaced_tokens,
            distilled_tokens=delivered_tokens,
        )
        return True
    except Exception:
        logger.debug("mcp savings write failed; dropping silently", exc_info=True)
        return False
    finally:
        with contextlib.suppress(Exception):
            store.close()
