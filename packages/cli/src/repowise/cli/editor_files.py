"""Shared helpers for managed AI editor instruction files."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.helpers import get_db_url_for_repo, load_config, load_config_for_write
from repowise.core.generation.editor_files import (
    AgentsMdGenerator,
    EditorFileData,
    EditorFileDataFetcher,
)
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.crud import get_repository_by_path


def set_editor_file_enabled(repo_path: Path, key: str, enabled: bool) -> None:
    """Persist an editor_files.<key> preference in .repowise/config.yaml."""
    from repowise.core.repo_config import save_repo_config

    cfg = load_config_for_write(repo_path)
    editor_files = dict(cfg.get("editor_files") or {})
    editor_files[key] = enabled
    cfg["editor_files"] = editor_files
    save_repo_config(repo_path, cfg)


def should_generate_editor_file(
    repo_path: Path,
    key: str,
    *,
    default: bool = True,
    override: bool | None = None,
) -> bool:
    """Resolve and persist an optional per-command editor file override."""
    if override is not None:
        set_editor_file_enabled(repo_path, key, override)
        return override
    from repowise.core.settings.model import flag

    editor_files = load_config(repo_path).get("editor_files")
    return flag(editor_files.get(key) if isinstance(editor_files, dict) else None, default)


async def fetch_editor_file_data(repo_path: Path) -> EditorFileData | None:
    """Fetch indexed repo data for editor-file generation."""
    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            fetcher = EditorFileDataFetcher(session, repo.id, repo_path)
            return await fetcher.fetch()
    finally:
        await engine.dispose()


async def write_agents_md(repo_path: Path) -> Path | None:
    """Fetch editor data and write AGENTS.md."""
    data = await fetch_editor_file_data(repo_path)
    if data is None:
        return None
    return AgentsMdGenerator().write(repo_path, data)
