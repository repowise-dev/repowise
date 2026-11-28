"""Run the sources plugins register against this repository's index.

A fetching seam is one whose implementations read from a service with
settings and credentials the host holds (``evidence-source``). Each source
declares what it reads as ``settings``, a tuple of
:class:`~repowise.core.settings.Setting` built with
:func:`~repowise.core.settings.plugins.plugin_setting` (an empty tuple for
one that needs none). A source is *configured* once each of those with no
default resolves. A source with no ``settings`` attribute is run only by a host
that calls ``run_fetch`` with credentials of its own, never by repowise. ``repowise sources sync`` and ``repowise update``
run each configured source through its seam's ``run_fetch``, storing with the
seam's own store, and keep where each one stopped in ``state.json``.

The host builds :class:`~repowise.core.evidence.sources.SourceRequest` from the
registry: plain values in ``settings`` and secrets in ``credentials``, both
keyed by setting key (``credentials`` is ``None`` for a source with no secret).
Credentials are read from the environment when the source runs and are never
written to ``state.json``, a log or a result.

A new fetching seam is one more entry in :func:`fetching_seams`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "LEDGER_KEY",
    "FetchingSeam",
    "PlannedSource",
    "fetching_seams",
    "ledger_cursor",
    "not_configured",
    "plan_sources",
    "record_result",
    "sync_command",
    "sync_source",
    "unsynced_sources",
]

#: ``state.json`` key holding ``{"<seam>:<name>": {cursor, settings_fp, last_state,
#: last_run_at, last_stored_at, last_batches, last_message}}``. ``last_message`` is
#: the result's message, which has every secret masked.
LEDGER_KEY = "source_sync"


def sync_command(name: str | None = None) -> str:
    """``repowise sources sync``, for one source when *name* is given."""
    return f"repowise sources sync --source {name}" if name else "repowise sources sync"


@dataclass(frozen=True)
class FetchingSeam:
    """One seam whose sources ``sources sync`` runs."""

    name: str
    #: The seam's ``SeamRegistry``.
    registry: Any
    #: ``run_fetch(source, request, store) -> FetchOutcome`` for this seam.
    run: Callable[..., Awaitable[Any]]
    #: ``store_for(session, repo, repo_path)`` -> the ``store`` ``run`` hands each batch to.
    store_for: Callable[[Any, Any, Path], Callable[[Any], Awaitable[dict[str, Any]]]]
    #: The command that shows what a sync stored.
    review: str
    #: Sync at the start of ``repowise update`` instead of its end, for a seam
    #: whose facts the update itself reads (``issue-source``).
    before_update: bool = False


def _evidence_store(
    session: Any, repo: Any, repo_path: Path
) -> Callable[[Any], Awaitable[dict[str, Any]]]:
    async def store(batch: Any) -> dict[str, Any]:
        from repowise.core.evidence.add import ingest_batch

        return await ingest_batch(session, repo, batch, repo_path=repo_path)

    return store


@dataclass(frozen=True)
class _IssueTarget:
    """What an ``issue-source`` sync writes to: it builds its own request from the checkout."""

    session: Any
    repo_path: Path


def _issue_target(session: Any, repo: Any, repo_path: Path) -> Any:
    return _IssueTarget(session, repo_path)


async def _run_issue_sync(source: Any, request: Any, target: _IssueTarget) -> Any:
    """Ask *source* about the defect window's commits and store its links."""
    from repowise.core.issues.sync import sync_issues

    return await sync_issues(
        target.session,
        request.repository_id,
        target.repo_path,
        source,
        repository_name=request.repository_name,
        settings=request.settings,
        credentials=request.credentials,
    )


def fetching_seams() -> tuple[FetchingSeam, ...]:
    """Every seam ``sources sync`` runs, in the order it runs them."""
    from repowise.core.evidence.sources import evidence_sources, run_fetch
    from repowise.core.issues import issue_sources

    return (
        FetchingSeam(
            "evidence-source",
            evidence_sources,
            run_fetch,
            _evidence_store,
            "repowise evidence status",
        ),
        # Before the update, so a commit it indexes is counted with its linked issues.
        FetchingSeam(
            "issue-source",
            issue_sources,
            _run_issue_sync,
            _issue_target,
            "repowise update",
            before_update=True,
        ),
    )


