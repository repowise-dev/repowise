"""Shared helpers for managed AI editor instruction files."""

from __future__ import annotations

from pathlib import Path

from repowise.cli.helpers import get_db_url_for_repo, load_config
from repowise.core.generation.editor_files import (
    AgentsMdGenerator,
    ClaudeMdGenerator,
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
    import yaml  # type: ignore[import-untyped]

    cfg = load_config(repo_path)
    editor_files = dict(cfg.get("editor_files", {}))
    editor_files[key] = enabled
    cfg["editor_files"] = editor_files

    cfg_path = repo_path / ".repowise" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        yaml.dump(cfg, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


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
    cfg = load_config(repo_path)
    return bool(cfg.get("editor_files", {}).get(key, default))


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


async def write_claude_md(repo_path: Path) -> Path | None:
    """Fetch editor data and write .claude/CLAUDE.md."""
    data = await fetch_editor_file_data(repo_path)
    if data is None:
        return None
    return ClaudeMdGenerator().write(repo_path, data)


async def write_agents_md(repo_path: Path) -> Path | None:
    """Fetch editor data and write AGENTS.md."""
    data = await fetch_editor_file_data(repo_path)
    if data is None:
        return None
    return AgentsMdGenerator().write(repo_path, data)
