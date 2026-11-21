"""``update`` keeps an existing ``.repowise/decisions.yaml`` current, and only that.

The file is opt-in and people commit it, so the guarantees are: no file is
ever created; a file is rewritten only when the rewrite loses nothing (the
store moved on, the file did not); and a file that says something the store
has not read, committed or not, is left exactly as it is.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.commands.update_cmd.persistence import refresh_decisions_manifest
from repowise.cli.main import cli

from .test_decision_sync import (
    AGENT_ACCEPTED,
    AGENT_CANDIDATE,
    _accept,
    _db,
    _git,
    _seed,
)

_MANIFEST = Path(".repowise") / "decisions.yaml"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "widget"
    path.mkdir()
    _git(path, "init", "-q")
    (path / ".gitignore").write_text(".repowise/\n", encoding="utf-8")
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "start")
    _seed(path)
    return path


def _export_and_commit(repo: Path) -> str:
    result = CliRunner().invoke(cli, ["decision", "export", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    _git(repo, "add", ".gitignore", str(_MANIFEST))
    _git(repo, "commit", "-q", "-m", "decisions")
    return (repo / _MANIFEST).read_text(encoding="utf-8")


def _add_rows(repo: Path, *rows) -> None:
    async def _write() -> None:
        engine = create_async_engine(_db(repo))
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                session.add_all(rows)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_write())


def _entries(repo: Path) -> dict[str, dict]:
    data = yaml.safe_load((repo / _MANIFEST).read_text(encoding="utf-8"))
    return {e["id"]: e for e in data["decisions"]}


def _rewrite(repo: Path, edit) -> None:
    path = repo / _MANIFEST
    text = path.read_text(encoding="utf-8")
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    data = yaml.safe_load(text)
    edit(data)
    path.write_text(header + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_no_file_is_never_created(repo: Path) -> None:
    assert refresh_decisions_manifest(repo) == "absent"

    assert not (repo / _MANIFEST).exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_a_new_decision_reaches_an_existing_file(repo: Path) -> None:
    _export_and_commit(repo)
    assert AGENT_CANDIDATE not in _entries(repo)
    _add_rows(repo, _accept(AGENT_CANDIDATE))

    assert refresh_decisions_manifest(repo) == "written"
    assert AGENT_CANDIDATE in _entries(repo)
    # Nothing new: the bytes stay as they are.
    before = (repo / _MANIFEST).read_bytes()
    assert refresh_decisions_manifest(repo) == "unchanged"
    assert (repo / _MANIFEST).read_bytes() == before


def test_a_decision_the_store_moved_past_is_rewritten(repo: Path) -> None:
    _export_and_commit(repo)
    withdrawn = _accept(AGENT_ACCEPTED)
    withdrawn.seq = 2
    withdrawn.action = "dismissed"
    withdrawn.currency = "dismissed"
    withdrawn.created_at = datetime.now(UTC) + timedelta(minutes=1)
    _add_rows(repo, withdrawn)

    assert refresh_decisions_manifest(repo) == "written"
    assert _entries(repo)[AGENT_ACCEPTED]["currency"] == "dismissed"


def test_a_committed_edit_the_store_has_not_read_is_held(repo: Path) -> None:
    _export_and_commit(repo)

    def edit(data):
        for entry in data["decisions"]:
            if entry["id"] == AGENT_ACCEPTED:
                entry["reason"] = "A colleague narrowed this"

    _rewrite(repo, edit)
    _git(repo, "commit", "-q", "-am", "colleague")
    before = (repo / _MANIFEST).read_bytes()
    _add_rows(repo, _accept(AGENT_CANDIDATE))

    assert refresh_decisions_manifest(repo) == "held"
    assert (repo / _MANIFEST).read_bytes() == before


def test_a_colleagues_new_entry_is_held(repo: Path) -> None:
    _export_and_commit(repo)

    def add(data):
        data["decisions"].append(
            {
                "id": "f" * 32,
                "title": "Queue writes",
                "decision": "Writes go through the queue",
                "reason": "Bursts",
                "scope": ["src/q.py"],
            }
        )

    _rewrite(repo, add)
    before = (repo / _MANIFEST).read_bytes()

    assert refresh_decisions_manifest(repo) == "held"
    assert (repo / _MANIFEST).read_bytes() == before


def test_an_uncommitted_removal_is_held(repo: Path) -> None:
    _export_and_commit(repo)
    _rewrite(
        repo,
        lambda data: data.update(
            decisions=[e for e in data["decisions"] if e["id"] != AGENT_ACCEPTED]
        ),
    )
    before = (repo / _MANIFEST).read_bytes()
    _add_rows(repo, _accept(AGENT_CANDIDATE))

    assert refresh_decisions_manifest(repo) == "held"
    assert (repo / _MANIFEST).read_bytes() == before


def test_decisions_off_leaves_the_file_alone(repo: Path) -> None:
    _export_and_commit(repo)
    (repo / ".repowise" / "config.yaml").write_text(
        "decisions:\n  enabled: false\n", encoding="utf-8"
    )
    _add_rows(repo, _accept(AGENT_CANDIDATE))

    assert refresh_decisions_manifest(repo) == "absent"
    assert AGENT_CANDIDATE not in _entries(repo)


def test_the_format_is_what_export_writes(repo: Path) -> None:
    exported = _export_and_commit(repo)
    _add_rows(repo, _accept(AGENT_CANDIDATE))
    refresh_decisions_manifest(repo)
    refreshed = (repo / _MANIFEST).read_text(encoding="utf-8")

    # The same command run by hand writes the same bytes.
    (repo / _MANIFEST).write_text(exported, encoding="utf-8")
    result = CliRunner().invoke(cli, ["decision", "export", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert (repo / _MANIFEST).read_text(encoding="utf-8") == refreshed
    assert json.loads(result.output)["exported"] == len(_entries(repo))
