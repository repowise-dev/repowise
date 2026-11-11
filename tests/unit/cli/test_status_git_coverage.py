"""``repowise status`` reports Git history coverage from the live index."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands import status_cmd


def _seed_git_metadata(repo_path: Path, history_counts: tuple[int, ...] = ()) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import GitMetadata, _new_uuid

    db_path = repo_path / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True)

    async def seed() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        await init_db(engine)
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await upsert_repository(
                    session,
                    name=repo_path.name,
                    local_path=str(repo_path),
                )
                for index, commit_count in enumerate(history_counts):
                    session.add(
                        GitMetadata(
                            id=_new_uuid(),
                            repository_id=repo.id,
                            file_path=f"src/file_{index}.py",
                            commit_count_total=commit_count,
                        )
                    )
        finally:
            await engine.dispose()

    asyncio.run(seed())


def _prepare_status(monkeypatch) -> None:
    async def pages(_path: Path) -> tuple[dict[str, int], int]:
        return {}, 0

    monkeypatch.setattr(status_cmd, "_query_pages", pages)
    monkeypatch.setattr(
        status_cmd,
        "_mapping_report",
        lambda _path: {"mapping_valid": True, "reason": None},
    )
    monkeypatch.setattr(status_cmd, "_query_health", lambda _path: None)
    monkeypatch.setattr(status_cmd, "_query_health_line", lambda _path: None)
    monkeypatch.setattr(status_cmd, "_load_actions", lambda _path: None)


def _write_state(repo_path: Path) -> None:
    (repo_path / ".repowise" / "state.json").write_text(
        json.dumps(
            {
                "last_sync_commit": "abc",
                "git_history_coverage": {
                    "files_with_history": 36,
                    "files_eligible": 36,
                },
                "index_scope": {"git_commit_cap": 5000},
            }
        ),
        encoding="utf-8",
    )


def test_status_uses_live_git_rows_and_suggests_index_only(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_git_metadata(tmp_path)
    _write_state(tmp_path)
    _prepare_status(monkeypatch)

    result = CliRunner().invoke(
        status_cmd.status_command,
        [str(tmp_path), "--no-workspace"],
        terminal_width=200,
    )

    assert result.exit_code == 0, result.output
    assert "0/36 files (last 5,000 commits)" in result.output
    assert "repowise update --index-only" in result.output


def test_status_json_keeps_coverage_keys_and_reports_live_count(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_git_metadata(tmp_path)
    _write_state(tmp_path)
    _prepare_status(monkeypatch)

    result = CliRunner().invoke(
        status_cmd.status_command,
        [str(tmp_path), "--no-workspace", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    coverage = data["index_scope"]["git_history_coverage"]
    assert coverage == {"files_with_history": 0, "files_eligible": 36}


def test_status_counts_only_git_metadata_rows_with_commits(tmp_path: Path, monkeypatch) -> None:
    _seed_git_metadata(tmp_path, history_counts=(4, 0))
    _write_state(tmp_path)
    _prepare_status(monkeypatch)

    result = CliRunner().invoke(
        status_cmd.status_command,
        [str(tmp_path), "--no-workspace"],
        terminal_width=200,
    )

    assert result.exit_code == 0, result.output
    assert "1/36 files (last 5,000 commits)" in result.output


def test_status_marks_stored_git_coverage_when_live_query_fails(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_git_metadata(tmp_path)
    _write_state(tmp_path)
    _prepare_status(monkeypatch)
    monkeypatch.setattr(status_cmd, "_query_git_history_file_count", lambda _path: None)

    result = CliRunner().invoke(
        status_cmd.status_command,
        [str(tmp_path), "--no-workspace"],
        terminal_width=200,
    )

    assert result.exit_code == 0, result.output
    assert "36/36 files (last 5,000 commits)" in result.output
    assert "live coverage unavailable; showing stored value" in result.output
