"""The one way a capture surface records what it saved.

Every surface — distill, the hooks, MCP, the native VS Code tools — writes
through this function. Not because the write is complicated, but because the
rules around it are, and they were previously re-decided per surface: which
database to open, whether to create one, and what to do when the write fails.
Three surfaces answered those three questions three ways.

Two of those answers are load-bearing and are fixed here.

**The sidecar is repo-local and is never created.** A savings row belongs to the
repository it describes, in the same file the costs page reads. Falling back to
``~/.repowise`` would put a row somewhere no dashboard looks and mix unrelated
repositories into one ledger, so an absent sidecar means the repository never
opted in and the event is dropped.

**Recording never raises.** Accounting sits behind a live tool call, a hook, and
a shell command. A ledger that can fail a user's command is worse than no
ledger, so every failure here is swallowed and logged at debug. The return value
says whether a row landed, for callers that want to stamp metadata only when one
did; nobody has to check it.

Short and WAL-friendly by construction: open, write, close. No handle outlives
the call, because two of the callers are hot paths and one of them is a hook
that runs on every tool use.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def sidecar_path(repo_root: str | Path) -> Path:
    """Where a repository's savings sidecar lives."""
    from repowise.core.distill.store import OMISSIONS_DB_FILENAME, OMISSIONS_DIRNAME

    return Path(repo_root) / ".repowise" / OMISSIONS_DIRNAME / OMISSIONS_DB_FILENAME


def record_event(repo_root: str | Path | None, payload: Mapping[str, Any]) -> bool:
    """Record one savings event against *repo_root*. Never raises.

    *payload* is the mapping :meth:`SavingsEvent.from_mapping` validates, minus
    ``repository_id``, which is taken from *repo_root* so a caller cannot
    attribute an event to a repository it is not writing to.

    Returns True only when a row was actually inserted. A retry of an event
    already recorded returns False, as does a dropped one, because in both cases
    this call added nothing.
    """
    if not repo_root:
        return False
    db_path = sidecar_path(repo_root)
    if not db_path.is_file():
        return False

    # Imported here rather than at module scope: ``distill.store`` pulls
    # structlog, which costs ~250ms, and the hook path measures its budget in
    # milliseconds. By the time anything calls this, the response is already out.
    try:
        from repowise.core.distill.store import OmissionStore
        from repowise.core.savings.contracts import SavingsEvent
    except Exception:  # pragma: no cover - import failure is environmental
        logger.debug("savings recorder unavailable", exc_info=True)
        return False

    try:
        # The repository is taken from the path being written to, overriding
        # whatever the payload said, so a surface holding a stale id cannot file
        # one repository's savings under another.
        event = SavingsEvent.from_mapping({**payload, "repository_id": str(repo_root)})
    except Exception:
        # A malformed event is a bug in the calling surface, not in the user's
        # command. Loud in the log, invisible to them.
        logger.debug("savings event rejected before write", exc_info=True)
        return False

    try:
        store = OmissionStore(db_path)
    except Exception:
        logger.debug("savings sidecar open failed", exc_info=True)
        return False
    try:
        return store.savings().record_event(event)
    except Exception:
        logger.debug("savings write failed; dropping silently", exc_info=True)
        return False
    finally:
        store.close()


def record_opportunity(repo_root: str | Path | None, payload: Mapping[str, Any]) -> bool:
    """Record one observed opportunity. Never raises.

    Kept deliberately separate from :func:`record_event`. An opportunity is
    behaviour that *could* have been optimised and was not, so it must never
    reach a total of what was saved; giving it its own function means no caller
    can pass one to the other by filling in a different field.
    """
    if not repo_root:
        return False
    db_path = sidecar_path(repo_root)
    if not db_path.is_file():
        return False

    try:
        from repowise.core.distill.store import OmissionStore
        from repowise.core.savings.contracts import OpportunityObservation
    except Exception:  # pragma: no cover - import failure is environmental
        logger.debug("savings recorder unavailable", exc_info=True)
        return False

    try:
        observation = OpportunityObservation.from_mapping(payload, repository_id=str(repo_root))
    except Exception:
        logger.debug("opportunity rejected before write", exc_info=True)
        return False

    try:
        store = OmissionStore(db_path)
    except Exception:
        logger.debug("savings sidecar open failed", exc_info=True)
        return False
    try:
        return store.savings().record_opportunity(observation)
    except Exception:
        logger.debug("opportunity write failed; dropping silently", exc_info=True)
        return False
    finally:
        store.close()
