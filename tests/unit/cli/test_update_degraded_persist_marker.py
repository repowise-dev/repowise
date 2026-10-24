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


def test_degraded_docs_update_leaves_a_marker_the_next_update_re_covers(
    tmp_path: Path, monkeypatch
) -> None:
    import asyncio

    from click.testing import CliRunner

    import repowise.core.pipeline.persist as persist
    from repowise.cli.commands.update_cmd.command import release_update_lock
    from repowise.cli.helpers import save_state
    from repowise.cli.main import cli
    from repowise.core.pipeline.full_index import index_repo_full

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
    asyncio.run(index_repo_full(repo))
    save_state(repo, {"last_sync_commit": c0, "last_docs_commit": c0, "docs_enabled": True})

    (repo / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "c1")
    c1 = _git(repo, "rev-parse", "HEAD")

    real_symbols = persist.persist_incremental_symbols

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(persist, "persist_incremental_symbols", _boom)
    result = CliRunner().invoke(
        cli, ["update", str(repo), "--docs", "--provider", "mock", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output
    assert "next update will re-cover it" in result.output

    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["last_sync_commit"] == c1
    assert state["pending_repair"] == {"from_commit": c0, "steps": ["Symbol persist"]}

    monkeypatch.setattr(persist, "persist_incremental_symbols", real_symbols)
    # The first run's lock is released at exit, which never comes in-process.
    release_update_lock(repo)
    result = CliRunner().invoke(cli, ["update", str(repo), "--index-only", "--no-workspace"])
    assert result.exit_code == 0, result.output
    assert f"(from {c0[:8]})" in result.output

    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert "pending_repair" not in state


def test_update_prunes_legacy_degraded_notices_from_state(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from repowise.cli.main import cli

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

    # Seed state with legacy init notices plus a real warning directly in state.json
    state_file = repo / ".repowise" / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "last_sync_commit": c0,
                "last_docs_commit": c0,
                "docs_mode": "none",
                "degraded": [
                    "Graph build can take several minutes on a first run. Safe to Ctrl-C — "
                    "re-run 'repowise init --resume' to continue where it stopped.",
                    "  Not indexed: big.cs (9,745 KB, over the 2,048 KB limit)",
                    "  ...and more source files skipped on size",
                    "Git indexing skipped: boom",
                ],
            }
        ),
        encoding="utf-8",
    )

    (repo / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "c1")

    result = CliRunner().invoke(cli, ["update", str(repo), "--index-only", "--no-workspace"])
    assert result.exit_code == 0, result.output

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state.get("degraded") == ["Git indexing skipped: boom"]

    # When update runs on a state with only legacy notices, "degraded" is dropped entirely.
    (repo / "a.py").write_text("def a():\n    return 3\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "c2")

    disk_state = json.loads(state_file.read_text(encoding="utf-8"))
    disk_state["degraded"] = [
        "Graph build can take several minutes on a first run. Safe to Ctrl-C — "
        "re-run 'repowise init --resume' to continue where it stopped."
    ]
    state_file.write_text(json.dumps(disk_state), encoding="utf-8")

    from repowise.cli.commands.update_cmd.command import release_update_lock

    release_update_lock(repo)
    result = CliRunner().invoke(cli, ["update", str(repo), "--index-only", "--no-workspace"])
    assert result.exit_code == 0, result.output

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "degraded" not in state

