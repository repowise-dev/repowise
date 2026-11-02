"""The index-data row: are the stored git, health and security results current?

The other store rows say the stores agree with each other; none says the data
in them is complete or was written by the rules this build runs. An index whose
git tables are empty, or whose stamps predate the current git history rules,
health analyzer or security scanner, passed every one of them. This row reads
the same stamps ``repowise update`` reads to decide what to redo, so it names
exactly what the next update will rework. Stamp reads plus ``count(*)`` keep it
cheap enough for ``doctor --quick``.
"""

from __future__ import annotations

from pathlib import Path as _DoctorPath
from typing import Any

from repowise.cli.helpers import run_async

from .._types import DoctorCheck, _check
from .context import CheckContext, open_store

NAME = "Index data"
CHECK_ID = "index-data"
#: No model calls: re-walks git history and re-scores health and security.
FIX = "repowise update --index-only"


def git_indexed(state: dict[str, Any]) -> bool:
    """Whether the stored index walked git history.

    ``init`` writes ``git_tier`` in every mode, so the recorded HEAD is what
    tells a git checkout apart from a plain folder, which never stamps the
    history version and has no git rows to count.
    """
    return bool(state.get("git_tier")) and bool(state.get("last_sync_commit"))


def _stamp(state: dict[str, Any], key: str) -> str:
    stored = state.get(key)
    return "unstamped" if stored is None else f"v{stored}"


def _history_part(state: dict[str, Any]) -> str:
    from repowise.core.ingestion.git_indexer import (
        GIT_HISTORY_BACKFILL_REWALK,
        GIT_HISTORY_VERSION,
        GIT_HISTORY_VERSION_KEY,
    )

    if state.get(GIT_HISTORY_VERSION_KEY) == GIT_HISTORY_BACKFILL_REWALK:
        return "git history awaits a re-walk after attribution backfill"
    return (
        f"git history written by older rules "
        f"({_stamp(state, GIT_HISTORY_VERSION_KEY)}, current v{GIT_HISTORY_VERSION})"
    )


def _rescore_parts(state: dict[str, Any]) -> list[str]:
    from repowise.cli.commands.status_cmd import _rescore_pending
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
    from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION

    labels = {
        "health": (
            f"health scored by an older analyzer "
            f"({_stamp(state, 'health_analyzer_version')}, current v{HEALTH_ANALYZER_VERSION})"
        ),
        "security": (
            f"security findings from an older scanner "
            f"({_stamp(state, 'security_scanner_version')}, current v{SECURITY_SCANNER_VERSION})"
        ),
    }
    return [labels[part] for part in _rescore_pending(state)]


def stale_parts(state: dict[str, Any]) -> list[str]:
    """What the next ``repowise update`` redoes because a stamp is out of date.

    Reuses the predicates update itself runs (``git_history_stale``,
    ``health_analyzer_changed``, ``security_scanner_changed``), so doctor and
    update cannot disagree about what is stale.
    """
    from repowise.core.ingestion.git_indexer import git_history_stale

    history = [_history_part(state)] if git_indexed(state) and git_history_stale(state) else []
    return history + _rescore_parts(state)


def index_data_row(parts: list[str], git_rows: int | None, git_tier: str | None) -> DoctorCheck:
    """The row for *parts* stale and *git_rows* stored (None: git not indexed).

    A WARN, never a FAIL: the index still answers, from data the next update
    replaces, so a healthy-but-stale index keeps doctor's exit code.
    """
    problems = list(parts)
    if git_rows == 0:
        problems.append(f"git tables are empty though the {git_tier} git tier was indexed")
    if problems:
        return _check(
            NAME,
            True,
            f"{'; '.join(problems)}. Run: {FIX}",
            check_id=CHECK_ID,
            warn=True,
            fix=FIX,
        )
    detail = "current" if git_rows is None else f"current; git tables hold {git_rows} rows"
    return _check(NAME, True, detail, check_id=CHECK_ID)


async def _git_row_count(repo_path: _DoctorPath) -> int | None:
    """Rows this repository holds across the git tables; None if it is not in the store."""
    from sqlalchemy import func, select

    from repowise.core.persistence import get_repository_by_path, get_session
    from repowise.core.persistence.models import (
        GitCommit,
        GitCommitFile,
        GitFunctionBlame,
        GitMetadata,
    )

    engine, sf = await open_store(repo_path)
    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            total = 0
            for model in (GitMetadata, GitCommit, GitCommitFile, GitFunctionBlame):
                total += await session.scalar(
                    select(func.count()).select_from(model).where(model.repository_id == repo.id)
                ) or 0
            return total
    finally:
        await engine.dispose()


def index_data_check(ctx: CheckContext) -> DoctorCheck:
    """The index-data row for *ctx*, whose database and state.json both loaded."""
    try:
        git_rows = run_async(_git_row_count(ctx.repo_path)) if git_indexed(ctx.state) else None
        return index_data_row(stale_parts(ctx.state), git_rows, ctx.state.get("git_tier"))
    except Exception as exc:
        return _check(
            NAME,
            True,
            f"Could not check: {type(exc).__name__}: {exc}",
            check_id=CHECK_ID,
            warn=True,
        )
