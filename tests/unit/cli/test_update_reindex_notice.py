"""The reindex recommendation on the ``repowise update`` path.

Two guarantees: it is surfaced at most once per store (even across the no-op
"already up to date" path, which is the common upgrade case), and an old-shape
store survives ``update`` / ``status`` without error while the recommendation
stays alive (the routine persist never advances past the reindex gate).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli import upgrade as cliup
from repowise.cli.commands.update_cmd import command as upd
from repowise.cli.helpers import save_state
from repowise.cli.main import cli


class _FakeTerminal:
    """A console that reports as a terminal and records what was printed."""

    is_terminal = True

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **_kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))


def test_reindex_notice_shows_once_then_suppresses(tmp_path: Path, monkeypatch) -> None:
    save_state(tmp_path, {"store_format_version": 1})  # a store predating the concept tree
    verdict = cliup.assess_store(tmp_path)
    assert verdict.reindex_recommended

    fake = _FakeTerminal()
    monkeypatch.setattr(upd, "console", fake)

    upd._surface_reindex_recommendation(tmp_path, verdict, emitter=None, dry_run=False)
    assert any("Reindex recommended" in line for line in fake.lines)

    fake.lines.clear()
    upd._surface_reindex_recommendation(tmp_path, verdict, emitter=None, dry_run=False)
    assert fake.lines == []  # ledger suppresses the second showing


def test_reindex_notice_silent_when_not_a_terminal(tmp_path: Path, monkeypatch) -> None:
    save_state(tmp_path, {"store_format_version": 1})
    verdict = cliup.assess_store(tmp_path)

    class _NonTerminal(_FakeTerminal):
        is_terminal = False

    fake = _NonTerminal()
    monkeypatch.setattr(upd, "console", fake)
    upd._surface_reindex_recommendation(tmp_path, verdict, emitter=None, dry_run=False)
    assert fake.lines == []
    # And a suppressed showing must not burn the one-shot: nothing was recorded.
    assert not cliup.reindex_notice_already_shown(tmp_path, verdict)


# --- e2e: an old-shape store survives update/status -----------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_update_survives_old_store_and_keeps_recommendation(tmp_path: Path) -> None:
    from repowise.core.pipeline.full_index import index_repo_full

    repo = _make_repo(tmp_path)
    asyncio.run(index_repo_full(repo))  # real index-only store (wiki.db), keyless

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()

    # Author a state.json as a release predating the concept tree would have left
    # it: at store format v1, synced to HEAD so `update` takes the no-op path.
    state_path = repo / ".repowise" / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "store_format_version": 1,
                "written_by_version": "0.21.0",
                "last_sync_commit": head,
                "git_tier": "full",
                "run_mode": "standard",
            }
        ),
        encoding="utf-8",
    )

    res = CliRunner().invoke(cli, ["update", str(repo)])
    assert res.exit_code == 0, res.output

    after = json.loads(state_path.read_text(encoding="utf-8"))
    # The routine update never advances past the reindex gate, so the store
    # stays at v1 and the recommendation is not silenced.
    assert after["store_format_version"] == 1

    res_status = CliRunner().invoke(cli, ["status", str(repo)])
    assert res_status.exit_code == 0, res_status.output
