"""Helpers shared by the ``repowise workspace`` subcommands."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.markup import escape

from repowise.cli.helpers import (
    console,
    db_configured,
    find_workspace_root,
    get_db_url_for_repo,
    get_repowise_dir,
    reconcile_schema_best_effort,
    run_async,
)

if TYPE_CHECKING:
    from repowise.core.workspace.config import WorkspaceConfig


def _require_workspace(start: Path | None = None) -> tuple[Path, WorkspaceConfig]:  # type: ignore[name-defined]
    """Load the workspace config or abort with a helpful message.

    Returns ``(ws_root, ws_config)``.
    """
    from repowise.core.workspace.config import WorkspaceConfig

    ws_root = find_workspace_root(start)
    if ws_root is None:
        raise click.ClickException(
            "No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' to create a workspace."
        )
    ws_config = WorkspaceConfig.load(ws_root)
    return ws_root, ws_config


def _format_age(generated_at: str | None) -> str:
    """Render an ISO timestamp as a short age, or say it is missing."""
    from datetime import datetime

    if not generated_at:
        return "never built"
    try:
        stamped = datetime.fromisoformat(generated_at)
    except ValueError:
        return f"stamped {generated_at}"
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - stamped).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    if seconds < 172800:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


def _report_artifact_provenance(
    ws_root: Path,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
    generated_at: str | None,
    artifact_repos: list[str],
    walked_repos: Iterable[str] = (),
) -> None:
    """Print how old the artifact is, and flag config/artifact disagreement.

    Commands that read a persisted artifact otherwise present it as the state
    of the tree right now. When the config and the artifact list different
    repos, that gap is itself the finding — it means the artifact describes a
    workspace that no longer exists, or that the config lost a repo.

    *walked_repos* are the repos the last extraction read (the graph's
    ``repo_provenance``). One of those missing from *artifact_repos* yielded no
    contracts; re-running the update would change nothing, so it is not told to.
    """
    console.print(f"[dim]Read from .repowise-workspace/, built {_format_age(generated_at)}.[/dim]")

    configured = {e.alias for e in ws_config.repos}
    covered = set(artifact_repos)
    missing_from_config = sorted(covered - configured)
    missing_from_artifact = sorted(configured - covered)
    if missing_from_config:
        console.print(
            f"[yellow]![/yellow] Covers {', '.join(missing_from_config)}, which "
            f"{'is' if len(missing_from_config) == 1 else 'are'} no longer in "
            f"{ws_root.name}/.repowise-workspace.yaml."
        )
    walked = set(walked_repos)
    no_contracts = [r for r in missing_from_artifact if r in walked]
    never_read = [r for r in missing_from_artifact if r not in walked]
    if no_contracts:
        console.print(
            f"[yellow]![/yellow] {', '.join(no_contracts)} "
            f"{'was' if len(no_contracts) == 1 else 'were'} read but yielded no "
            "contracts, so the graph has nothing to place for "
            f"{'it' if len(no_contracts) == 1 else 'them'}."
        )
    if never_read:
        console.print(
            f"[yellow]![/yellow] {', '.join(never_read)} "
            f"{'is' if len(never_read) == 1 else 'are'} configured but "
            "absent from this artifact; re-run 'repowise update --workspace'."
        )


def _query_store_counts(repo_path: Path) -> tuple[int, int] | None:
    """Return ``(file_count, symbol_count)`` from the store configured for *repo_path*.

    ``None`` means no store could answer for this repository: there is no
    repo-local ``wiki.db`` and no database is configured, or the configured
    store holds no repository row for this path. A returned ``(0, 0)`` means a
    store answered and holds nothing for it, which under a configured database
    still means the repository is indexed. Collapsing the two would report a
    repository the store has never seen as indexed with a zero count.

    With ``REPOWISE_DB_URL`` (or ``REPOWISE_DATABASE_URL``) set the local file is
    not consulted at all: a repository indexed into a shared store has no file
    on disk, and a repo-local ``wiki.db`` left behind from an earlier run is not
    the store the rest of the app reads. A repository row for this path is the
    index.

    This is the workspace's own query, beside ``status_cmd._query_repo_counts``,
    because that one reports ``(0, 0)`` for all three outcomes: ``status`` prints
    the same empty table either way, while a workspace listing has to say which
    repos it counts as indexed. The counts themselves come from the same
    ``count_files``/``count_symbols`` the status surfaces use.
    """
    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists() and not db_configured():
        return None

    async def _query() -> tuple[int, int] | None:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_repository_by_path,
            get_session,
        )
        from repowise.core.persistence.crud import count_files, count_symbols

        url = get_db_url_for_repo(repo_path)
        # Without this a store one repowise older reports 0 files: the
        # `no such column` is swallowed by the except below.
        await reconcile_schema_best_effort(url)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        try:
            async with get_session(sf) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return None
                return (
                    await count_files(session, repo.id),
                    await count_symbols(session, repo.id),
                )
        finally:
            await engine.dispose()

    try:
        return run_async(_query())
    except Exception:
        return None


def _print_evidence(evidence: dict[str, Any] | None) -> None:
    """State how much evidence the graph rests on, before any figure built on it.

    *evidence* is :meth:`GraphEvidence.to_dict`. Measured gets one quiet line;
    partial and insufficient list every reason, since each names what is unseen.
    """
    if not evidence:
        return
    state = evidence.get("state")
    if state == "measured":
        console.print(
            f"[dim]Evidence: measured ({evidence.get('runtime_edges', 0)} runtime and "
            f"{evidence.get('build_edges', 0)} build edge(s) across "
            f"{evidence.get('services', 0)} service(s)).[/dim]"
        )
        return
    label = "Not determined" if state == "insufficient" else "Partial evidence"
    console.print(f"[yellow]{label}:[/yellow]")
    for reason in evidence.get("reasons", []):
        console.print(f"  [yellow]-[/yellow] {escape(reason.get('message', ''))}")
