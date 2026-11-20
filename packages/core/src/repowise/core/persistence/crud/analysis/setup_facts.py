"""Gather :class:`SetupFacts`: a few counts from the index, the rest from the checkout.

Every read is its own: one that fails is named in ``unavailable`` and costs
only its step, never the list. The checkout reads (git, the report and
profile walks, the ledger, the settings) are blocking and run in one worker
thread.

The rewrite hook lives in the agent's own settings, which only the CLI reads;
a caller that knows its state passes it, and without it the distill step is
reported unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.actions.setup import (
    NAMED_FILES,
    SETUP_STATE_ID,
    SYNCED_COMMITS_UNLOCK,
    FoundFile,
    SetupFacts,
)

from ...models import CoverageFile, EvidenceRun, GitCommit, Repository
from .actions import set_action_state

logger = logging.getLogger(__name__)



async def load_setup_facts(
    session: AsyncSession,
    repo_id: str,
    root: str | Path,
    *,
    rewrite_hook: bool | None = None,
) -> SetupFacts:
    """The live setup facts for *repo_id*, whose checkout is *root*."""
    root = Path(root)
    values: dict[str, Any] = {}
    unavailable: dict[str, str] = {}

    async def read(name: str, reader: Any) -> None:
        try:
            async with session.begin_nested():
                values.update(await reader())
        except Exception as exc:  # one fact must not cost the list
            logger.warning("setup: %s unavailable: %s", name, exc)
            unavailable[name] = "Could not be read from this index."

    await read("coverage", lambda: _coverage_ingested(session, repo_id))
    await read("runtime", lambda: _runtime_stored(session, repo_id))
    await read("decisions", lambda: _background_records(session, repo_id))
    await read("history", lambda: _synced_commits(session, repo_id))
    local, missing = await asyncio.to_thread(_local_facts, root, values)
    unavailable.update(missing)
    from repowise.core.setup_state import read_setup_state

    return SetupFacts(
        root=str(root),
        rewrite_hook=rewrite_hook,
        completed=bool(read_setup_state(root).get("completed")),
        unavailable=unavailable,
        **values,
        **local,
    )


async def dismiss_setup(session: AsyncSession, repo_id: str) -> None:
    """Record that the person declined the finish-setup pass."""
    await set_action_state(session, repo_id, SETUP_STATE_ID, state="dismissed")


# -- the index ----------------------------------------------------------------


async def _coverage_ingested(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    row = await session.execute(
        select(CoverageFile.id).where(CoverageFile.repository_id == repo_id).limit(1)
    )
    return {"coverage_ingested": row.first() is not None}


async def _runtime_stored(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    row = await session.execute(
        select(EvidenceRun.id).where(EvidenceRun.repository_id == repo_id).limit(1)
    )
    return {"runtime_stored": row.first() is not None}


async def _background_records(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    from ..decision_discovery import background_records

    return {"background_records": len(await background_records(session, repo_id))}


async def _synced_commits(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """Indexed commits made after the index was first built: usage, not age."""
    created = (
        await session.execute(select(Repository.created_at).where(Repository.id == repo_id))
    ).scalar_one_or_none()
    if created is None:
        return {"synced_commits": 0}
    count = (
        await session.execute(
            select(func.count())
            .select_from(GitCommit)
            .where(GitCommit.repository_id == repo_id, GitCommit.committed_at > created)
        )
    ).scalar_one()
    return {"synced_commits": int(count)}


# -- the checkout and the machine ---------------------------------------------------


def _local_facts(root: Path, known: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    out: dict[str, Any] = {}
    missing: dict[str, str] = {}
    readers = (
        ("sync_hook", lambda: _sync(root)),
        ("coverage", lambda: _reports(root) if known.get("coverage_ingested") is False else {}),
        ("runtime", lambda: _evidence(root) if known.get("runtime_stored") is False else {}),
        ("ledger", lambda: _usage(root)),
        # A failed history read leaves suggestions unread; settings_suggested
        # then reports the history as unavailable.
        ("settings", lambda: _settings(root, known.get("synced_commits") or 0)),
        ("model", lambda: _model(root)),
    )
    for name, reader in readers:
        try:
            out.update(reader())
        except Exception as exc:  # one fact must not cost the list
            logger.warning("setup: %s unavailable: %s", name, exc)
            missing[name] = "Could not be read on this machine."
    return out, missing


def _sync(root: Path) -> dict[str, Any]:
    from repowise.core.git_head import read_head_commit
    from repowise.core.git_hooks import sync_hook_installed
    from repowise.core.workspace.update.state import count_commits_between, read_state_commit

    installed = sync_hook_installed(root)
    synced, head = read_state_commit(root), read_head_commit(str(root))
    behind = None
    if synced and head:
        behind = 0 if synced == head else count_commits_between(root, synced, head)
    return {"sync_hook": installed, "unsynced_commits": behind}


def _found(
    root: Path, paths: list[Path], kinds: list[str], cap: int | None = NAMED_FILES
) -> tuple[FoundFile, ...]:
    now = time.time()
    found = []
    for path, kind in zip(paths[:cap], kinds, strict=False):
        try:
            age = max(0, int((now - path.stat().st_mtime) // 86_400))
        except OSError:
            continue
        found.append(FoundFile(path.relative_to(root).as_posix(), age, kind))
    return tuple(found)


def _reports(root: Path) -> dict[str, Any]:
    from repowise.core.analysis.health.coverage import CoverageConfig, discover_artifacts
    from repowise.core.repo_config import load_repo_config

    cfg = CoverageConfig.from_repo_config(load_repo_config(root))
    if not cfg.auto_discover and not cfg.artifacts:
        return {"coverage_reports": ()}
    # Every report, not the first few: the step counts and ingests them all.
    # The discovery walk is bounded on its own.
    paths = discover_artifacts(root, globs=cfg.artifacts or None)
    return {"coverage_reports": _found(root, paths, [""] * len(paths), cap=None)}


def _evidence(root: Path) -> dict[str, Any]:
    from repowise.core.evidence.found import discover_evidence

    found = discover_evidence(root)
    return {"evidence_files": _found(root, [p for p, _ in found], [k for _, k in found])}


def _usage(root: Path) -> dict[str, Any]:
    from repowise.core.agents.identity import CLAUDE_CODE
    from repowise.core.savings.service import load_agent_usage

    usage = load_agent_usage(root)
    if usage is None:
        # No ledger yet is no calls yet; an unreadable one is a read error
        # the service already folds into None as well, so both read as zero.
        return {"agent_calls": 0, "claude_code_calls": 0, "first_call_at": None}
    claude = usage.get(CLAUDE_CODE.slug)
    return {
        "agent_calls": sum(u.calls for u in usage.values()),
        "claude_code_calls": claude.calls if claude else 0,
        "first_call_at": min((u.first for u in usage.values()), default=None),
    }


def _settings(root: Path, synced: int) -> dict[str, Any]:
    from repowise.core.repo_config import load_repo_config
    from repowise.core.settings import resolve

    out: dict[str, Any] = {
        "always_load": bool(resolve("mcp.always_load", load_repo_config(root)).value)
    }
    # Read only once unlocked: the suggestions read several stores.
    if synced >= SYNCED_COMMITS_UNLOCK:
        from repowise.core.settings.suggest import suggestions

        out["suggested_settings"] = tuple(s.key for s in suggestions(root))
    return out


def _model(root: Path) -> dict[str, Any]:
    from repowise.core.index_scope import load_index_scope, model_command, upgrade_command
    from repowise.core.providers.llm.subscription import keyed_model_source

    scope = load_index_scope(root)
    # The same test the keyless upgrade lines use: Index only, or pages built
    # from the index alone.
    keyless = upgrade_command(scope) is not None or (
        isinstance(scope, dict) and scope.get("content_provenance") == "template"
    )
    source = keyed_model_source(root) if keyless else None
    if source is None:
        return {"model_source": None}
    provider, line = source
    return {"model_source": (provider, line, f"{model_command(scope)} --provider {provider}")}


def with_current_call(facts: SetupFacts) -> SetupFacts:
    """*facts* as an agent's call sees them: that call is a call, ledger or not."""
    return replace(facts, agent_calls=max(facts.agent_calls or 0, 1))


__all__ = ["dismiss_setup", "load_setup_facts", "with_current_call"]
