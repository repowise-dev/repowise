"""The plan screen of an interactive workspace init: what each key changes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import click

from repowise.cli.helpers import console, load_config
from repowise.cli.providers import resolve_embedder
from repowise.cli.ui import (
    agent_providers_set_up,
    interactive_advanced_config,
    interactive_provider_config_select,
    quick_repo_scan,
)
from repowise.cli.ui.glyphs import glyph
from repowise.cli.ui.mascot import owl_status
from repowise.cli.ui.plan_screen import (
    EDIT,
    MODEL,
    QUIT,
    REPOS,
    THIS_REPO,
    PlanState,
    build_offer,
    end_of_input_is_eof,
    model_calls_for,
    price_model,
    read_plan_key,
)
from repowise.cli.ui.plan_screen.workspace import (
    WorkspacePlanState,
    WorkspaceRepo,
    choose_repos,
    render_workspace_plan,
)
from repowise.cli.ui.subscription import SubscriptionContext

from ._plan import outside_lines, say_no_answer


@dataclass
class WorkspaceAnswers:
    """The run settings the plan screen (or the flags alone) settle for every repo."""

    index_only: bool
    provider_name: str | None
    model: str | None
    reasoning: str | None
    commit_limit: int | None
    follow_renames: bool
    skip_tests: bool
    skip_infra: bool
    concurrency: int
    exclude_patterns: list[str]
    test_run: bool
    onboarding: bool
    wiki_style: str
    language: str | None
    #: ``--embedder`` or the one chosen under ``e``; resolved once keys are loaded.
    embedder_name: str | None = None
    embedder_name_resolved: str = "mock"


def apply_workspace_advanced(answers: WorkspaceAnswers, adv: dict[str, Any]) -> None:
    """Fold the advanced-config answers into the workspace run."""
    answers.commit_limit = adv.get("commit_limit") or answers.commit_limit
    answers.follow_renames = adv.get("follow_renames", answers.follow_renames)
    answers.skip_tests = adv.get("skip_tests", answers.skip_tests)
    answers.skip_infra = adv.get("skip_infra", answers.skip_infra)
    answers.concurrency = adv.get("concurrency", answers.concurrency)
    if adv.get("exclude"):
        answers.exclude_patterns = list(answers.exclude_patterns) + list(adv["exclude"])
    answers.test_run = adv.get("test_run", answers.test_run)
    answers.reasoning = adv.get("reasoning") or answers.reasoning
    answers.embedder_name = adv.get("embedder") or answers.embedder_name
    answers.embedder_name_resolved = resolve_embedder(answers.embedder_name)
    answers.onboarding = adv.get("onboarding", answers.onboarding)
    if adv.get("wiki_style"):
        answers.wiki_style = adv["wiki_style"]
    if adv.get("language"):
        answers.language = adv["language"]


@dataclass
class WorkspacePlan:
    """What Enter on the workspace screen settled."""

    selected: list[Any]
    primary_alias: str
    answers: WorkspaceAnswers
    #: The post-commit hook answer; ``None`` leaves it to the flags.
    sync: bool | None = None
    #: ``True`` / ``False`` answered the global MCP question; ``None`` never asked it.
    global_consent: bool | None = None


@dataclass
class _Screen:
    """What stays put while the screen is up, and what the keys change."""

    state: WorkspacePlanState
    detected: dict[str, str]
    prefer: tuple[str, ...]
    editor_options: Any
    selection: Any = None
    sync: bool = True


def _primary_path(screen: _Screen) -> Any:
    return next(r.repo.path for r in screen.state.repos if r.repo.alias == screen.state.primary)


def _refresh(screen: _Screen, answers: WorkspaceAnswers) -> None:
    """Re-plan for the current pick: prices, agents and the outside line follow it."""
    from repowise.cli.editor_setup import planned_agents
    from repowise.cli.providers import template_run_embedder
    from repowise.cli.providers.embedders import embedder_was_requested

    state = screen.state
    state.onboarding = answers.onboarding
    primary = _primary_path(screen)
    shape = state.shape(answers.onboarding, answers.concurrency)
    chosen = (
        price_model(screen.selection.provider_name, screen.selection.model, shape)
        if screen.selection is not None
        else None
    )
    resolved = resolve_embedder(answers.embedder_name)
    requested = embedder_was_requested(
        answers.embedder_name, load_config(primary).get("embedder")
    )
    plan = state.plan
    state.plan = replace(
        plan,
        repo_name=primary.name,
        scan=next(r.scan for r in state.repos if r.repo.alias == state.primary),
        chosen=chosen,
        offer=build_offer(screen.detected, screen.prefer, shape),
        free_embedder=template_run_embedder(resolved, requested),
        model_embedder=resolved,
        agents=planned_agents(primary, screen.editor_options) if plan.editor_setup else (),
        sync=screen.sync,
        outside=outside_lines(primary) if plan.editor_setup else (),
    )


def _pick_model(screen: _Screen, answers: WorkspaceAnswers, save_key: bool) -> None:
    """``m``: the picker, or back to built from code when a model is already picked."""
    if screen.selection is not None:
        screen.selection = None
        return
    screen.selection = interactive_provider_config_select(
        console,
        answers.model,
        answers.reasoning,
        repo_path=_primary_path(screen),
        save_key=save_key,
        subscription=SubscriptionContext(
            prefer=screen.prefer,
            model_calls=model_calls_for(
                screen.state.shape(answers.onboarding, answers.concurrency)
            ),
            concurrency=answers.concurrency,
            allow_skip=True,
        ),
    )


def _edit_plan(screen: _Screen, answers: WorkspaceAnswers) -> None:
    """``e``: the advanced options, then the sync hook. One answer for every repo."""
    adv = interactive_advanced_config(
        console,
        prompt_reasoning=False,
        generate_docs=screen.selection is not None,
        wiki_style=answers.wiki_style,
        language=answers.language,
        commit_limit=answers.commit_limit,
        ask_file_pages=False,
    )
    apply_workspace_advanced(answers, adv)
    if screen.state.plan.editor_setup:
        screen.sync = click.confirm(
            "  Keep each index in sync after each commit (post-commit hook)?",
            default=screen.sync,
        )


def _until_enter(screen: _Screen, answers: WorkspaceAnswers, save_key: bool) -> None:
    """Render, read a key, act on it; return on Enter."""
    while True:
        _refresh(screen, answers)
        console.print()
        render_workspace_plan(console, screen.state)
        action = read_plan_key(console, screen.state.plan, extra={"r": REPOS})
        if action == QUIT:
            console.print("  [dim]Nothing indexed.[/dim]")
            raise click.exceptions.Exit(0)
        if action == MODEL:
            _pick_model(screen, answers, save_key)
        elif action == EDIT:
            _edit_plan(screen, answers)
        elif action == REPOS:
            choose_repos(console, screen.state)
        elif action == THIS_REPO:
            plan = screen.state.plan
            screen.state.plan = replace(plan, this_repo_only=not plan.this_repo_only)
        else:
            return


def run_workspace_plan_screen(
    scan: Any,
    answers: WorkspaceAnswers,
    *,
    editor_options: Any,
    editor_setup: bool,
    save_key: bool,
    hook: bool | None,
) -> WorkspacePlan | None:
    """Show the workspace plan, act on keys until Enter, and return what it settled.

    ``None`` when nobody can answer (EOF on the first read or inside a
    question): the caller runs the non-interactive defaults, every repo
    with the first as primary. Ctrl-C still stops the run.
    """
    from repowise.cli.editor_setup import is_editor_setup_disabled
    from repowise.cli.ui.provider_selection import _detect_provider_status

    repos = list(scan.repos)
    with owl_status(console, f"  Scanning repositories{glyph('ellipsis')}"):
        rows = [WorkspaceRepo(repo, quick_repo_scan(repo.path)) for repo in repos]
    with console.status("  Checking for a model…"):
        detected = _detect_provider_status(repos[0].path)
    work = replace(answers, exclude_patterns=list(answers.exclude_patterns))
    screen = _Screen(
        state=WorkspacePlanState(
            root=scan.root,
            repos=rows,
            selected=list(range(len(rows))),
            primary=repos[0].alias,
            plan=PlanState(
                repo_name=repos[0].name,
                scan=rows[0].scan,
                editor_setup=not is_editor_setup_disabled(not editor_setup),
            ),
        ),
        detected=detected,
        prefer=agent_providers_set_up(repos[0].path),
        editor_options=editor_options,
        sync=hook if hook is not None else True,
    )
    try:
        # The questions behind m, e and r end the same way as the key read.
        with end_of_input_is_eof():
            _until_enter(screen, work, save_key)
    except EOFError:
        say_no_answer()
        return None
    selection = screen.selection
    work.index_only = selection is None
    if selection is not None:
        work.provider_name = selection.provider_name
        work.model = selection.model
        work.reasoning = selection.reasoning
    plan = screen.state.plan
    return WorkspacePlan(
        selected=[r.repo for r in screen.state.picked],
        primary_alias=screen.state.primary,
        answers=work,
        sync=screen.sync if plan.editor_setup and hook is None else None,
        global_consent=(not plan.this_repo_only) if plan.can_keep_global else None,
    )
