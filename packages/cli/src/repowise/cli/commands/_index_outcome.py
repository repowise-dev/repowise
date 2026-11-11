"""Steps every outcome of ``repowise init`` and ``repowise update`` shares.

The telemetry outcome both commands attach to ``command_run``, and the stored
read views both refresh once every store is final. A workspace update is the
exception for the views: a member whose fingerprint has not moved since its
last refresh skips them until it moves or the UTC day changes
(``update_cmd/member_refresh.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from repowise.cli.helpers import console, run_async

log = structlog.get_logger(__name__)


def record_index_outcome(
    *,
    index_only: bool,
    provider: Any,
    docs_mode: str,
    pages: list | None,
    buckets: dict[str, int] | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    """Attach an anonymous index-shape outcome to the ``command_run`` event.

    Coarse buckets + enums only: each ``buckets`` count is reported as
    ``<name>_bucket``, ``fields`` as given. Provider and model are named only
    for a docs run. No repo names, paths, or exact counts. Best-effort: never
    let telemetry break the command's happy path.
    """
    try:
        from repowise.cli.platform import telemetry

        outcome: dict[str, Any] = {
            "outcome": "success",
            "index_only": bool(index_only),
            "docs_mode": docs_mode,
            **(fields or {}),
        }
        for name, count in (buckets or {}).items():
            outcome[f"{name}_bucket"] = telemetry.bucket_count(count)
        if not index_only and provider is not None:
            outcome["provider"] = getattr(provider, "provider_name", None)
            outcome["model"] = getattr(provider, "model_name", None)
        if pages:
            outcome["pages_bucket"] = telemetry.bucket_count(len(pages))
        telemetry.add_command_outcome(**{k: v for k, v in outcome.items() if v is not None})
    except Exception:
        return


def refresh_read_snapshots(repo_path: Any) -> bool:
    """Store the Fix first queue and the next-actions view for the next reader.

    A named step at every outcome of ``repowise update`` and at the end of
    ``repowise init`` (a workspace update skips unchanged members, see the
    module docstring), once every store is final, so ``repowise next``, the
    overview and the dashboard read one row instead of rebuilding in each new
    process. A view no write has invalidated since is left as it is, so the
    "already up to date" path costs one primary-key read per view.
    Best-effort: without a snapshot a reader builds live, as before. A
    snapshot that is not written is named with its error, here rather than in
    the core log, which an update keeps at ERROR. Returns whether the views
    were written: ``False`` on a failure and when the store could not be opened
    or holds no repository row, so a caller that skips unchanged repos retries.
    """
    from repowise.cli.helpers import repo_index_session
    from repowise.core.persistence.crud.analysis.actions import write_read_snapshots

    async def _run() -> list[tuple[str, Exception]] | None:
        async with repo_index_session(Path(repo_path)) as opened:
            return None if opened is None else await write_read_snapshots(*opened)

    try:
        failed = run_async(_run())
    except Exception as exc:
        failed = [("read snapshots", exc)]
    if failed is None:  # no store to write to; silent, as before
        return False
    try:
        for name, exc in failed:
            log.debug("read_snapshot_not_written", snapshot=name, error=repr(exc))
            console.print(f"[yellow]Snapshot not written: {_short_error(name, exc)}[/yellow]")
    except Exception:  # reporting must never fail the update
        log.debug("read_snapshot_report_failed", exc_info=True)
    return not failed


def _short_error(name: str, exc: BaseException) -> str:
    """``<snapshot>: <ErrorType>: <first line>``, cut to one line and escaped
    for Rich: an error text can hold markup brackets or a whole traceback."""
    from rich.markup import escape

    first = (str(exc).strip().splitlines() or [""])[0]
    if len(first) > 160:
        first = first[:159] + "…"
    return escape(f"{name}: {type(exc).__name__}" + (f": {first}" if first else ""))
