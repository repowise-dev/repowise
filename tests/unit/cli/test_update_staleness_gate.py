"""An owed wiki rebuild keeps an update with no new commit off the "up to date" exit."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import command
from repowise.cli.commands.update_cmd.options import UpdateRun
from repowise.core.pipeline import PhaseTimings


@pytest.fixture
def quiet_store(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Analyzers, history and scanner all current; records the store reads made."""
    reads: list[str] = []
    monkeypatch.setattr(command, "health_analyzer_changed", lambda state: False)
    monkeypatch.setattr(command, "git_history_stale", lambda state: False)
    monkeypatch.setattr(command, "security_scanner_changed", lambda state: False)
    monkeypatch.setattr(command, "parser_changed", lambda path: reads.append("parser") or False)
    monkeypatch.setattr(
        command, "load_actionable_stale_targets", lambda run, opts: reads.append("stale") or 0
    )
    return reads


def _run(*, wiki_rebuild: bool) -> UpdateRun:
    run = UpdateRun(start=0.0, timings=PhaseTimings())
    run.head = run.base_ref = "abc123"
    run.config = SimpleNamespace(changed=False)
    run.wiki_rebuild = wiki_rebuild
    return run


def test_no_rebuild_owed_is_up_to_date(quiet_store: list[str]) -> None:
    run = _run(wiki_rebuild=False)
    command._assess_staleness(SimpleNamespace(), run)
    assert run.git_is_current
    assert quiet_store == ["parser", "stale"]


def test_owed_rebuild_is_not_up_to_date(quiet_store: list[str]) -> None:
    run = _run(wiki_rebuild=True)
    command._assess_staleness(SimpleNamespace(), run)
    assert not run.git_is_current
    # The "up to date" store reads are skipped, as for any other pending rework.
    assert quiet_store == []
