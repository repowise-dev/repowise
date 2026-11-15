"""An owed wiki rebuild keeps an update with no new commit off the "up to date" exit."""

from __future__ import annotations

from pathlib import Path
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


def _run(repo_path: Path, *, wiki_rebuild: bool) -> UpdateRun:
    run = UpdateRun(start=0.0, timings=PhaseTimings())
    run.repo_path = repo_path
    run.head = run.base_ref = "abc123"
    run.config = SimpleNamespace(changed=False)
    run.wiki_rebuild = wiki_rebuild
    return run


def test_no_rebuild_owed_is_up_to_date(quiet_store: list[str], tmp_path: Path) -> None:
    run = _run(tmp_path, wiki_rebuild=False)
    command._assess_staleness(SimpleNamespace(refresh_git=False, cascade_budget=None), run)
    assert run.git_is_current
    assert quiet_store == ["parser", "stale"]


def test_owed_rebuild_is_not_up_to_date(quiet_store: list[str], tmp_path: Path) -> None:
    run = _run(tmp_path, wiki_rebuild=True)
    command._assess_staleness(SimpleNamespace(refresh_git=False, cascade_budget=None), run)
    assert not run.git_is_current
    # The "up to date" store reads are skipped, as for any other pending rework.
    assert quiet_store == []


@pytest.fixture
def stale_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """The store holds stale pages an update can render (the cascade budget overflow)."""

    def load(run, opts):
        run.stale_db_paths, run.stale_deterministic_ids = ["a.cpp", "b.h"], {"scc_page:1"}
        return 0

    monkeypatch.setattr(command, "health_analyzer_changed", lambda state: False)
    monkeypatch.setattr(command, "git_history_stale", lambda state: False)
    monkeypatch.setattr(command, "security_scanner_changed", lambda state: False)
    monkeypatch.setattr(command, "parser_changed", lambda path: False)
    monkeypatch.setattr(command, "load_actionable_stale_targets", load)
    monkeypatch.setattr(command, "_file_pages_to_fill", lambda run, opts: False)


def _woken(run: UpdateRun) -> bool:
    """Whether the update leaves the "up to date" exit and runs the pipeline."""
    return not run.git_is_current or bool(run.stale_db_paths or run.stale_deterministic_ids)


def test_pages_left_stale_at_this_commit_keep_the_update_a_no_op(
    stale_pages: None, tmp_path: Path
) -> None:
    run = _run(tmp_path, wiki_rebuild=False)
    run.state = {command.STALE_PAGES_PLANNED_KEY: "abc123"}
    command._assess_staleness(SimpleNamespace(refresh_git=False, cascade_budget=None), run)
    assert not _woken(run)
    assert run.stale_pages_planned == 3


def test_stale_pages_no_run_at_this_commit_planned_still_wake_the_update(
    stale_pages: None, tmp_path: Path
) -> None:
    run = _run(tmp_path, wiki_rebuild=False)
    run.state = {command.STALE_PAGES_PLANNED_KEY: "older"}
    command._assess_staleness(SimpleNamespace(refresh_git=False, cascade_budget=None), run)
    assert _woken(run)
    assert run.stale_db_paths == ["a.cpp", "b.h"]
    assert run.stale_pages_planned == 0


def test_update_full_catch_up_renders_pages_planned_at_this_commit(
    stale_pages: None, tmp_path: Path
) -> None:
    from repowise.cli.commands.update_cmd.catch_up import UNCAPPED_CASCADE

    run = _run(tmp_path, wiki_rebuild=False)
    run.state = {command.STALE_PAGES_PLANNED_KEY: "abc123"}
    command._assess_staleness(
        SimpleNamespace(refresh_git=False, cascade_budget=UNCAPPED_CASCADE), run
    )
    assert _woken(run)
    assert run.stale_db_paths == ["a.cpp", "b.h"]
    assert run.stale_pages_planned == 0


@pytest.mark.parametrize(
    "force",
    ["config", "analyzer", "parser", "history", "working_tree", "refresh_git", "new_commit"],
)
def test_reasons_to_run_still_run_with_pages_planned_at_this_commit(
    stale_pages: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, force: str
) -> None:
    run = _run(tmp_path, wiki_rebuild=False)
    run.state = {command.STALE_PAGES_PLANNED_KEY: "abc123"}
    opts = SimpleNamespace(refresh_git=force == "refresh_git", cascade_budget=None)
    if force == "config":
        run.config = SimpleNamespace(changed=True)
    elif force == "analyzer":
        monkeypatch.setattr(command, "health_analyzer_changed", lambda state: True)
    elif force == "parser":
        monkeypatch.setattr(command, "parser_changed", lambda path: True)
    elif force == "history":
        monkeypatch.setattr(command, "git_history_stale", lambda state: True)
    elif force == "working_tree":
        run.working_tree_diffs = [SimpleNamespace(path="dirty.cpp")]
    elif force == "new_commit":
        run.head = "def456"
    command._assess_staleness(opts, run)
    assert _woken(run)


@pytest.mark.parametrize("renders", [True, False])
def test_planning_pages_records_the_commit_only_when_it_can_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, renders: bool
) -> None:
    from repowise.cli.commands.update_cmd import deterministic, stale_targets

    monkeypatch.setattr(command, "can_regenerate_pages", lambda run, opts: renders)

    monkeypatch.setattr(deterministic, "load_stale_file_page_ages", lambda path: {})
    monkeypatch.setattr(stale_targets, "stale_flow_pages", lambda run: 0)
    monkeypatch.setattr(command, "_stale_renderer_paths", lambda path, parsed: [])
    for name in ("_add_stale_pages", "_widen_for_full_render", "_report_page_plan"):
        monkeypatch.setattr(command, name, lambda *a: None)
    run = _run(tmp_path, wiki_rebuild=False)
    run.state = {}
    run.graph_builder = SimpleNamespace(graph=lambda: None, pagerank=lambda: {})
    run.detector = SimpleNamespace(get_affected_pages=lambda *a, **k: SimpleNamespace())
    command._plan_pages(SimpleNamespace(cascade_budget=5, verbose=False), run)
    # Without a model the stale pages wait for an update at this commit that has one.
    assert run.state.get(command.STALE_PAGES_PLANNED_KEY) == ("abc123" if renders else None)
