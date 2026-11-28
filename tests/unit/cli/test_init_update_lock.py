"""`init` takes the update lock, not just its own marker (#5512).

The init marker alone is not mutual exclusion. ``update`` learns of it only in
``_acquire_update_lock``, so a run that already holds the update lock keeps
writing the store an init is also writing. Both halves have to hold both locks,
and a third copy that starts while one is running must fail rather than stack.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from repowise.cli.commands.init_cmd import command as init_command
from repowise.cli.main import cli
from repowise.core.update_lock import (
    read_init_lock,
    read_update_lock,
    try_acquire_update_lock,
)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return tmp_path


def _init_with_stubbed_body(monkeypatch, repo, tmp_path):
    """Run the real command path up to `_run_single_repo`, stubbing the body."""
    started = []

    def _fake_run(opts, repo_path, start):
        started.append(repo_path)

    monkeypatch.setattr(init_command, "_run_single_repo", _fake_run)
    monkeypatch.setattr(
        "repowise.cli.commands.init_cmd._prepare.run_provider_survey",
        lambda *a, **k: None,
        raising=False,
    )
    result = CliRunner().invoke(
        cli,
        ["init", str(repo), "--no-workspace", "--no-prose", "--no-editor-setup"],
    )
    return result, started


def test_init_takes_the_update_lock_while_it_runs(repo, tmp_path, monkeypatch):
    result, started = _init_with_stubbed_body(monkeypatch, repo, tmp_path)
    assert result.exit_code == 0, result.output
    assert started, "_run_single_repo never ran, so the test proved nothing"
    # The body released it on the way out.
    assert read_update_lock(repo) is None
    assert read_init_lock(repo) is None


def test_init_refuses_when_an_update_holds_the_lock(repo, tmp_path, monkeypatch):
    holder = try_acquire_update_lock(repo, "abc123")
    assert holder is None, "the lock should have been free for the holder"

    result, started = _init_with_stubbed_body(monkeypatch, repo, tmp_path)
    assert not started, "init ran while another writer held the lock"
    assert result.exit_code != 0
    assert "already running" in result.output
    # The holder is untouched: init must not steal or clear it.
    assert read_update_lock(repo) is not None
    # init's own marker must not survive a refusal either.
    assert read_init_lock(repo) is None
