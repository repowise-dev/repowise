"""`repowise dead-code` reads the findings the index stored instead of re-parsing.

The stored path answers the default request when the index is at HEAD; a flag
the stored run did not compute (a floor below the one it stored at) and an
index behind HEAD fall back to the live analysis. Both paths print one shape.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands import dead_code_cmd
from repowise.cli.commands.dead_code_cmd import dead_code_command
from repowise.cli.helpers import run_async

# A finding the live analyzer can never produce for this tree: the file does
# not exist, so seeing it proves the answer came from the store.
_STORED_ONLY = {
    "kind": "unused_export",
    "file_path": "gone/only_in_store.py",
    "symbol_name": "stored_symbol",
    "symbol_kind": "function",
    "confidence": 0.9,
    "reason": "Public symbol 'stored_symbol' has no importers",
    "lines": 4,
    "start_line": 1,
    "end_line": 4,
    "evidence": ["stored"],
    "safe_to_delete": True,
    "commit_count_90d": 0,
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


async def _store(repo_path: Path, findings: list[dict]) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import save_dead_code_findings

    db_path = repo_path / ".repowise" / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            await save_dead_code_findings(session, repo.id, findings)
    finally:
        await engine.dispose()


@pytest.fixture
def indexed_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = tmp_path.resolve()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "a@example.com")
    _git(root, "config", "user.name", "A")
    (root / ".gitignore").write_text(".repowise/\n")
    (root / "a.py").write_text("def unused():\n    return 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    (root / ".repowise").mkdir()
    state = {"last_sync_commit": _git(root, "rev-parse", "HEAD")}
    (root / ".repowise" / "state.json").write_text(json.dumps(state))
    run_async(_store(root, [_STORED_ONLY]))
    return root


def _json(root: Path, *args: str) -> list[dict]:
    result = CliRunner().invoke(
        dead_code_command, [str(root), "--format", "json", "--no-workspace", *args]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output[result.output.index("[") :])


def test_default_flags_read_the_store_without_parsing(indexed_repo, monkeypatch):
    def _no_parse(*_a, **_k):
        raise AssertionError("the live analysis ran for a request the store answers")

    monkeypatch.setattr(dead_code_cmd, "_analyze_live", _no_parse)

    findings = _json(indexed_repo)

    assert [f["file_path"] for f in findings] == [_STORED_ONLY["file_path"]]
    assert findings[0]["safe_to_delete"] is True


def test_a_floor_below_the_stored_one_runs_live(indexed_repo):
    findings = _json(indexed_repo, "--min-confidence", "0.0")

    assert _STORED_ONLY["file_path"] not in {f["file_path"] for f in findings}


def test_an_index_behind_head_runs_live(indexed_repo):
    (indexed_repo / "b.py").write_text("x = 1\n")
    _git(indexed_repo, "add", "b.py")
    _git(indexed_repo, "commit", "-q", "-m", "second")

    findings = _json(indexed_repo)

    assert _STORED_ONLY["file_path"] not in {f["file_path"] for f in findings}


def test_kind_switches_filter_the_stored_rows(indexed_repo, monkeypatch):
    monkeypatch.setattr(dead_code_cmd, "_analyze_live", None)

    assert _json(indexed_repo, "--no-unused-exports") == []
    assert len(_json(indexed_repo, "--kind", "unused_export")) == 1


def test_both_paths_print_one_shape(indexed_repo):
    stored = _json(indexed_repo)
    live = _json(indexed_repo, "--min-confidence", "0.0")

    assert live, "the live fixture must produce a finding to compare"
    assert {tuple(f) for f in stored} == {tuple(f) for f in live}
