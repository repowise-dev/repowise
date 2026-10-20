"""The options ``repowise init`` was invoked with, and the state one run builds up."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console


@dataclass(frozen=True)
class InitOptions:
    """Every ``repowise init`` flag, named as the Click parameters are.

    Stages that act on an answer (the interactive questions, ``--mode fast``)
    derive a new value with :func:`dataclasses.replace` rather than mutate this.
    """

    path: str | None
    provider_name: str | None
    model: str | None
    embedder_name: str | None
    skip_tests: bool
    skip_infra: bool
    dry_run: bool
    yes: bool
    resume: bool
    force: bool
    concurrency: int
    reasoning: str | None
    test_run: bool
    prose: bool | None
    index_only: bool
    docs_opt: str | None
    run_mode: str
    exclude: tuple[str, ...]
    commit_limit: int | None
    follow_renames: bool
    no_claude_md: bool
    seed_from: str | None
    no_seed: bool
    agents_md: bool | None
    codex_setup: bool | None
    distill_hook: bool | None
    hook: bool | None
    editor_setup: bool
    save_key: bool
    include_submodules: bool
    no_workspace: bool
    init_all: bool
    repos: str | None
    onboarding: bool
    max_file_pages_opt: int | None
    coverage_report: tuple[str, ...]
    wiki_style: str | None
    language_opt: str | None
    no_cost_tracking: bool
    verbose: bool
    progress: str


@dataclass
class InitRun:
    """What a single-repo init has resolved so far, filled in stage by stage."""

    opts: InitOptions
    repo_path: Path
    start: float
    is_interactive: bool = False
    #: This run made ``.repowise/``, so quitting before indexing removes it again.
    repowise_dir_created: bool = False
    # Whether a model writes the documentation (the plan screen's answer).
    generate_docs: bool = True
    # Advanced-mode answers, None until chosen. Each loses to its flag and
    # wins over config.yaml.
    language_choice: str | None = None
    max_file_pages_choice: int | None = None
    editor_options: Any = None
    #: The plan screen's answer to repointing the global MCP entry; None = not asked.
    global_mcp_consent: bool | None = None
    #: The plan screen's answer: add machine-specific files to .git/info/exclude.
    git_exclude: bool = False
    config: dict[str, Any] = field(default_factory=dict)
    language: str = "en"
    max_file_pages: int | None = None
    resolved_reasoning: str = "auto"
    exclude_patterns: list[str] = field(default_factory=list)
    resolved_commit_limit: int = 0
    resolved_follow_renames: bool = False
    embedder_name_resolved: str = ""
    # Why that embedder: the flag, pin or env key behind it, for the run to state.
    embedder_reason: str = ""
    embedder_was_requested: bool = False
    prior_docs_mode: str | None = None
    first_index: bool = False
    provider: Any = None
    # Set when a full run found no provider at all and fell back to the
    # template renderer. Treated exactly like ``--index-only`` from the
    # generation phase onward.
    no_provider: bool = False
    total_phases: int = 4
    result: Any = None
    # PhaseTimingRecorder shared by every stage.
    recorder: Any = None
    run_warnings: list[str] = field(default_factory=list)
    # Embedder of the template wiki, None when none was rendered.
    index_only_embedder: str | None = None
    cost_declined: bool = False
    # Asked-for work a stage could not do; any entry makes init exit degraded.
    gaps: list[str] = field(default_factory=list)
    effective_index_only: bool = False
    phase_timings: dict[str, float] = field(default_factory=dict)
    scope_embedder: str | None = None
    # What the finish reported, kept for the --progress json summary.
    mcp_repointed: bool = False
    setup_outcome: Any = None
    files_written: list[Path] = field(default_factory=list)
    next_steps: list[tuple[str, str]] = field(default_factory=list)


def resolve_structural_flags(opts: InitOptions) -> InitOptions:
    """Fold ``--prose``, ``--index-only``, ``--docs`` and ``--mode fast`` into ``index_only``.

    ``--prose / --no-prose`` is the one switch: prose on the concept pages, or
    the whole wiki from structure. ``--index-only`` and ``--docs`` are the
    previous spellings, kept working for one release as deprecated aliases so a
    scripted ``--index-only`` does not break. ``index_only`` stays the internal
    value every downstream branch reads.
    """
    if opts.index_only or opts.docs_opt is not None:
        console.print(
            "[dim]--index-only and --docs are deprecated; use --no-prose "
            "(built from code, no key) or --prose (written by a model). They still "
            "work this release.[/dim]"
        )
    # Each of the three sources votes for "structural" (True) or "prose" (False).
    # --index-only always means structural; --docs maps deterministic->structural,
    # llm->prose; --prose/--no-prose is the direct switch. Two votes that disagree
    # is a contradiction rather than a silent last-one-wins.
    votes = []
    if opts.index_only:
        votes.append(True)
    if opts.docs_opt is not None:
        votes.append(opts.docs_opt == "deterministic")
    if opts.prose is not None:
        votes.append(not opts.prose)
    if len(set(votes)) > 1:
        raise click.UsageError(
            "This mix of --prose / --no-prose, --index-only and --docs contradicts "
            "itself. Pass one."
        )
    index_only = votes[0] if votes else opts.index_only
    # --mode fast is a graph + essential-git index with no LLM work, so it
    # implies index-only on the CLI side; the orchestrator mode switches the
    # git tier to ESSENTIAL.
    if opts.run_mode == "fast":
        index_only = True
    # --test-run caps what a model writes; a run with no model has nothing to cap.
    if opts.test_run and index_only:
        raise click.UsageError(
            "--test-run limits the model run to the top 10 files, and --no-prose "
            "or --mode fast runs no model. Drop --test-run, or use --prose."
        )
    return replace(opts, index_only=index_only)
