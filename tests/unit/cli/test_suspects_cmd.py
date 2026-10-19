"""`repowise suspects` against a real git repo and local index."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.suspects_cmd import suspects_command
from repowise.cli.helpers import run_async


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
        env={"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "a",
             "GIT_COMMITTER_EMAIL": "a@x", "PATH": __import__("os").environ["PATH"]},
    ).stdout.strip()


def _commit(root: Path, path: str, body: str, message: str) -> str:
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(body, encoding="utf-8")
    _git(root, "add", path)
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


async def _store(root: Path, changes: list[tuple[str, str, str, datetime]]) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_changes

    (root / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(root / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(root))
            await upsert_changes(
                session,
                repo.id,
                [
                    {
                        "change_id": f"commit-{sha}",
                        "identity_kind": "commit",
                        "identity_value": sha,
                        "shape": "commit",
                        "forge": "generic",
                        "title": title,
                        "merged_at": at,
                        "head_sha": sha,
                        "commit_shas_json": json.dumps([sha]),
                        "files_json": json.dumps([{"path": path, "added": 1, "deleted": 0}]),
                        "files_changed": 1,
                        "lines_added": 1,
                        "lines_deleted": 0,
                        "category": "feature",
                        "metadata_source": "git",
                    }
                    for sha, title, path, at in changes
                ],
            )
    finally:
        await engine.dispose()


def test_ranks_the_change_the_symptom_names(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    a = _commit(root, "billing/invoice.py", "def total(lines):\n    return 0\n", "Round invoice totals")
    b = _commit(root, "docs/readme.md", "hello\n", "Reword the readme")
    run_async(
        _store(
            root,
            [
                (a, "Round invoice totals", "billing/invoice.py", datetime(2026, 3, 1, tzinfo=UTC)),
                (b, "Reword the readme", "docs/readme.md", datetime(2026, 3, 2, tzinfo=UTC)),
            ],
        )
    )
    out = CliRunner().invoke(
        suspects_command,
        ["--path", str(root), "--symptom", "invoice totals are off in billing/invoice.py",
         "--until", "2026-03-03", "--format", "json"],
    )
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["candidates"] == 2
    top = data["suspects"][0]
    assert top["id"] == f"commit-{a}" and top["rank"] == 1
    assert {"text", "recency"} <= set(top["signals"])
    assert data["symptom_files"] == ["billing/invoice.py"]
    assert "not a cause" in data["note"]


def test_needs_a_symptom(tmp_path):
    out = CliRunner().invoke(suspects_command, ["--path", str(tmp_path)])
    assert out.exit_code != 0 and "Give a symptom" in out.output
