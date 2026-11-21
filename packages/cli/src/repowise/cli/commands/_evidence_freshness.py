"""Read how fresh the stored coverage and runtime evidence are, for CLI consumers.

``doctor`` rows, the ``status --format json`` block and the ``health`` connect
line all read :func:`stored_freshness`, so they agree on what is stored and
what is stale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.helpers import repo_index_session, run_async
from repowise.core.evidence.setup import COVERAGE_SETUP, EVIDENCE_SETUP


def stored_freshness(repo_path: Path) -> dict[str, Any] | None:
    """Coverage and runtime evidence against HEAD on disk
    (``evidence.status.load_evidence_freshness``); ``None`` without a
    readable index. Raises :class:`~repowise.cli.helpers.StaleIndexError` for
    a store an older repowise wrote."""
    from repowise.core.evidence.status import load_evidence_freshness
    from repowise.core.workspace.update.state import get_head_commit

    async def _read() -> dict[str, Any] | None:
        async with repo_index_session(repo_path) as opened:
            if opened is None:
                return None
            session, repo_id = opened
            return await load_evidence_freshness(session, repo_id, get_head_commit(repo_path))

    return run_async(_read())


#: The command that collects each kind: prints the exact run and ingest lines.
SETUP = {"coverage": COVERAGE_SETUP, "runtime": EVIDENCE_SETUP}


def _days(n: int | None) -> str:
    if n is None:
        return "age unknown"
    return "today" if n == 0 else f"{n} day{'s' if n != 1 else ''} ago"


def freshness_words(entry: dict[str, Any], reference: str | None) -> str:
    """One kind's freshness in words: ``measured at HEAD (abc12345), 3 days ago``."""
    if not entry["present"]:
        return "none stored"
    commit = (entry["commit_sha"] or "")[:8]
    age = _days(entry["age_days"])
    if entry["freshness"] == "current":
        return f"measured at HEAD ({commit}), {age}"
    if entry["freshness"] == "stale":
        return f"stale: measured at {commit}, {age}; HEAD is {(reference or '')[:8]}"
    where = f"at {commit}" if commit else "at an unknown commit"
    return f"measured {where}, {age}; freshness unknown"
