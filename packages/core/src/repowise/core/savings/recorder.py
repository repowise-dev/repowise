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

import contextlib
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


#: Spelled out rather than imported from ``distill.store``, which owns these
#: constants but costs a structlog import to read them -- the same reason the
#: hook path spells the path out. Kept honest by
#: ``test_the_sidecar_path_matches_the_store_that_owns_it``.
_SIDECAR_PARTS = (".repowise", "omissions", "omissions.db")


def sidecar_path(repo_root: str | Path) -> Path:
    """Where a repository's savings sidecar lives."""
    return Path(repo_root).joinpath(*_SIDECAR_PARTS)


def record_event(repo_root: str | Path | None, payload: Mapping[str, Any]) -> bool:
    """Record one savings event against *repo_root*. Never raises.

    *payload* is the mapping :meth:`SavingsEvent.from_mapping` validates, minus
    ``repository_id``, which is taken from *repo_root* so a caller cannot
    attribute an event to a repository it is not writing to.

    Returns True only when a row was actually inserted. A retry of an event
    already recorded returns False, as does a dropped one, because in both cases
    this call added nothing.
    """
    try:
        if not repo_root:
            return False
        db_path = sidecar_path(repo_root)
        if not db_path.is_file():
            return False
        event = _build(payload, repo_root)
        if event is None:
            return False
        # ``distill.store`` pulls structlog, which is why this is not imported
        # at module scope. A surface that measures its latency in milliseconds
        # reaches the ledger through :func:`record_event_on` instead.
        from repowise.core.distill.store import OmissionStore

        store = OmissionStore(db_path)
    except Exception:
        logger.debug("savings recorder could not open the sidecar", exc_info=True)
        return False
    try:
        return _write(store.savings(), event)
    finally:
        with contextlib.suppress(Exception):
            store.close()


def record_event_in(store: Any, repo_root: str | Path | None, payload: Mapping[str, Any]) -> bool:
    """Record an event through an already-open store. Never raises.

    Same rules as :func:`record_event`, minus the open and close. A surface that
    already holds the sidecar open uses this rather than the other: a second
    connection to the same file, while the first one holds a write lock, is a
    lock fight rather than a second writer.
    """
    if store is None or not repo_root:
        return False
    event = _build(payload, repo_root)
    if event is None:
        return False
    try:
        return _write(store.savings(), event)
    except Exception:
        logger.debug("savings write failed; dropping silently", exc_info=True)
        return False


def record_event_on(
    connection: Any, repo_root: str | Path | None, payload: Mapping[str, Any]
) -> bool:
    """Record an event on a raw sqlite connection. Never raises.

    For the surface that cannot afford :class:`OmissionStore`. The hook budgets
    itself in milliseconds and opens the sidecar with plain ``sqlite3``
    precisely so it never imports ``distill.store``, which pulls structlog at
    roughly 250ms. It reaches the ledger through the same validation and against
    the same repository as every other surface, on the connection it already
    holds.

    The ceiling: a raw connection does not run the schema upgrade, so an event
    written against a sidecar older than the event tables is dropped rather than
    migrating the store from inside a hook. Any other opener repairs it, and
    paying a migration on a latency-critical path is the worse trade.
    """
    if connection is None or not repo_root:
        return False
    event = _build(payload, repo_root)
    if event is None:
        return False
    try:
        from repowise.core.savings.repository import SavingsRepository

        return _write(SavingsRepository(connection), event)
    except Exception:
        logger.debug("savings write failed; dropping silently", exc_info=True)
        return False


def _build(payload: Mapping[str, Any], repo_root: str | Path) -> Any:
    """Validate *payload* into an event, or ``None`` when it is malformed.

    The repository is taken from the path being written to, overriding whatever
    the payload said, so a surface holding a stale id cannot file one
    repository's savings under another. A malformed event is a bug in the
    calling surface, not in the user's command: loud in the log, invisible to
    them.

    ``accept_event_id`` is set, so the id the surface minted is the id stored.
    Without it the contract quietly substitutes a fresh one, and then nothing
    can join a log line to its row, and the idempotency key is scoped on an id
    that was discarded -- which makes retry deduplication unreachable while
    appearing to work.
    """
    try:
        from repowise.core.savings.contracts import SavingsEvent

        return SavingsEvent.from_mapping(
            {**payload, "repository_id": str(repo_root)}, accept_event_id=True
        )
    except Exception:
        logger.debug("savings event rejected before write", exc_info=True)
        return None


def _write(repository: Any, event: Any) -> bool:
    try:
        return bool(repository.record_event(event))
    except Exception:
        logger.debug("savings write failed; dropping silently", exc_info=True)
        return False


def record_opportunity(repo_root: str | Path | None, payload: Mapping[str, Any]) -> bool:
    """Record one observed opportunity. Never raises.

    Kept deliberately separate from :func:`record_event`. An opportunity is
    behaviour that *could* have been optimised and was not, so it must never
    reach a total of what was saved; giving it its own function means no caller
    can pass one to the other by filling in a different field.
    """
    try:
        if not repo_root:
            return False
        db_path = sidecar_path(repo_root)
        if not db_path.is_file():
            return False

        from repowise.core.distill.store import OmissionStore
        from repowise.core.savings.contracts import OpportunityObservation

        observation = OpportunityObservation.from_mapping(payload, repository_id=str(repo_root))
        store = OmissionStore(db_path)
    except Exception:
        logger.debug("opportunity rejected before write", exc_info=True)
        return False
    try:
        return bool(store.savings().record_opportunity(observation))
    except Exception:
        logger.debug("opportunity write failed; dropping silently", exc_info=True)
        return False
    finally:
        with contextlib.suppress(Exception):
            store.close()
