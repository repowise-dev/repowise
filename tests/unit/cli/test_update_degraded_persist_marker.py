"""A degraded range-scoped persist leaves a repair marker, end to end.

The sync pointer advances after a degraded run so every other reader stays
current, and the marker is what keeps the range from being stranded: the next
update widens its base back to the commit this run started from. The unit
tests pin the marker arithmetic; this one drives the real command through a
forced failure and reads ``state.json`` back.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def test_degraded_git_persist_records_the_range_it_missed(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from repowise.cli.helpers import save_state
    from repowise.cli.main import cli
    from repowise.core.persistence import crud

    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "c0")
    c0 = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise").mkdir()
    save_state(repo, {"last_sync_commit": c0, "last_docs_commit": c0, "docs_mode": "none"})

    (repo / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "c1")
    c1 = _git(repo, "rev-parse", "HEAD")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(crud, "upsert_git_metadata_bulk", _boom)

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    assert "next update will re-cover it" in result.output

    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["last_sync_commit"] == c1
    assert state["pending_repair"]["from_commit"] == c0
    assert "Git persist" in state["pending_repair"]["steps"]
