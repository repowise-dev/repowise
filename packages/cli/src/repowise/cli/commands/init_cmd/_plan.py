"""The plan screen of an interactive single-repo init: what each key changes."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console, load_config
from repowise.cli.ui import (
    WARN,
    agent_providers_set_up,
    interactive_advanced_config,
    interactive_provider_config_select,
    should_offer_fast_mode,
)
from repowise.cli.ui.plan_screen import (
    EDIT,
    MODEL,
    QUIT,
    THIS_REPO,
    ModelPrice,
    PlanState,
    RunShape,
    build_offer,
    end_of_input_is_eof,
    model_calls_for,
    price_model,
    read_plan_key,
    render_plan,
)
from repowise.cli.ui.subscription import SubscriptionContext

from ._options import InitRun


@dataclass
class _Answers:
    """What the keys have changed so far."""

    chosen: ModelPrice | None = None
    selection: Any = None
    sync: bool = True
    this_repo_only: bool = False
    git_exclude: bool = False


def outside_lines(repo_path: Path) -> tuple[str, ...]:
    """Old and new target of the global MCP entry, empty when it would not move."""
    from repowise.cli.editor_integrations.global_mcp import read_global_mcp_target

    try:
        target = read_global_mcp_target(repo_path)
    # A probe failure never blocks the screen; JSONDecodeError is a ValueError.
    except (OSError, ValueError, KeyError, TypeError):
        return ()
    return tuple(target.summary_lines()) if target.repoints else ()


def _embedders(run: InitRun) -> tuple[str, str]:
    """``(free run embedder, model run embedder)``, as the run would resolve them."""
    from repowise.cli.providers import resolve_embedder, template_run_embedder
    from repowise.cli.providers.embedders import embedder_was_requested

    resolved = resolve_embedder(run.opts.embedder_name)
    requested = embedder_was_requested(
        run.opts.embedder_name, load_config(run.repo_path).get("embedder")
    )
    return template_run_embedder(resolved, requested), resolved


def _keep_out(run: InitRun, editor_setup: bool) -> tuple[str, ...]:
    """Machine-specific files the run leaves visible to git, written now or by this run."""
    from repowise.cli.editor_setup import planned_project_files
    from repowise.cli.git_exclude import keep_out_candidates

    from ._prepare import _editor_options

    planned = planned_project_files(run.repo_path, _editor_options(run)) if editor_setup else ()
    return keep_out_candidates(run.repo_path, planned)


def _state(run: InitRun, base: PlanState, answers: _Answers) -> PlanState:
    """The screen for the run as it stands."""
    from repowise.cli.editor_setup import planned_agents

    from ._prepare import _editor_options

    free_embedder, model_embedder = _embedders(run)
    agents = planned_agents(run.repo_path, _editor_options(run)) if base.editor_setup else ()
    keep_out = _keep_out(run, base.editor_setup)
    return replace(
        base,
        run_mode=run.opts.run_mode,
        chosen=answers.chosen,
        free_embedder=free_embedder,
        model_embedder=model_embedder,
        agents=agents,
        sync=answers.sync,
        this_repo_only=answers.this_repo_only,
        keep_out=keep_out,
        git_exclude=answers.git_exclude and bool(keep_out),
        commit_limit=_commit_limit(run),
    )


def _commit_limit(run: InitRun) -> int:
    """The commit cap the run will use: flag, then config.yaml, then the default."""
    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT

    limit = (
        run.opts.commit_limit
        or load_config(run.repo_path).get("commit_limit")
        or _DEFAULT_COMMIT_LIMIT
    )
    return max(1, min(int(limit), 10000))


def _shape(run: InitRun, scan: Any) -> RunShape:
    return RunShape(scan, onboarding=run.opts.onboarding, concurrency=run.opts.concurrency)


def _base_state(run: InitRun, scan: Any, prefer: tuple[str, ...]) -> PlanState:
    """The parts that do not change while the screen is up (detection, the probe)."""
    from repowise.cli.editor_setup import is_editor_setup_disabled
    from repowise.cli.ui.provider_selection import _detect_provider_status

    opts = run.opts
    editor_setup = not is_editor_setup_disabled(not opts.editor_setup)
    with console.status("  Checking for a model…"):
        offer = build_offer(_detect_provider_status(run.repo_path), prefer, _shape(run, scan))
    return PlanState(
        repo_name=run.repo_path.name or str(run.repo_path),
        scan=scan,
        offer=offer,
        editor_setup=editor_setup,
        outside=outside_lines(run.repo_path) if editor_setup else (),
        large_repo=should_offer_fast_mode(scan),
    )


def _pick_model(run: InitRun, scan: Any, prefer: tuple[str, ...], answers: _Answers) -> None:
    """``m``: the picker, or back to built from code when a model is already picked."""
    if answers.chosen is not None:
        answers.chosen = answers.selection = None
        return
    opts = run.opts
    if opts.run_mode == "fast":
        # A model writes documentation, which an index-only run skips.
        run.opts = replace(opts, run_mode="standard")
        console.print("  [dim]Run mode back to standard: fast mode writes no documentation.[/dim]")
    selection = interactive_provider_config_select(
        console,
        opts.model,
        opts.reasoning,
        repo_path=run.repo_path,
        save_key=opts.save_key,
        subscription=SubscriptionContext(
            prefer=prefer,
            model_calls=model_calls_for(_shape(run, scan)),
            concurrency=opts.concurrency,
            allow_skip=True,
        ),
    )
    if selection is None:
        return
    answers.selection = selection
    answers.chosen = price_model(selection.provider_name, selection.model, _shape(run, scan))


def _edit_plan(run: InitRun, scan: Any, editor_setup: bool, answers: _Answers) -> None:
    """``e``: the advanced options, the agent checklist and the sync hook."""
    from repowise.cli.editor_setup import select_agents_interactively

    from ._prepare import _editor_options
    from ._questions import _apply_advanced_answers

    run.generate_docs = answers.chosen is not None
    adv = interactive_advanced_config(
        console,
        scan=scan,
        allow_fast=True,
        prompt_reasoning=False,
        generate_docs=run.generate_docs,
        wiki_style=run.opts.wiki_style,
        language=run.opts.language_opt,
        commit_limit=run.opts.commit_limit,
    )
    _apply_advanced_answers(run, adv)
    if run.opts.run_mode == "fast":
        answers.chosen = answers.selection = None
    if editor_setup:
        run.editor_options = select_agents_interactively(
            console, run.repo_path, _editor_options(run)
        )
        answers.sync = click.confirm(
            "  Keep the index in sync after each commit (post-commit hook)?",
            default=answers.sync,
        )
    keep_out = _keep_out(run, editor_setup)
    if keep_out:
        answers.git_exclude = click.confirm(
            f"  Keep {', '.join(keep_out)} out of git (adds to .git/info/exclude)?",
            default=answers.git_exclude,
        )


def _apply(run: InitRun, base: PlanState, answers: _Answers) -> None:
    """Enter: fold the plan into the run, so nothing later asks again."""
    opts = run.opts
    selection = answers.selection
    run.generate_docs = selection is not None
    if selection is not None:
        opts = replace(
            opts,
            index_only=False,
            provider_name=selection.provider_name,
            model=selection.model,
            reasoning=selection.reasoning,
        )
    else:
        opts = replace(opts, index_only=True)
    if base.editor_setup and opts.hook is None:
        opts = replace(opts, hook=answers.sync)
    run.opts = opts
    if base.can_keep_global:
        run.global_mcp_consent = not answers.this_repo_only
    run.git_exclude = answers.git_exclude


def _leave_repo_as_it_was(run: InitRun) -> None:
    """Remove the ``.repowise/`` this run made, when nothing has been put in it."""
    if not run.repowise_dir_created:
        return
    # Not empty means the picker saved a key there, and that stays.
    with contextlib.suppress(OSError):
        (run.repo_path / ".repowise").rmdir()


def _until_enter(
    run: InitRun, scan: Any, prefer: tuple[str, ...], base: PlanState, answers: _Answers
) -> None:
    """Render, read a key, act on it; return on Enter."""
    while True:
        state = _state(run, base, answers)
        console.print()
        render_plan(console, state)
        action = read_plan_key(console, state)
        if action == QUIT:
            _leave_repo_as_it_was(run)
            console.print("  [dim]Nothing indexed.[/dim]")
            raise click.exceptions.Exit(0)
        if action == MODEL:
            _pick_model(run, scan, prefer, answers)
        elif action == EDIT:
            _edit_plan(run, scan, base.editor_setup, answers)
        elif action == THIS_REPO:
            answers.this_repo_only = not answers.this_repo_only
        else:
            # What the screen showed: on only while there is something to exclude.
            answers.git_exclude = state.git_exclude
            return


def say_no_answer() -> None:
    """The line a plan screen prints when stdin ends before Enter."""
    console.print(
        f"\n[{WARN}]No answer available on stdin[/] "
        "[dim]- continuing with defaults. Pass --yes to skip the "
        "questions, or --prose / --no-prose to choose directly.[/dim]"
    )


def run_plan_screen(run: InitRun, scan: Any) -> None:
    """Show the plan, act on keys until Enter, then fold the answers into *run*.

    The first read is the probe for a terminal that cannot answer (Windows
    Git Bash ``< /dev/null``, pty wrappers, ``docker run -t`` without -i):
    EOF drops to the non-interactive path instead of dying on a question
    nobody can hear. Only EOFError is caught; Ctrl-C still stops the run.
    """
    prefer = agent_providers_set_up(run.repo_path)
    base = _base_state(run, scan, prefer)
    answers = _Answers(sync=run.opts.hook if run.opts.hook is not None else True)
    # Restored on EOF, so the defaults path never runs half an answer.
    before = (run.opts, run.generate_docs, run.editor_options)
    try:
        # The questions behind m and e end the same way as the key read.
        with end_of_input_is_eof():
            _until_enter(run, scan, prefer, base, answers)
    except EOFError:
        run.opts, run.generate_docs, run.editor_options = before
        run.is_interactive = False
        say_no_answer()
        return
    _apply(run, base, answers)
