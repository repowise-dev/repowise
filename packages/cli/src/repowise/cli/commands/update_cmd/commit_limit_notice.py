"""Telling a user that their config pins a history window below the default.

``commit_limit`` written in ``config.yaml`` is the user's choice and is never
changed for them. When it sits below the default window, say so once per store
and per pinned value, so a raised default does not pass a pinned repo by
unnoticed. Shares the shown-notice ledger with the other one-shot notices.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from repowise.cli.helpers import console, load_config, load_state
from repowise.cli.upgrade import SHOWN_NOTICES_KEY

log = structlog.get_logger(__name__)

_LEDGER_PREFIX = "commit_limit_pinned:"


def surface_pinned_commit_limit(repo_path: Path, *, emitter: Any, dry_run: bool) -> None:
    """Print one dim line, once, when ``commit_limit`` is pinned below the default.

    Interactive-terminal only, like the other one-shot notices, so a background
    post-commit update never spends it on a log nobody reads.
    """
    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT

    if not (console.is_terminal and emitter is None):
        return
    try:
        pinned = int(load_config(repo_path).get("commit_limit") or 0)
    except (TypeError, ValueError):
        return
    if not 0 < pinned < _DEFAULT_COMMIT_LIMIT:
        return
    key = f"{_LEDGER_PREFIX}{pinned}:{_DEFAULT_COMMIT_LIMIT}"
    state = load_state(repo_path)
    shown = state.get(SHOWN_NOTICES_KEY)
    if isinstance(shown, list) and key in shown:
        return

    console.print(
        f"[dim]commit_limit is pinned to {pinned} in config.yaml; "
        f"the default is now {_DEFAULT_COMMIT_LIMIT}.[/dim]"
    )
    if dry_run:
        return
    try:
        from repowise.cli.helpers import save_state

        shown = list(shown) if isinstance(shown, list) else []
        shown.append(key)
        state[SHOWN_NOTICES_KEY] = shown
        save_state(repo_path, state)
    except Exception as exc:  # a ledger write must never break a command
        log.debug("commit_limit_notice.record_failed", error=str(exc))
