"""The exclude patterns every health read narrows its files by."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ....models import Repository


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

    patterns: list[str] = []
    seen: set[str] = set()

    def _add(values: Any) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, str) and value not in seen:
                seen.add(value)
                patterns.append(value)

    try:
        settings = json.loads(getattr(repo, "settings_json", "") or "{}")
        if isinstance(settings, dict):
            _add(settings.get("exclude_patterns"))
    except (TypeError, ValueError):
        pass

    try:
        from repowise.core.repo_config import load_repo_config

        cfg = load_repo_config(Path(repo_path or repo.local_path))
        if isinstance(cfg, dict):
            _add(cfg.get("exclude_patterns"))
    except Exception:
        pass

    if not patterns:
        return None

    import pathspec

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _filter_excluded_paths(rows: list[Any], spec: Any) -> list[Any]:
    if spec is None:
        return rows
    return [row for row in rows if not spec.match_file(getattr(row, "file_path", ""))]
