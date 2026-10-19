"""`repowise events` against a real local index."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.events_cmd import events_command
from repowise.cli.helpers import run_async


async def _store(repo_path: Path) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import EngineEventRecord

    (repo_path / ".repowise").mkdir(exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{(repo_path / '.repowise' / 'wiki.db').as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            now = datetime.now(UTC)
            for i, (kind, age) in enumerate((("dead_code_growth", 30), ("test_reach_drop", 1))):
                session.add(
                    EngineEventRecord(
                        id=f"{i:032d}",
                        repository_id=repo.id,
                        kind=kind,
                        component="api",
                        severity="info",
                        occurred_at=now - timedelta(days=age),
                        stamp="s",
                        payload_json=json.dumps({"evidence": {"basis": "inferred", "share": 0.4}}),
                    )
                )
    finally:
        await engine.dispose()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    run_async(_store(root))
    return root


def test_formats_and_filters(tmp_path):
    root = _repo(tmp_path)
    runner = CliRunner()

    got = runner.invoke(events_command, ["--path", str(root), "--format", "json"])
    assert got.exit_code == 0, got.output
    kinds = [e["data"]["repowise.kind"] for e in json.loads(got.output)["events"]]
    assert kinds == ["dead_code_growth", "test_reach_drop"]

    recent = runner.invoke(events_command, ["--path", str(root), "--since", "7d", "--format", "jsonl"])
    lines = [json.loads(line) for line in recent.output.splitlines() if line.strip()]
    assert [e["id"] for e in lines] == [f"{1:032d}"]

    table = runner.invoke(events_command, ["--path", str(root), "--kind", "dead_code_growth"])
    assert table.exit_code == 0 and "dead_code_growth" in table.output
    assert "test_reach_drop" not in table.output

    bad = runner.invoke(events_command, ["--path", str(root), "--kind", "nope"])
    assert bad.exit_code != 0
    bad_since = runner.invoke(events_command, ["--path", str(root), "--since", "yesterday"])
    assert bad_since.exit_code != 0


def test_no_events_says_whether_runs_could_be_compared(tmp_path):
    root = _repo(tmp_path)
    got = CliRunner().invoke(events_command, ["--path", str(root), "--since", "2099-01-01"])
    assert got.exit_code == 0, got.output
    # No snapshot is stored, so no events is not a quiet history.
    assert "No events yet: 0 component snapshot(s) stored" in got.output
