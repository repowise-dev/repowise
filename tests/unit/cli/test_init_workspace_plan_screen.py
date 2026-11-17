"""The workspace plan screen: the repo list, choosing repos and the primary, EOF."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
from rich.console import Console

from repowise.cli.ui import workspace_selection
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.plan_screen import (
    EDIT,
    MODEL,
    QUIT,
    REPOS,
    START,
    PlanState,
    build_offer,
    read_plan_key,
)
from repowise.cli.ui.plan_screen.workspace import (
    ROWS_SHOWN,
    WorkspacePlanState,
    WorkspaceRepo,
    choose_repos,
    render_workspace_plan,
)
from repowise.cli.ui.repo_scanner import RepoScanInfo

_ROOT = Path("/work/platform")


def _repo(i: int) -> WorkspaceRepo:
    name = f"service-{i}"
    return WorkspaceRepo(
        SimpleNamespace(
            name=name,
            alias=name,
            path=_ROOT / "services" / name,
            has_repowise=i == 2,
            is_submodule=False,
        ),
        RepoScanInfo(total_files=50 * i, language_counts={"Python": 30}, total_commits=100),
    )


def _state(n: int, **plan: Any) -> WorkspacePlanState:
    repos = [_repo(i) for i in range(1, n + 1)]
    return WorkspacePlanState(
        root=_ROOT,
        repos=repos,
        selected=list(range(n)),
        primary="service-1",
        plan=PlanState(repo_name="service-1", scan=repos[0].scan, **plan),
    )


def _lines(state: WorkspacePlanState) -> list[str]:
    console = Console(record=True, width=100, color_system=None, theme=THEME)
    render_workspace_plan(console, state)
    return console.export_text().splitlines()


def _flat(state: WorkspacePlanState) -> str:
    return " ".join(" ".join(_lines(state)).split())


def _answers() -> Any:
    from repowise.cli.commands.init_cmd._workspace_plan import WorkspaceAnswers

    return WorkspaceAnswers(
        index_only=False, provider_name=None, model=None, reasoning=None,
        commit_limit=None, follow_renames=False, skip_tests=False, skip_infra=False,
        concurrency=10, exclude_patterns=[], test_run=False, onboarding=True,
        wiki_style="comprehensive", language=None,
    )


def _keys(*answers: str):
    it = iter(answers)
    return lambda: next(it)


# -- render -------------------------------------------------------------------


def test_three_repos_show_the_list_the_plan_and_the_repos_key() -> None:
    state = _state(3)
    out = _flat(state)
    assert "platform · 3 repos found · 3 selected (300 files)" in out
    assert "to index and build the documentation for 3 repos" in out
    assert "[x] 1 service-1 services/service-1 50 new · primary" in out
    assert "[x] 2 service-2 services/service-2 100 indexed" in out
    # The same plan rows as one repo.
    for row in ("Index", "Decisions", "Documentation", "Built from code", "Search", "Connect"):
        assert row in out
    assert "Keep in sync" in out
    assert "r choose repos" in out and "Enter start" in out and "q quit" in out
    assert "\N{EM DASH}" not in out
    assert all(len(line) <= 100 for line in _lines(state))


def test_twenty_five_repos_list_the_first_rows_and_count_the_rest() -> None:
    state = _state(25)
    state.selected = [0, 2, 24]
    out = _flat(state)
    assert f"service-{ROWS_SHOWN}" in out
    assert f"service-{ROWS_SHOWN + 1} " not in out
    assert "13 more repos not shown; 3 of 25 selected in all. r lists every repo." in out
    assert "[ ] 2 service-2" in out
    assert "25 repos found · 3 selected" in out
    assert all(len(line) <= 100 for line in _lines(state))


def test_the_model_line_prices_the_selected_repos_together() -> None:
    one = _state(3)
    one.selected = [0]
    three = _state(3)
    one.plan = replace(one.plan, offer=build_offer({"openai": "K"}, (), one.shape()))
    three.plan = replace(three.plan, offer=build_offer({"openai": "K"}, (), three.shape()))
    assert one.plan.offer.price and three.plan.offer.price
    assert one.plan.offer.price.cost != three.plan.offer.price.cost
    out = _flat(three)
    assert "K found: openai /" in out and "model calls" in out
    # Per repo, the compact column: a model's calls for each selected repo.
    assert "Model calls" in out and "about " in out


def test_a_long_path_is_cut_to_fit_100_columns() -> None:
    state = _state(3)
    state.repos[0].repo.path = _ROOT / ("deep-" * 30) / "service-1"
    state.repos[0].repo.name = "a-repository-name-" * 4
    assert all(len(line) <= 100 for line in _lines(state))


# -- keys ---------------------------------------------------------------------


def test_r_is_offered_on_the_workspace_screen_only() -> None:
    console = Console(record=True, width=100, theme=THEME)
    plan = _state(3).plan
    assert read_plan_key(console, plan, _keys("r"), extra={"r": REPOS}) == REPOS
    assert read_plan_key(console, plan, _keys("r", ""), extra=None) == START
    assert "Press Enter to start, or m, e or q." in console.export_text()
    console = Console(record=True, width=100, theme=THEME)
    read_plan_key(console, plan, _keys("x", ""), extra={"r": REPOS})
    assert "Press Enter to start, or m, e, r or q." in console.export_text()


def test_choose_repos_takes_numbers_and_ranges_then_the_primary(monkeypatch: Any) -> None:
    monkeypatch.setattr(workspace_selection.Prompt, "ask", lambda *a, **k: "2")
    state = _state(5)
    choose_repos(Console(width=100, theme=THEME), state, _keys("1,3-4"))
    assert state.selected == [0, 2, 3]
    assert state.primary == "service-3"


def test_choose_repos_keeps_the_primary_when_it_stays_picked(monkeypatch: Any) -> None:
    defaults: list[str] = []

    def ask(*_a: Any, **kwargs: Any) -> str:
        defaults.append(kwargs["default"])
        return kwargs["default"]

    monkeypatch.setattr(workspace_selection.Prompt, "ask", ask)
    state = _state(4)
    state.primary = "service-3"
    choose_repos(Console(width=100, theme=THEME), state, _keys("2-3"))
    assert state.selected == [1, 2]
    assert defaults == ["2"]
    assert state.primary == "service-3"


def test_choose_repos_rejects_none_and_nonsense_and_enter_keeps(monkeypatch: Any) -> None:
    console = Console(record=True, width=100, theme=THEME)
    state = _state(3)
    choose_repos(console, state, _keys("none", "9", "x", "3"))
    out = console.export_text()
    assert "Choose at least one repo" in out
    assert "Invalid selection" in out
    assert state.selected == [2] and state.primary == "service-3"
    choose_repos(console, state, _keys(""))
    assert state.selected == [2]


# -- the screen loop ----------------------------------------------------------


@pytest.fixture
def screen(monkeypatch: Any):
    from repowise.cli.commands.init_cmd import _workspace_plan as module
    from repowise.cli.ui import provider_selection

    monkeypatch.setattr(module, "quick_repo_scan", lambda p: RepoScanInfo(total_files=50))
    monkeypatch.setattr(module, "agent_providers_set_up", lambda _r: ())
    monkeypatch.setattr(provider_selection, "_detect_provider_status", lambda _r: {})
    monkeypatch.setattr(module, "outside_lines", lambda _p: ())
    monkeypatch.setattr("repowise.cli.editor_setup.planned_agents", lambda *_a: ("Claude Code",))
    monkeypatch.setattr(
        "repowise.cli.editor_setup.is_editor_setup_disabled", lambda _flag=False: False
    )
    scan = SimpleNamespace(root=_ROOT, repos=[_repo(i).repo for i in (1, 2, 3)])

    def drive(*keys: str, hook: bool | None = None) -> Any:
        it = iter(keys)
        monkeypatch.setattr(module, "read_plan_key", lambda *_a, **_k: next(it))
        return module.run_workspace_plan_screen(
            scan, _answers(), editor_options=None, editor_setup=True, save_key=False, hook=hook
        )

    return SimpleNamespace(module=module, drive=drive)


def test_enter_runs_every_repo_built_from_code(screen: Any) -> None:
    plan = screen.drive(START)
    assert [r.alias for r in plan.selected] == ["service-1", "service-2", "service-3"]
    assert plan.primary_alias == "service-1"
    assert plan.answers.index_only is True
    assert plan.answers.provider_name is None
    assert plan.sync is True


def test_r_then_enter_runs_the_chosen_repos(screen: Any, monkeypatch: Any) -> None:
    # Rich's prompt reads both: the repos, then the primary.
    typed = iter(["1,3", "2"])
    monkeypatch.setattr(workspace_selection.Prompt, "ask", lambda *a, **k: next(typed))
    plan = screen.drive(REPOS, START)
    assert [r.alias for r in plan.selected] == ["service-1", "service-3"]
    assert plan.primary_alias == "service-3"


def test_m_picks_a_model_for_every_repo(screen: Any, monkeypatch: Any) -> None:
    from repowise.cli.ui.provider_selection import ProviderSelection

    seen: dict[str, Any] = {}

    def picker(*_a: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return ProviderSelection("openai", "gpt-test", "auto")

    monkeypatch.setattr(screen.module, "interactive_provider_config_select", picker)
    plan = screen.drive(MODEL, START)
    assert seen["subscription"].model_calls > 0
    assert seen["repo_path"] == _ROOT / "services" / "service-1"
    assert plan.answers.index_only is False
    assert (plan.answers.provider_name, plan.answers.model) == ("openai", "gpt-test")


def test_q_quits_without_indexing(screen: Any) -> None:
    with pytest.raises(click.exceptions.Exit):
        screen.drive(QUIT)


def test_eof_falls_back_to_the_defaults(screen: Any, monkeypatch: Any) -> None:
    def eof(*_a: Any, **_k: Any) -> str:
        raise EOFError

    monkeypatch.setattr(screen.module, "read_plan_key", eof)
    scan = SimpleNamespace(root=_ROOT, repos=[_repo(1).repo, _repo(2).repo])
    plan = screen.module.run_workspace_plan_screen(
        scan, _answers(), editor_options=None, editor_setup=True, save_key=False, hook=None
    )
    assert plan is None


def test_eof_inside_the_repo_question_falls_back_too(screen: Any, monkeypatch: Any) -> None:
    def eof(*_a: Any, **_k: Any) -> str:
        raise EOFError

    monkeypatch.setattr(workspace_selection.Prompt, "ask", eof)
    assert screen.drive(REPOS) is None



def test_e_does_not_ask_file_pages_a_workspace_would_ignore(screen: Any, monkeypatch: Any) -> None:
    from repowise.cli.ui import mode_selection

    def no_file_pages(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("file pages asked on a workspace")

    monkeypatch.setattr(mode_selection, "prompt_file_page_volume", no_file_pages)
    monkeypatch.setattr(mode_selection.click, "confirm", lambda *_a, **k: k.get("default", False))
    monkeypatch.setattr(mode_selection.click, "prompt", lambda *_a, **k: k.get("default"))
    monkeypatch.setattr(screen.module.click, "confirm", lambda *_a, **k: False)
    monkeypatch.setattr(mode_selection, "_prompt_exclude", lambda *_a, **_k: [])
    monkeypatch.setattr(mode_selection, "_prompt_index_only_search", lambda *_a, **_k: None)
    plan = screen.drive(EDIT, START)
    assert plan.sync is False
    assert plan.answers.index_only is True
