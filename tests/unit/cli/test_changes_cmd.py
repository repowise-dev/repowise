"""`repowise changes list` and `show` against a real local index."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.changes_cmd import changes_group
from repowise.cli.helpers import run_async

HEAD = "c" * 40


async def _store(repo_path: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_changes

    (repo_path / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            await upsert_changes(
                session,
                repo.id,
                [
                    {
                        "change_id": "pr-5",
                        "identity_kind": "pr",
                        "identity_value": "5",
                        "shape": "squash",
                        "forge": "github",
                        "forge_ref": "#5",
                        "title": "fix: crash (#5)",
                        "merged_at": datetime(2026, 3, 1, tzinfo=UTC),
                        "head_sha": HEAD,
                        "commit_shas_json": json.dumps([HEAD]),
                        "files_json": json.dumps([{"path": "a.py", "added": 1, "deleted": 2}]),
                        "files_changed": 1,
                        "lines_added": 1,
                        "lines_deleted": 2,
                        "category": "fix",
                        "metadata_source": "git",
                    }
                ],
            )
    finally:
        await engine.dispose()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    run_async(_store(root))
    return root


def test_list_and_show_formats(tmp_path):
    root = _repo(tmp_path)
    runner = CliRunner()

    listed = runner.invoke(changes_group, ["list", "--path", str(root), "--format", "json"])
    assert listed.exit_code == 0, listed.output
    data = json.loads(listed.output)
    assert data["total"] == 1 and data["changes"][0]["id"] == "pr-5"
    assert "files" not in data["changes"][0]

    table = runner.invoke(changes_group, ["list", "--path", str(root)])
    assert table.exit_code == 0 and "#5" in table.output

    shown = json.loads(
        runner.invoke(changes_group, ["show", "pr-5", "--path", str(root), "--format", "json"]).output
    )
    assert shown["schema_version"] == 1 and shown["files"][0]["path"] == "a.py"

    statement = json.loads(
        runner.invoke(
            changes_group, ["show", HEAD[:8], "--path", str(root), "--format", "in-toto"]
        ).output
    )
    assert statement["_type"] == "https://in-toto.io/Statement/v1"
    assert statement["subject"] == [{"name": HEAD, "digest": {"gitCommit": HEAD}}]
    assert statement["predicateType"] == "https://repowise.dev/ChangeRecord/v1"

    missing = runner.invoke(changes_group, ["show", "pr-9", "--path", str(root)])
    assert missing.exit_code == 1


def test_no_index_exits_non_zero(tmp_path):
    root = tmp_path / "bare"
    (root / ".git").mkdir(parents=True)
    result = CliRunner().invoke(changes_group, ["list", "--path", str(root)])
    assert result.exit_code == 1 and "No index" in result.output
