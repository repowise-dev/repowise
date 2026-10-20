"""Everything a single-repo init settles before indexing."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import click

from repowise.cli._setup import configure_cli_logging
from repowise.cli.editor_integrations.defaults import (
    get_default_disabled_project_files,
    get_default_integration_overrides,
    get_default_project_file_overrides,
)
from repowise.cli.editor_setup import resolve_editor_setup_options
from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    load_config,
    load_state,
    resolve_max_file_pages,
    resolve_reasoning,
)
from repowise.cli.providers import resolve_embedder_choice
from repowise.cli.providers.embedders import embedder_was_requested as _embedder_was_requested
from repowise.cli.ui import WARN, load_dotenv
from repowise.core.docs_mode import resolve_docs_mode
from repowise.core.generation.styles import resolve_style

from ._options import InitOptions, InitRun
from ._provider import (
    announce_index_only,
    note_subscription_cli,
    resolve_docs_provider,
)
from ._questions import _ask_questions
from .persistence import effective_run_mode_for_resume


def _interactive_gate(
    *,
    isatty: bool,
    provider_name: str | None,
    index_only: bool,
    yes: bool,
    resume: bool,
) -> bool:
    """Whether the interactive questionnaire runs.

    Interactive requires a TTY, no explicit provider, docs on, and neither
    --yes nor --resume. --resume skips the gate because the prior run
    already answered every question and those answers are on disk
    (config.yaml); re-asking would let a resumed run diverge from the pages
    already written with them.
    """
    return isatty and provider_name is None and not index_only and not yes and not resume


def _start_run(opts: InitOptions, repo_path: Path, start: float) -> InitRun:
    """Prepare ``.repowise/``, logging and the resume mode, then open the run."""
    dir_created = not (repo_path / ".repowise").exists()
    ensure_repowise_dir(repo_path)
    load_dotenv(repo_path)

    # Quiet library/structlog output so the progress bars are the only output;
    # `-v` lets repowise's debug lines through for troubleshooting.
    configure_cli_logging(verbose=opts.verbose)

    # On --resume, continue the prior run's git tier rather than fall back to
    # FULL. Done before the interactive gate so a resume never re-prompts.
    run_mode = effective_run_mode_for_resume(repo_path, opts.run_mode, opts.resume)
    opts = replace(opts, run_mode=run_mode, index_only=opts.index_only or run_mode == "fast")

    # --yes forces non-interactive even on a TTY (mirrors the workspace path),
    # so a scripted `init -y` never blocks on the mode-selection menu.
    is_interactive = _interactive_gate(
        isatty=sys.stdin.isatty(),
        provider_name=opts.provider_name,
        index_only=opts.index_only,
        yes=opts.yes,
        resume=opts.resume,
    )

    if opts.resume:
        console.print(
            f"[bold]Resuming[/] the previous run in {repo_path}, "
            "reusing the answers it already saved to config.yaml."
        )
    return InitRun(
        opts=opts,
        repo_path=repo_path,
        start=start,
        is_interactive=is_interactive,
        generate_docs=not opts.index_only,
        repowise_dir_created=dir_created,
    )


def _editor_options(run: InitRun) -> Any:
    """Which editor files and integrations this run writes.

    The agent checklist on the plan screen's ``e`` sets ``run.editor_options``;
    otherwise the flags and detection decide, with no question.
    """
    if run.editor_options is not None:
        return run.editor_options
    opts = run.opts
    return resolve_editor_setup_options(
        disabled_project_files=get_default_disabled_project_files(
            no_claude_md=opts.no_claude_md,
        ),
        project_file_overrides=get_default_project_file_overrides(
            agents_md=opts.agents_md,
        ),
        integration_overrides=get_default_integration_overrides(
            codex_setup=opts.codex_setup,
        ),
    )


def _resolve_settings(run: InitRun) -> None:
    """Resolve each setting from its flag, the interactive answer and config.yaml."""
    opts = run.opts
    config = load_config(run.repo_path)
    run.config = config
    # Output language: CLI flag > advanced-mode choice > config.yaml > English.
    run.language = opts.language_opt or run.language_choice or config.get("language", "en")
    # File-page cap: flag > advanced-mode answer > config.yaml > unset (policy).
    file_pages = opts.max_file_pages_opt
    if file_pages is None:
        file_pages = run.max_file_pages_choice
    run.max_file_pages = resolve_max_file_pages(file_pages, config)
    run.resolved_reasoning = resolve_reasoning(opts.reasoning, config)
    # Merge exclude_patterns from config.yaml and --exclude/-x flags.
    run.exclude_patterns = list(config.get("exclude_patterns") or []) + list(opts.exclude)

    # Commit limit: CLI flag → config.yaml → default.
    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT

    commit_limit = opts.commit_limit or config.get("commit_limit") or _DEFAULT_COMMIT_LIMIT
    run.resolved_commit_limit = max(1, min(commit_limit, 10000))
    if opts.commit_limit is not None:
        config["commit_limit"] = run.resolved_commit_limit

    # follow_renames: CLI flag → config.yaml.
    run.resolved_follow_renames = opts.follow_renames or config.get("follow_renames", False)
    if opts.follow_renames:
        config["follow_renames"] = True

    run.embedder_name_resolved, run.embedder_reason = resolve_embedder_choice(opts.embedder_name)
    run.embedder_was_requested = _embedder_was_requested(opts.embedder_name, config.get("embedder"))


def _announce_kept_documentation(run: InitRun) -> None:
    """Fast mode writes no pages, so documentation already here stays."""
    if run.prior_docs_mode != "none":
        console.print(
            "[dim]Keeping the documentation this repo already has. "
            "Fast mode rebuilds the index and leaves the pages as they are.[/dim]"
        )


def _confirm_wiki_overwrite(run: InitRun) -> bool:
    """Read the prior state; False when the user keeps a model-written wiki.

    A template wiki overwrites a model-written one page for page: the upsert
    replaces content and stamps provider_name="template", keeping the old text
    only as a version snapshot. Fine when the user meant it, a bad surprise
    when they were reaching for a re-index, so ask. Non-interactive runs refuse
    rather than guess. ``--yes`` and ``--force`` both mean "do not ask me".
    """
    prior_state = load_state(run.repo_path)
    # Whether this repo has ever been indexed, captured before the run writes
    # its own state. A first index tells the user to restart Claude Code to
    # load the tools; a re-index does not.
    run.first_index = not prior_state.get("last_sync_commit")
    run.prior_docs_mode = resolve_docs_mode(prior_state)
    opts = run.opts
    if opts.run_mode == "fast":
        _announce_kept_documentation(run)
        return True
    if not opts.index_only or run.prior_docs_mode != "llm" or opts.force or opts.yes:
        return True
    console.print(
        f"\n[{WARN}]This repo already has documentation written by a model.[/] "
        "Indexing without a model\nrebuilds every page from code; the "
        "written versions stay in page history."
    )
    if not sys.stdin.isatty():
        raise reasoned_error(
            "Refusing to replace documentation written by a model with pages built "
            "from code. Re-run with --yes to confirm, or drop --no-prose.",
            reason="wiki_overwrite_unconfirmed",
        )
    if not click.confirm(
        "  Replace the written documentation with pages built from code?", default=False
    ):
        console.print("[dim]Nothing changed.[/dim]")
        return False
    return True


def _resolve_provider_stage(run: InitRun) -> None:
    """Which model this run calls: none for a structural run, else the docs model.

    The one gate for ``--no-prose`` (and ``--mode fast``): no provider is
    resolved, so nothing later in the run can call a model, whatever keys the
    environment or stored config holds.
    """
    if run.opts.index_only:
        if run.opts.provider_name:
            console.print(
                f"[dim]--provider {run.opts.provider_name} is not used: this run "
                "builds from code and makes no model calls.[/dim]"
            )
        announce_index_only(run)
    else:
        resolve_docs_provider(run)
    note_subscription_cli(run)


def _prepare_single_repo(opts: InitOptions, repo_path: Path, start: float) -> InitRun | None:
    """Everything a single-repo run settles before indexing. None to stop."""
    run = _start_run(opts, repo_path, start)
    if run.is_interactive:
        _ask_questions(run)

    # Resolve the effective style (CLI flag > interactive prompt > default) and
    # canonicalize it. Used by generation and persisted so update/restyle honor it.
    run.opts = replace(run.opts, wiki_style=resolve_style(run.opts.wiki_style).name)
    run.editor_options = _editor_options(run)
    _resolve_settings(run)
    if not _confirm_wiki_overwrite(run):
        return None
    _resolve_provider_stage(run)
    return run