@dataclass(frozen=True)
class PlannedSource:
    """One registered source, with what the registry resolved for it."""

    seam: FetchingSeam
    name: str
    source: Any
    settings: dict[str, Any]
    #: Secret values by key, ``None`` when the source declares no secret. Never printed.
    credentials: dict[str, Any] | None = field(repr=False)
    #: Required settings with no value, in declaration order.
    missing: tuple[Any, ...] = ()
    #: The source declares ``settings``, so repowise may run it; else only its host does.
    declared: bool = True

    @property
    def ledger_id(self) -> str:
        return f"{self.seam.name}:{self.name}"

    @property
    def settings_fp(self) -> str:
        """A short hash of the plain settings, so a changed one starts from no cursor.

        Secrets are left out: they are never written down, even hashed, and a
        rotated token still reads the same data.
        """
        text = json.dumps(self.settings, sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    @property
    def configured(self) -> bool:
        return self.declared and not self.missing


def plan_sources(
    config: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None
) -> list[PlannedSource]:
    """Every source on every fetching seam, loading plugins first, configured or not."""
    from repowise.core.settings.plugins import source_values

    planned = []
    for seam in fetching_seams():
        for name, source in seam.registry.all():
            declared = tuple(getattr(source, "settings", None) or ())
            plain, secrets, missing = source_values(declared, config, environ)
            has_secret = any(s.secret for s in declared)
            planned.append(
                PlannedSource(
                    seam,
                    name,
                    source,
                    plain,
                    secrets if has_secret else None,
                    tuple(missing),
                    declared=hasattr(source, "settings"),
                )
            )
    return planned


def not_configured(planned: PlannedSource) -> dict[str, Any]:
    """The result for a source whose required settings are not all set."""
    from repowise.core.settings.plugins import fix_command

    if not planned.declared:
        return _result(
            planned,
            "not_configured",
            message="it declares no settings, so only a host that holds its credentials runs it",
            next_command="repowise plugins",
        )
    fixes = [
        {"key": s.key, "env": s.env.name if s.env else None, "fix": fix_command(s)}
        for s in planned.missing
    ]
    names = ", ".join(s.env.name if s.secret and s.env else s.key for s in planned.missing)
    return _result(
        planned,
        "not_configured",
        message=f"needs {names}",
        next_command=fixes[0]["fix"],
        missing=fixes,
    )


def _result(
    planned: PlannedSource,
    state: str,
    *,
    message: str | None = None,
    next_command: str | None = None,
    batches: int = 0,
    missing: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "source": planned.name,
        "seam": planned.seam.name,
        "state": state,
        "batches": batches,
        "message": message,
        "next_command": next_command,
        "missing": missing or [],
    }


def _next_command(planned: PlannedSource, status: str) -> str:
    """The one command that follows a fetch that ended in *status*."""
    if status == "ok":
        return planned.seam.review
    if status == "unavailable":
        return sync_command(planned.name)
    if status == "refused":
        # The far side refused a value the settings hold: list them to fix one.
        return "repowise config list --area plugins"
    return "repowise plugins"


async def sync_source(
    planned: PlannedSource,
    *,
    session: Any,
    repo: Any,
    repo_path: Path,
    cursor: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Fetch *planned* from *cursor* into the index; ``(result, cursor to keep)``.

    The caller commits *session* and then records the cursor, so a crash in
    between fetches again from the old cursor, which stores nothing twice.
    """
    from repowise.core.evidence.sources import SourceRequest

    request = SourceRequest(
        repository_id=repo.id,
        repository_name=repo.name or "",
        cursor=cursor,
        settings=planned.settings,
        credentials=planned.credentials,
    )
    store = planned.seam.store_for(session, repo, repo_path)
    outcome = await planned.seam.run(planned.source, request, store)
    state = "stored" if outcome.status == "ok" else outcome.status
    result = _result(
        planned,
        state,
        message=_masked(outcome.error, planned.credentials),
        next_command=_next_command(planned, outcome.status),
        batches=len(outcome.stored),
    )
    return result, outcome.cursor


def _masked(message: str | None, credentials: Mapping[str, Any] | None) -> str | None:
    """*message* with each secret value replaced by ``***``.

    A driver's error can quote the connection string it was given, and this
    message is printed and returned as JSON.
    """
    if not message:
        return message
    for value in (credentials or {}).values():
        text = str(value)
        if text:
            message = message.replace(text, "***")
    return message


def _ledger(state: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = (state or {}).get(LEDGER_KEY)
    return raw if isinstance(raw, dict) else {}


def ledger_cursor(state: Mapping[str, Any] | None, planned: PlannedSource) -> str | None:
    """The cursor *planned*'s last sync kept, ``None`` before its first.

    Also ``None`` once its plain settings changed (another database, another
    team): the cursor is the source's own position, which means nothing there.
    """
    entry = _ledger(state).get(planned.ledger_id)
    if not isinstance(entry, dict) or entry.get("settings_fp") != planned.settings_fp:
        return None
    cursor = entry.get("cursor")
    return cursor if isinstance(cursor, str) else None


def record_result(
    state: dict[str, Any], planned: PlannedSource, result: Mapping[str, Any], cursor: str | None
) -> None:
    """Keep *cursor* and how the sync ended for *planned* in *state* (``state.json``)."""
    ledger = state.get(LEDGER_KEY)
    if not isinstance(ledger, dict):
        ledger = state[LEDGER_KEY] = {}
    entry = ledger.get(planned.ledger_id)
    entry = entry if isinstance(entry, dict) else {}
    at = datetime.now(UTC).isoformat(timespec="seconds")
    entry.update(
        cursor=cursor,
        settings_fp=planned.settings_fp,
        last_state=result["state"],
        last_run_at=at,
        last_batches=result["batches"],
        last_message=result["message"],
    )
    if result["state"] == "stored":
        entry["last_stored_at"] = at
    ledger[planned.ledger_id] = entry


def unsynced_sources(root: Path) -> tuple[str, ...]:
    """Configured sources that have never stored a sync into the index at *root*, by name."""
    from repowise.core.repo_config import load_repo_config
    from repowise.core.workspace.state import read_repo_state

    planned = [p for p in plan_sources(load_repo_config(root)) if p.configured]
    if not planned:
        return ()
    ledger = _ledger(read_repo_state(root))

    def stored_once(p: PlannedSource) -> bool:
        entry = ledger.get(p.ledger_id)
        return isinstance(entry, dict) and bool(entry.get("last_stored_at"))

    return tuple(p.name for p in planned if not stored_once(p))
