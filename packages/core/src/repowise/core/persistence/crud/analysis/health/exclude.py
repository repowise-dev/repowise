"""The exclude patterns every health read narrows its files by."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ....models import Repository

logger = logging.getLogger(__name__)


async def health_exclude_spec(
    session: AsyncSession, repository_id: str, repo_path: Path | str | None = None
) -> Any:
    """The exclude patterns every health surface narrows its files by.

    The repository's settings and ``exclude_patterns`` only. Not the gitignore
    stack: ingestion keeps a file git tracks even when an ignore rule matches
    it, so a stored row under such a rule is a real file of the repository.
    ``repo_path`` is where to read the config when the caller serves a
    checkout other than the stored ``local_path``.
    """
    repo = await session.get(Repository, repository_id)
    if repo is None:
        return None

    from repowise.core.repo_config import resolve_file_settings

    try:
        stored = json.loads(getattr(repo, "settings_json", "") or "{}")
    except (TypeError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    # config.yaml first; a legacy settings copy only while the file lacks the key.
    try:
        values = resolve_file_settings(Path(repo_path or repo.local_path), stored)
    except Exception:
        logger.warning(
            "Could not read config.yaml exclude_patterns for %s; using saved settings",
            repo_path or repo.local_path,
            exc_info=True,
        )
        values = stored
    raw = values.get("exclude_patterns")
    if not isinstance(raw, list):
        raw = []
    patterns = list(dict.fromkeys(v for v in raw if isinstance(v, str)))

    if not patterns:
        return None

    import pathspec

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _filter_excluded_paths(rows: list[Any], spec: Any) -> list[Any]:
    if spec is None:
        return rows
    return [row for row in rows if not spec.match_file(getattr(row, "file_path", ""))]
