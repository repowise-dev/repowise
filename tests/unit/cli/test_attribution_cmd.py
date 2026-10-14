"""`repowise attribution status` and `purge` against a real local index."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.attribution_cmd import attribution_group
from repowise.cli.helpers import run_async


async def _store(repo_path: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_git_commits_bulk, upsert_git_metadata

    (repo_path / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            rows = [
                {
                    "sha": f"{label[0]}{i:09d}",
                    "author_name": "A",
                    "author_email": "a@example.com",
                    "committed_at": datetime.fromtimestamp(1000 + i, tz=UTC),
                    "subject": "x",
                    "attribution": label,
                    "agent_name": "claude" if label == "agent" else None,
                }
                for i, label in enumerate(["agent"] * 3 + ["human_attested"] + ["unknown"] * 6)
            ]
            await upsert_git_commits_bulk(session, repo.id, rows)
            await upsert_git_metadata(
                session,
                repository_id=repo.id,
                file_path="a.py",
                commit_count_total=10,
                agent_commit_count=3,
                attribution_known_count=4,
                agent_authored_pct=0.75,
                agent_line_count=12,
            )
    finally:
        await engine.dispose()


async def _labels(repo_path: Path) -> tuple[list, list]:
    from sqlalchemy import select

    from repowise.core.persistence import create_engine
    from repowise.core.persistence.models import GitCommit, GitMetadata

    engine = create_engine(f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}")
    try:
        async with engine.connect() as conn:
            commits = (await conn.execute(select(GitCommit.agent_name, GitCommit.attribution))).all()
            meta = (
                await conn.execute(
                    select(
                        GitMetadata.agent_commit_count,
                        GitMetadata.agent_authored_pct,
                        GitMetadata.agent_line_count,
                    )
                )
            ).all()
            return [tuple(r) for r in commits], [tuple(r) for r in meta]
    finally:
        await engine.dispose()


@pytest.fixture
def indexed_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    run_async(_store(root))
    return root


def test_status_reports_settings_layers_and_coverage(indexed_repo: Path) -> None:
    (indexed_repo / ".repowise" / "config.yaml").write_text(
        "attribution:\n  capture: local\n  agents: [codex, nope]\n", encoding="utf-8"
    )
    result = CliRunner().invoke(attribution_group, ["status", str(indexed_repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["effective"] == {"enabled": True, "capture": "off", "agents": []}
    assert data["repo"]["capture"] == "local"
    assert data["repo"]["agents"] == ["codex"]
    assert data["user"]["capture"] is None
    assert any("nope" in e for e in data["errors"])
    assert data["commits"] == {"agent": 3, "human_attested": 1, "unknown": 6}
    assert data["coverage"] == pytest.approx(0.4)
    assert data["agent_share_of_covered"] == pytest.approx(0.75)


def test_status_text_when_disabled(indexed_repo: Path) -> None:
    (indexed_repo / ".repowise" / "config.yaml").write_text(
        "attribution:\n  enabled: false\n", encoding="utf-8"
    )
    result = CliRunner().invoke(attribution_group, ["status", str(indexed_repo)])
    assert result.exit_code == 0, result.output
    assert "Nothing about AI is stored or shown" in result.output
    assert "commits" not in result.output


def test_purge_asks_first_and_clears_derived_rows(indexed_repo: Path) -> None:
    declined = CliRunner().invoke(attribution_group, ["purge", str(indexed_repo)], input="n\n")
    assert declined.exit_code == 0
    commits, _ = run_async(_labels(indexed_repo))
    assert ("claude", "agent") in commits

    result = CliRunner().invoke(attribution_group, ["purge", str(indexed_repo), "--yes"])
    assert result.exit_code == 0, result.output
    commits, meta = run_async(_labels(indexed_repo))
    assert set(commits) == {(None, None)}
    assert meta == [(0, None, 0)]


def test_purge_leaves_third_party_git_ai_notes(indexed_repo: Path) -> None:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=indexed_repo, check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q")
    git("config", "user.email", "a@example.com")
    git("config", "user.name", "A")
    (indexed_repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "a.py")
    git("commit", "-q", "-m", "init")
    note = 'a.py\n  h_0123456789abcd 1\n---\n{"schema_version": "authorship/3.0.0", "prompts": {}}'
    git("notes", "--ref=ai", "add", "-m", note, "HEAD")

    result = CliRunner().invoke(attribution_group, ["purge", str(indexed_repo), "--yes"])
    assert result.exit_code == 0, result.output
    assert "h_0123456789abcd" in git("notes", "--ref=ai", "show", "HEAD")


def test_status_explains_a_value_the_other_layer_overrides(indexed_repo: Path) -> None:
    (indexed_repo / ".repowise" / "config.yaml").write_text(
        "attribution:\n  capture: notes\n", encoding="utf-8"
    )
    result = CliRunner().invoke(attribution_group, ["status", str(indexed_repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    (reason,) = json.loads(result.output)["explanations"]
    assert reason.startswith("capture: the repo config sets notes, but off applies")
    assert "the user config does not set it" in reason
