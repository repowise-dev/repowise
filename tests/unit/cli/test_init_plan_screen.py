"""The init plan screen: what it shows, what each key does, and the -y help."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli.main import cli
from repowise.cli.ui import provider_selection
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.plan_screen import (
    EDIT,
    MODEL,
    QUIT,
    START,
    THIS_REPO,
    ModelOffer,
    PlanState,
    RunShape,
    build_offer,
    read_plan_key,
    render_plan,
)
from repowise.cli.ui.repo_scanner import RepoScanInfo
from repowise.cli.ui.subscription import SubscriptionContext

_SCAN = RepoScanInfo(total_files=50, language_counts={"Python": 30}, total_commits=400)
_OUTSIDE = ("Global repowise MCP entry (Claude Code) now points at /old, will point here: /new",)


def _render(state: PlanState) -> str:
    console = Console(record=True, width=100, color_system=None, theme=THEME)
    render_plan(console, state)
    # Flattened: where the console wraps at 100 columns is not the copy's business.
    return " ".join(console.export_text().split())


def _state(**kwargs: Any) -> PlanState:
    return PlanState(repo_name="demo", scan=_SCAN, **kwargs)


# -- render -------------------------------------------------------------------


def test_keyless_plan_is_the_free_run_with_a_visible_model_opt_in() -> None:
    out = _render(_state(agents=("Claude Code",)))
    assert "demo · 50 files scanned · 30 source · 1 language · 400 commits" in out
    assert "Under a minute to index and build the documentation." in out
    assert "free, runs on this machine" in out
    assert "Built from code" in out
    assert "No key found" in out
    assert "Claude Code, Codex or OpenCode" in out
    assert "decisions from PRs, git history" in out
    assert "Enter start" in out and "m add a model" in out and "q quit" in out
    # s only exists when the global entry would move.
    assert "this repo only" not in out
    assert "e covers scope, run mode" in out
    assert "\N{EM DASH}" not in out


def test_a_detected_key_is_named_with_cost_and_time_and_never_used_unasked() -> None:
    offer = build_offer({"openai": "OPENAI_API_KEY"}, (), RunShape(_SCAN))
    out = _render(_state(offer=offer, model_embedder="openai"))
    assert "OPENAI_API_KEY found: openai /" in out
    assert "$" in out and "model calls" in out and "min more" in out
    assert "used only if you press m" in out
    assert "semantic search with the openai embedder" in out
    # The plan itself stays free.
    assert "Built from code" in out


def test_a_logged_in_claude_code_is_offered_with_quota_and_time(monkeypatch: Any) -> None:
    offer = build_offer({"claude_cli": "agent CLI"}, (), RunShape(_SCAN))
    out = _render(_state(offer=offer))
    assert "Claude Code is logged in: claude_cli /" in out
    assert "uses your Claude Code login" in out
    assert "at a time" in out
    assert "$0.00" not in out


def test_a_chosen_model_changes_the_plan_and_the_m_key() -> None:
    offer = build_offer({"openai": "OPENAI_API_KEY"}, (), RunShape(_SCAN))
    assert offer.price is not None
    out = _render(_state(offer=offer, chosen=offer.price, model_embedder="openai"))
    assert f"Written by {offer.price.model}" in out
    assert "uses openai /" in out
    assert "m back to built from code" in out
    assert "Optional" not in out
    assert "full-text and semantic search (openai embedder)" in out


def test_outside_this_repo_shows_old_and_new_and_offers_s() -> None:
    out = _render(_state(outside=_OUTSIDE))
    assert "! Outside this repo" in out
    assert "now points at /old, will point here: /new" in out
    assert "s this repo only" in out
    kept = _render(_state(outside=_OUTSIDE, this_repo_only=True))
    assert "left as is" in kept
    assert "s point it here" in kept


def test_editor_setup_off_writes_nothing_outside() -> None:
    out = _render(_state(outside=_OUTSIDE, editor_setup=False))
    assert "Outside this repo" not in out
    assert "off (--no-editor-setup)" in out
    assert "this repo only" not in out


def test_fast_mode_and_the_large_repo_hint() -> None:
    assert "Index only: none this run" in _render(_state(run_mode="fast"))
    big = _render(_state(large_repo=True))
    assert "Above 5,000 files a fast first index (index only) is recommended" in big


# -- keys ---------------------------------------------------------------------


def _keys(*answers: str):
    it = iter(answers)
    return lambda: next(it)


@pytest.mark.parametrize(
    ("typed", "action"),
    [("", START), ("m", MODEL), ("E", EDIT), (" q ", QUIT)],
)
def test_each_key_maps_to_its_action(typed: str, action: str) -> None:
    assert read_plan_key(Console(width=100, theme=THEME), _state(), _keys(typed)) == action


def test_s_needs_a_global_entry_that_would_move() -> None:
    console = Console(record=True, width=100, theme=THEME)
    assert read_plan_key(console, _state(), _keys("s", "x", "")) == START
    assert "Press Enter to start, or m, e or q." in console.export_text()
    assert read_plan_key(console, _state(outside=_OUTSIDE), _keys("s")) == THIS_REPO


def test_eof_propagates_to_the_caller() -> None:
    def eof() -> str:
        raise EOFError

    with pytest.raises(EOFError):
        read_plan_key(Console(width=100, theme=THEME), _state(), eof)


# -- the no-key path ----------------------------------------------------------


def test_no_key_offers_to_continue_without_a_model(monkeypatch: Any) -> None:
    monkeypatch.setattr(provider_selection, "_detect_provider_status", lambda *_a: {})
    monkeypatch.setattr(provider_selection, "_prompt_api_key", lambda *a, **k: None)
    gemini = str(list(provider_selection._PROVIDER_CHOICES).index("gemini") + 1)
    answers = iter([gemini, "1"])
    monkeypatch.setattr(provider_selection.Prompt, "ask", lambda *a, **k: next(answers))
    console = Console(record=True, width=100, theme=THEME)

    selection = provider_selection.interactive_provider_config_select(
        console, None, subscription=SubscriptionContext(allow_skip=True)
    )

    assert selection is None
    out = console.export_text()
    assert "Continue without a model" in out
    assert "Please select another provider" not in out


def test_no_key_can_still_pick_another_provider(monkeypatch: Any) -> None:
    renders: list[int] = []

    def detect(*_a: object) -> dict[str, str]:
        renders.append(1)
        return {} if len(renders) == 1 else {"openai": "OPENAI_API_KEY"}

    monkeypatch.setattr(provider_selection, "_detect_provider_status", detect)
    monkeypatch.setattr(provider_selection, "_prompt_api_key", lambda *a, **k: None)
    choices = list(provider_selection._PROVIDER_CHOICES)
    answers = iter([str(choices.index("gemini") + 1), "2", str(choices.index("openai") + 1)])
    monkeypatch.setattr(provider_selection.Prompt, "ask", lambda *a, **k: next(answers))

    chosen = provider_selection._interactive_provider_name(
        Console(width=100, theme=THEME), None, subscription=SubscriptionContext(allow_skip=True)
    )

    assert chosen == "openai"


# -- the screen loop ----------------------------------------------------------


def _run(tmp_path: Path, **opts: Any) -> Any:
    from repowise.cli.commands.init_cmd._options import InitOptions, InitRun

    fields = {name: None for name in InitOptions.__dataclass_fields__}
    fields.update(
        exclude=(), coverage_report=(), run_mode="standard", concurrency=10,
        onboarding=True, editor_setup=True, save_key=True, yes=False, index_only=False,
        follow_renames=False, skip_tests=False, skip_infra=False,
    )
    fields.update(opts)
    return InitRun(
        opts=InitOptions(**fields), repo_path=tmp_path, start=0.0, is_interactive=True
    )


@pytest.fixture
def plan(monkeypatch: Any):
    from repowise.cli.commands.init_cmd import _plan

    monkeypatch.setattr(_plan, "agent_providers_set_up", lambda _r: ())
    monkeypatch.setattr(provider_selection, "_detect_provider_status", lambda _r: {})
    monkeypatch.setattr(_plan, "outside_lines", lambda _path: _OUTSIDE)
    monkeypatch.setattr(
        "repowise.cli.editor_setup.is_editor_setup_disabled", lambda _flag=False: False
    )
    monkeypatch.setattr("repowise.cli.editor_setup.planned_agents", lambda *_a: ("Claude Code",))

    def drive(run: Any, *keys: str) -> None:
        it = iter(keys)
        monkeypatch.setattr(_plan, "read_plan_key", lambda *_a: next(it))
        _plan.run_plan_screen(run, _SCAN)

    return SimpleNamespace(module=_plan, drive=drive)


def test_enter_runs_the_free_run_and_records_every_answer(plan: Any, tmp_path: Path) -> None:
    run = _run(tmp_path)
    plan.drive(run, START)
    assert run.generate_docs is False
    assert run.opts.index_only is True
    assert run.opts.provider_name is None
    assert run.opts.hook is True
    assert run.global_mcp_consent is True


def test_s_then_enter_keeps_the_global_entry(plan: Any, tmp_path: Path) -> None:
    run = _run(tmp_path)
    plan.drive(run, THIS_REPO, START)
    assert run.global_mcp_consent is False


def test_m_picks_a_model_and_enter_runs_it(
    plan: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    picked = provider_selection.ProviderSelection("openai", "gpt-test", "auto")
    seen: dict[str, Any] = {}

    def picker(*_a: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return picked

    monkeypatch.setattr(plan.module, "interactive_provider_config_select", picker)
    run = _run(tmp_path)
    plan.drive(run, MODEL, START)
    assert seen["subscription"].allow_skip is True
    assert seen["subscription"].model_calls > 0
    assert run.generate_docs is True
    assert run.opts.index_only is False
    assert (run.opts.provider_name, run.opts.model) == ("openai", "gpt-test")


def test_m_with_no_key_goes_back_to_the_plan(
    plan: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(plan.module, "interactive_provider_config_select", lambda *a, **k: None)
    run = _run(tmp_path)
    plan.drive(run, MODEL, START)
    assert run.generate_docs is False
    assert run.opts.index_only is True


def test_q_quits_without_indexing(plan: Any, tmp_path: Path) -> None:
    with pytest.raises(click.exceptions.Exit):
        plan.drive(_run(tmp_path), QUIT)


def test_eof_falls_back_to_the_non_interactive_path(
    plan: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    def eof(*_a: Any) -> str:
        raise EOFError

    monkeypatch.setattr(plan.module, "read_plan_key", eof)
    run = _run(tmp_path)
    plan.module.run_plan_screen(run, _SCAN)
    assert run.is_interactive is False


def test_explicit_no_hook_stays_off(plan: Any, tmp_path: Path) -> None:
    run = _run(tmp_path, hook=False)
    plan.drive(run, START)
    assert run.opts.hook is False


def test_offer_prefers_the_agent_set_up_here() -> None:
    offer = build_offer(
        {"openai": "OPENAI_API_KEY", "claude_cli": "agent CLI"},
        ("claude_cli",),
        RunShape(_SCAN),
    )
    assert offer.price is not None and offer.price.provider == "claude_cli"
    assert build_offer({}, (), RunShape(_SCAN)) == ModelOffer()


# -- -y help ------------------------------------------------------------------


def test_yes_help_says_everything_it_skips() -> None:
    result = CliRunner().invoke(cli, ["init", "--help"], terminal_width=200)
    flat = " ".join(result.output.split())
    for phrase in (
        "Skips the plan screen",
        "written by a model only when a provider is configured",
        "used only with --provider",
        "cost confirmation is accepted",
        "advanced options keep their defaults",
        "detected agents are connected",
        "old and new target printed",
        "post-commit hook is installed",
        "distill rewrite hook is not",
    ):
        assert phrase in flat, phrase


def test_eof_after_a_key_restores_the_run(plan: Any, tmp_path: Path, monkeypatch: Any) -> None:
    def picker(*_a: Any, **_k: Any) -> Any:
        raise EOFError

    monkeypatch.setattr(plan.module, "interactive_provider_config_select", picker)
    run = _run(tmp_path, run_mode="fast")
    opts = run.opts
    plan.drive(run, MODEL)
    assert run.opts is opts
    assert run.is_interactive is False


def test_q_removes_the_repowise_dir_this_run_made(plan: Any, tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    run = _run(tmp_path)
    run.repowise_dir_created = True
    with pytest.raises(click.exceptions.Exit):
        plan.drive(run, QUIT)
    assert not (tmp_path / ".repowise").exists()


def test_q_keeps_a_repowise_dir_that_was_there_or_holds_a_key(
    plan: Any, tmp_path: Path
) -> None:
    (tmp_path / ".repowise").mkdir()
    with pytest.raises(click.exceptions.Exit):
        plan.drive(_run(tmp_path), QUIT)
    assert (tmp_path / ".repowise").is_dir()

    (tmp_path / ".repowise" / ".env").write_text("GEMINI_API_KEY=x\n")
    run = _run(tmp_path)
    run.repowise_dir_created = True
    with pytest.raises(click.exceptions.Exit):
        plan.drive(run, QUIT)
    assert (tmp_path / ".repowise" / ".env").exists()


# -- ETA ----------------------------------------------------------------------


def _scan(source: int, commits: int) -> RepoScanInfo:
    return RepoScanInfo(
        total_files=source, language_counts={"Python": source}, total_commits=commits
    )


_BUILD = "to index and build the documentation."


@pytest.mark.parametrize(
    ("source", "commits", "run_mode", "expected"),
    [
        # itsdangerous: about 10 s for the whole free run.
        (16, 677, "standard", f"Under a minute {_BUILD}"),
        # flask: 1m 51s, nearly all of it file history and co-changes.
        (90, 5555, "standard", f"About 1-3 min {_BUILD}"),
        # fastapi: 1m 41s as a fast index, 1m 24s to 1m 44s as a full free run.
        (1186, 7860, "fast", "About 1-3 min to index."),
        (1186, 7860, "standard", f"About 1-3 min {_BUILD}"),
    ],
)
def test_eta_matches_measured_runs(
    source: int, commits: int, run_mode: str, expected: str
) -> None:
    state = PlanState(repo_name="r", scan=_scan(source, commits), run_mode=run_mode)
    assert expected in _render(state)


def test_eta_counts_only_the_commits_the_limit_lets_git_walk() -> None:
    state = PlanState(repo_name="r", scan=_scan(90, 5555), commit_limit=500)
    assert f"Under a minute {_BUILD}" in _render(state)


def test_eta_never_reads_zero_minutes() -> None:
    from repowise.cli.ui.plan_screen.state import eta_phrase

    assert eta_phrase(5, 59) == "under a minute"
    assert eta_phrase(30, 61) == "about 1-2 min"
    assert eta_phrase(200, 400) == "about 3-7 min"


# -- model id -----------------------------------------------------------------


def test_a_subscription_model_is_named_once() -> None:
    offer = build_offer({"claude_cli": "agent CLI"}, (), RunShape(_SCAN))
    assert offer.price is not None
    out = _render(_state(offer=offer))
    assert "claude_cli / claude_cli/" not in out
    assert f"claude_cli / {offer.price.model_label}" in out
    chosen = _render(_state(offer=offer, chosen=offer.price))
    assert f"Written by {offer.price.model_label}" in chosen
    assert "claude_cli/" not in chosen


def test_back_from_the_gateway_keeps_the_picker_context(monkeypatch: Any) -> None:
    seen: list[Any] = []
    names = iter([provider_selection._OPENAI_COMPATIBLE_CHOICE, None])

    def name(*_a: Any, subscription: Any, **_k: Any) -> Any:
        seen.append(subscription)
        return next(names)

    monkeypatch.setattr(provider_selection, "_interactive_provider_name", name)
    monkeypatch.setattr(
        provider_selection, "_interactive_openai_compatible_select", lambda *a, **k: None
    )
    context = SubscriptionContext(allow_skip=True)

    selection = provider_selection.interactive_provider_config_select(
        Console(width=100, theme=THEME), None, subscription=context
    )

    assert selection is None
    assert seen == [context, context]


# -- end of input inside the questions behind e and m -------------------------


def _raise(exc: type[BaseException]):
    def read(_prompt: str) -> str:
        raise exc

    return read


def test_eof_inside_edit_falls_back_like_the_key_read(
    plan: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(click.termui, "visible_prompt_func", _raise(EOFError))
    run = _run(tmp_path)
    opts = run.opts
    plan.drive(run, EDIT)
    assert run.is_interactive is False
    assert run.opts is opts


def test_eof_at_the_key_prompt_behind_m_falls_back_too(
    plan: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(click.termui, "hidden_prompt_func", _raise(EOFError))
    monkeypatch.setattr(provider_selection.Prompt, "ask", lambda *a, **k: "1")
    run = _run(tmp_path)
    plan.drive(run, MODEL)
    assert run.is_interactive is False


def test_ctrl_c_inside_edit_still_aborts(plan: Any, tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(click.termui, "visible_prompt_func", _raise(KeyboardInterrupt))
    with pytest.raises(click.Abort):
        plan.drive(_run(tmp_path), EDIT)


def test_click_prompts_are_restored_after_the_screen(plan: Any, tmp_path: Path) -> None:
    before = (click.termui.visible_prompt_func, click.termui.hidden_prompt_func)
    plan.drive(_run(tmp_path), START)
    assert (click.termui.visible_prompt_func, click.termui.hidden_prompt_func) == before


# -- after Enter: the plan is not repeated -------------------------------------


def _announce(run: Any, monkeypatch: Any) -> str:
    from repowise.cli.commands.init_cmd import _provider

    console = Console(record=True, width=100, color_system=None, theme=THEME)
    monkeypatch.setattr(_provider, "console", console)
    _provider.announce_index_only(run)
    return " ".join(console.export_text().split())


def test_after_the_plan_screen_the_forecast_is_not_repeated(
    tmp_path: Path, monkeypatch: Any
) -> None:
    run = _run(tmp_path, index_only=True, editor_setup=False)
    assert _announce(run, monkeypatch) == ""
    assert "MCP" not in _announce(run, monkeypatch)


def test_a_run_without_the_screen_still_says_what_it_builds(
    tmp_path: Path, monkeypatch: Any
) -> None:
    run = _run(tmp_path, index_only=True)
    run.is_interactive = False
    out = _announce(run, monkeypatch)
    assert "Built from code" in out
    assert "no model, no spend" in out
