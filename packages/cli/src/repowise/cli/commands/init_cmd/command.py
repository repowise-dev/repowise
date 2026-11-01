"""``repowise init`` — full wiki generation for a repository.

This module owns the Click command and the single-repo orchestration. The
stages live in sibling modules: :mod:`._prepare` (settings, provider, the
questionnaire in :mod:`._questions`), :mod:`._ingest`, :mod:`._generate` and
:mod:`._finish`. The multi-repo path lives in :mod:`.workspace`.

The stage names below are re-exported; patch a name on the module that
looks it up.
"""

from __future__ import annotations

import time
from typing import Any

import click

from repowise.cli.errors import reasoned_error
from repowise.cli.exit_codes import EXIT_CODES_HELP
from repowise.cli.helpers import resolve_repo_path
from repowise.core.analysis.health.coverage import PARSERS as COVERAGE_PARSERS
from repowise.core.generation.folder_coverage import parse_folder_coverage
from repowise.core.generation.languages import SUPPORTED_LANGUAGES
from repowise.core.generation.styles import list_styles, resolve_style
from repowise.core.reasoning import REASONING_MODES
from repowise.core.update_lock import init_in_progress

from ._agent_summary import init_output
from ._exit import exit_interrupted
from ._finish import (
    complete_setup,
    exit_with_gaps,
    persist_index,
    register_clients,
)
from ._generate import (
    _generate,
    _generate_with_model,
    _print_structural_plan,
    _render_template_wiki,
    _run_deterministic_generation_phase,
    _run_generation_phase,
)
from ._ingest import index_repository
from ._options import InitOptions, resolve_structural_flags
from ._prepare import (
    _confirm_wiki_overwrite,
    _editor_options,
    _interactive_gate,
    _prepare_single_repo,
    _resolve_provider_stage,
    _resolve_settings,
    _start_run,
)
from ._questions import _apply_advanced_answers, _ask_questions
from ._seed import delegate_to_seeded_update
from ._sibling_hint import suggest_workspace
from ._workspace_repos import check_repos_flag, split_repo_names
from .workspace import _workspace_init

__all__ = [
    "_apply_advanced_answers",
    "_ask_questions",
    "_confirm_wiki_overwrite",
    "_editor_options",
    "_generate",
    "_generate_with_model",
    "_interactive_gate",
    "_prepare_single_repo",
    "_print_structural_plan",
    "_render_template_wiki",
    "_resolve_provider_stage",
    "_resolve_settings",
    "_run_deterministic_generation_phase",
    "_run_generation_phase",
    "_run_workspace_init",
    "_start_run",
    "init_command",
]


def _run_workspace_init(opts: InitOptions, scan: Any) -> None:
    """Hand a directory holding several repos to the multi-repo flow."""
    _workspace_init(
        scan=scan,
        init_all=opts.init_all,
        repo_names=split_repo_names(opts.repos),
        exclude_patterns=list(opts.exclude),
        commit_limit=opts.commit_limit,
        follow_renames=opts.follow_renames,
        no_claude_md=opts.no_claude_md,
        agents_md=opts.agents_md,
        codex_setup=opts.codex_setup,
        distill_hook=opts.distill_hook,
        hook=opts.hook,
        editor_setup=opts.editor_setup,
        save_key=opts.save_key,
        include_submodules=opts.include_submodules,
        provider_name=opts.provider_name,
        model=opts.model,
        embedder_name=opts.embedder_name,
        index_only=opts.index_only,
        skip_tests=opts.skip_tests,
        skip_infra=opts.skip_infra,
        concurrency=opts.concurrency,
        reasoning=opts.reasoning,
        test_run=opts.test_run,
        yes=opts.yes,
        dry_run=opts.dry_run,
        resume=opts.resume,
        force=opts.force,
        onboarding=opts.onboarding,
        # Apply the chosen style uniformly across the workspace's repos
        # (no per-repo interactive prompt in the multi-repo flow).
        wiki_style=resolve_style(opts.wiki_style).name,
        language=opts.language_opt,
        run_mode=opts.run_mode,
    )


def _check_folder_coverage(
    _ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Reject a malformed ``--folder-coverage`` rule before any indexing starts."""
    try:
        parse_folder_coverage(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param=param) from None
    return value


@click.command("init", epilog=EXIT_CODES_HELP)
@click.argument("path", required=False, default=None)
@click.option(
    "--provider",
    "provider_name",
    default=None,
    help=(
        "LLM provider name (anthropic, openai, openrouter, gemini, "
        "deepseek, kimi, ollama, litellm, codex_cli, copilot_cli, claude_cli, "
        "opencode, "
        "edenai, mock). "
        "In a terminal, a missing key is prompted for; openai also asks for "
        "an optional OpenAI-compatible Base URL."
    ),
)
@click.option("--model", default=None, help="Model identifier override.")
@click.option(
    "--embedder",
    "embedder_name",
    default=None,
    type=click.Choice(["gemini", "openai", "openrouter", "ollama", "edenai", "mock"]),
    help=(
        "Embedder for RAG: gemini | openai | openrouter | ollama | edenai | mock "
        "(default: auto-detect)."
    ),
)
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files.")
@click.option("--skip-infra", is_flag=True, default=False, help="Skip infrastructure files.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    # "No wiki", not "writes nothing": the pipeline still warms its parse and
    # duplication caches, which are derived data.
    help="Show the generation plan and cost estimate. Writes no documentation.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help=(
        "Start at once with every answer at its default. Skips the plan screen "
        "and every question after it: documentation is written by a model only "
        "when a provider is configured (an API key in the environment or "
        ".repowise/.env), else built from code; a logged-in Claude Code, Codex "
        "or OpenCode is used only with --provider; the cost confirmation is "
        "accepted; advanced options keep their defaults (standard run mode at "
        "any size, comprehensive style); the detected agents "
        "are connected; the global repowise MCP entry is pointed here, with the "
        "old and new target printed; the post-commit hook is installed and the "
        "distill rewrite hook is not; and with --no-prose, documentation a "
        "model wrote is replaced without asking."
    ),
)
@click.option(
    "--resume",
    is_flag=True,
    default=False,
    help=(
        "Skip pages already generated in the vector store and continue from "
        "where a previous run stopped. Safe no-op on a fully indexed repo."
    ),
)
@click.option(
    "--force", is_flag=True, default=False, help="Regenerate all pages, ignoring existing."
)
@click.option("--concurrency", type=int, default=10, help="Max concurrent LLM calls.")
@click.option(
    "--reasoning",
    type=click.Choice(REASONING_MODES),
    default=None,
    help=(
        "Reasoning mode for supported providers: auto, off/none, minimal, "
        "low, medium, high, xhigh, or max. Default: auto."
    ),
)
@click.option(
    "--test-run",
    is_flag=True,
    default=False,
    help=(
        "Limit the model run to the top 10 files by PageRank for quick validation. "
        "Needs a model, so not with --no-prose or --mode fast."
    ),
)
@click.option(
    "--prose/--no-prose",
    "prose",
    default=None,
    help=(
        "Documentation written by a model (--prose, needs a key) or built from "
        "code (--no-prose). Only the subsystem (concept) pages differ; every other "
        "page is built from code either way. Default: written by a model when a key "
        "is available. --no-prose makes no model calls, whatever keys are set: it "
        "skips the written subsystem pages and model decision extraction from PRs, "
        "commit history and code comments. Run repowise generate later to add both. "
        "Use --mode fast for an index only, with no documentation."
    ),
)
@click.option(
    "--index-only",
    is_flag=True,
    default=False,
    hidden=True,
    help="Deprecated alias for --no-prose.",
)
@click.option(
    "--docs",
    "docs_opt",
    type=click.Choice(["llm", "deterministic"]),
    default=None,
    hidden=True,
    help="Deprecated: use --prose / --no-prose. 'llm' == --prose, 'deterministic' == --no-prose.",
)
@click.option(
    "--mode",
    "run_mode",
    type=click.Choice(["standard", "fast"]),
    default="standard",
    help=(
        "Pipeline depth. 'fast' is an index only: graph + essential git (no "
        "per-file blame, no co-change, no documentation) for a quick first pass "
        "on very large repos; backfill the rest later. Default: standard."
    ),
)
@click.option(
    "--exclude",
    "-x",
    multiple=True,
    metavar="PATTERN",
    help="Gitignore-style pattern to exclude. Can be repeated: -x vendor/ -x 'src/generated/**'",
)
@click.option(
    "--commit-limit",
    type=int,
    default=None,
    help="Max commits to analyze per file and for co-change detection (default: 5000, max: 10000). Saved to config.",
)
@click.option(
    "--follow-renames",
    is_flag=True,
    default=False,
    help="Use git log --follow to track files across renames (slower but more accurate history). Saved to config.",
)
@click.option(
    "--no-claude-md",
    "no_claude_md",
    is_flag=True,
    default=False,
    help="Skip generating CLAUDE.md. Saves 'editor_files.claude_md: false' to config.",
)
@click.option(
    "--agents/--no-agents",
    "agents_md",
    default=None,
    help="Generate managed AGENTS.md (default: config or enabled).",
)
@click.option(
    "--codex/--no-codex",
    "codex_setup",
    default=None,
    help="Generate or skip project-local Codex MCP config and hooks.",
)
@click.option(
    "--distill-hook/--no-distill-hook",
    "distill_hook",
    default=None,
    help=(
        "Let repowise's hooks compact what your agent sees. Noisy commands "
        "(tests, builds, git, searches) run through `repowise distill`; a "
        "whole-file Read comes back as a skeleton (`hooks.read_skeleton`); a "
        "grep matching many files comes back as a per-file digest "
        "(`hooks.search_digest`); re-reading an unchanged, unedited file comes "
        "back as a pointer to the earlier read (`hooks.read_reread`). One "
        "consent, and this flag decides all four without a prompt. Default: "
        "on. Interactive runs ask; --yes and non-interactive runs install it "
        "and print how to turn it off. A repo that opted out stays off, and "
        "--no-editor-setup installs nothing. In workspace mode the verdict "
        "applies to every selected repo."
    ),
)
@click.option(
    "--hook/--no-hook",
    "hook",
    default=None,
    help=(
        "Install the post-commit hook that runs `repowise update` after each "
        "commit, so the index stays current without anyone typing it. Default: "
        "on. Interactive runs ask; --yes and non-interactive runs install it and "
        "print how to undo it (`repowise hook uninstall`). --no-hook skips it. "
        "--no-editor-setup skips it too, since a git hook is a write outside "
        ".repowise/. In workspace mode the choice applies to every selected repo."
    ),
)
@click.option(
    "--editor-setup/--no-editor-setup",
    "editor_setup",
    default=True,
    help=(
        "Wire repowise into your editors. Covers both halves: the machine-wide "
        "config (the Claude Code / Claude Desktop MCP entry, the Claude Code "
        "hooks, the distill rewrite-hook offer) and the project-local files "
        "(.claude/CLAUDE.md, .vscode/mcp.json, .mcp.json when no user-scope "
        "entry or plugin already serves the repo, "
        ".vscode/extensions.json). Default: on. Use --no-editor-setup to index "
        "a repo without writing anything into it or outside it; only "
        ".repowise/ is touched, and 'repowise mcp .' still prints the config "
        "to connect a client by hand. REPOWISE_SKIP_EDITOR_SETUP=1 does the "
        "same thing for CI and sandboxes, and wins: with it set, --editor-setup "
        "does not turn setup back on."
    ),
)
@click.option(
    "--save-key/--no-save-key",
    "save_key",
    default=True,
    help=(
        "Save the provider API key this run authenticated with into "
        ".repowise/.env (gitignored, owner-only). Default: on, because a scripted "
        "init that succeeds must leave a repo whose MCP server can answer, "
        "and the key would otherwise be lost with the shell that set it. Use "
        "--no-save-key when the key is injected per-process (CI secrets, a "
        "shared machine) and must not be written to disk. "
        "REPOWISE_NO_SAVE_KEY=1 does the same for CI and sandboxes."
    ),
)
@click.option(
    "--include-submodules",
    is_flag=True,
    default=False,
    help="Include git submodule directories (excluded by default).",
)
@click.option(
    "--no-workspace",
    "no_workspace",
    is_flag=True,
    default=False,
    help=(
        "Force single-repo mode even when invoked from a workspace root. "
        "Indexes only the target PATH and skips workspace detection."
    ),
)
@click.option(
    "--all",
    "init_all",
    is_flag=True,
    default=False,
    help=(
        "In a folder with several repos, index every detected repo without "
        "prompting. --yes and a piped stdin do the same; use --repos to pick some."
    ),
)
@click.option(
    "--repos",
    "repos",
    default=None,
    metavar="NAMES",
    help=(
        "In a folder with several repos, index only these, with no picker. "
        "Comma-separated names (alias, directory name, or path relative to the "
        "folder when two repos share a name). In a new workspace the first one "
        "listed is primary; an existing workspace keeps its primary."
    ),
)
@click.option(
    "--onboarding/--no-onboarding",
    "onboarding",
    default=True,
    help=(
        "Generate the curated Onboarding collection (Project Overview, "
        "Architecture Guide, Getting Started, Codebase Map, Key Concepts, "
        "How It Works, Development Guide, Active Landscape). Default: on. "
        "Slots with insufficient signal are skipped automatically."
    ),
)
@click.option(
    "--max-file-pages",
    "max_file_pages_opt",
    type=int,
    default=None,
    metavar="N",
    help=(
        "Most file pages to emit, highest importance first. Omit to let the size "
        "policy decide: untouched on a normal repo, held to a ceiling on a very "
        "large one. Pass 0 for one page per eligible file however many that is. "
        "Saved to config.yaml so later runs keep it."
    ),
)
@click.option(
    "--folder-coverage",
    "folder_coverage_rules",
    multiple=True,
    metavar="GLOB=PCT",
    callback=_check_folder_coverage,
    help=(
        "Documentation floor for a folder: 'src/core=1.0' promises every code "
        "file under src/core gets a file page whatever its importance score; "
        "'src/legacy=0.5' promises half. Repeatable. Saved to config.yaml so "
        "later update runs keep the floors."
    ),
)
@click.option(
    "--coverage-report",
    "coverage_report",
    type=click.Path(exists=True, dir_okay=False),
    multiple=True,
    metavar="PATH",
    help=(
        f"Test-coverage report(s) to ingest ({' / '.join(COVERAGE_PARSERS)}). "
        "Repeatable. When omitted, common locations (coverage/lcov.info, "
        "**/cobertura.xml, ...) are auto-discovered. This is test coverage for "
        "code-health, not a documentation-breadth knob."
    ),
)
@click.option(
    "--wiki-style",
    "wiki_style",
    type=click.Choice([s.name for s in list_styles()]),
    default=None,
    help=(
        "Documentation voice/density for generated pages: "
        "comprehensive (default), caveman (token-condensed, AI-first), "
        "reference (API-manual), tutorial (beginner-friendly). When omitted in "
        "an interactive full run you'll be prompted; otherwise comprehensive."
    ),
)
@click.option(
    "--language",
    "language_opt",
    type=click.Choice(sorted(SUPPORTED_LANGUAGES)),
    default=None,
    metavar="CODE",
    help=(
        "Output language for the documentation (e.g. en, zh, ru, hi). "
        "Code, file paths, and symbol names stay untranslated. Saved to "
        "config so `update` keeps the language. Default: en."
    ),
)
@click.option(
    "--seed-from",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=str),
    help=(
        "Seed the index from an existing base-branch checkout to skip indexing "
        "unmodified files. Rarely needed: inside a linked git worktree the base "
        "checkout is detected and seeded automatically."
    ),
)
@click.option(
    "--no-seed",
    is_flag=True,
    default=False,
    help="Disable worktree auto-seeding and run a full init even inside a linked worktree.",
)
@click.option(
    "--no-cost-tracking",
    is_flag=True,
    default=False,
    help="Skip DB-backed LLM cost tracking for this run.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help=(
        "Show per-phase internals plus debug logs. Without it, debug/info "
        "logging is suppressed so the progress bar is the only output."
    ),
)
@click.option(
    "--progress",
    type=click.Choice(["rich", "json"]),
    default="rich",
    help=(
        "Progress output. 'rich' (default) draws a live checklist in a terminal "
        "and one short line per step anywhere else. 'json' writes one JSON event "
        "per line to stdout (stages, steps, warnings) and ends with a summary "
        "line: status, mode, restart_required, spend, warnings, files_written "
        "and next commands. Human text goes to stderr."
    ),
)
def init_command(**params: Any) -> None:
    """Index a codebase and build its documentation.

    PATH defaults to the current directory.
    --prose: documentation written by a model (needs a key).
    --no-prose: documentation built from code, no model and no key.
    Only the subsystem pages differ; every other page is built from code.
    --mode fast: index only (graph + essential git), for a very large repo.
    """
    from repowise.core.cancellation import PipelineCancelled

    with init_output(params.get("progress", "rich"), resolve_repo_path(params.get("path"))):
        try:
            _run_init(resolve_structural_flags(InitOptions(**params)))
        except (PipelineCancelled, KeyboardInterrupt):
            exit_interrupted()


def _run_init(opts: InitOptions) -> None:
    """The whole init, one repo or a workspace; exits 2 when it finishes with gaps."""
    start = time.monotonic()
    repo_path = resolve_repo_path(opts.path)

    if not repo_path.is_dir():
        raise reasoned_error(f"Not a directory: {repo_path}", reason="invalid_path")

    # ---- Workspace detection ----
    # If the path contains multiple git repos (and is not itself a single repo),
    # branch into the multi-repo workspace flow.  --no-workspace bypasses this
    # entirely and forces single-repo mode regardless of what scan finds.
    from repowise.core.workspace import scan_for_repos

    scan = scan_for_repos(repo_path, include_submodules=opts.include_submodules)

    is_workspace = len(scan.repos) > 1 and not opts.no_workspace
    check_repos_flag(opts.repos, init_all=opts.init_all, is_workspace=is_workspace)
    if delegate_to_seeded_update(opts, repo_path, scan):
        return
    if is_workspace:
        _run_workspace_init(opts, scan)
        return

    # If a single repo was found inside the given directory (not at root),
    # redirect to it so the user doesn't have to specify the exact path.
    if len(scan.repos) == 1 and scan.repos[0].path != repo_path:
        repo_path = scan.repos[0].path
    if not opts.no_workspace:
        suggest_workspace(repo_path)

    with init_in_progress(repo_path):
        _run_single_repo(opts, repo_path, start)


def _run_single_repo(opts: InitOptions, repo_path: Any, start: float) -> None:
    run = _prepare_single_repo(opts, repo_path, start)
    if run is None:
        return
    index_repository(run)
    # Each one is an analysis step the pipeline skipped on an error.
    if run.run_warnings:
        run.gaps.append(f"{len(run.run_warnings):,} warning(s) while indexing (see above)")

    if _generate(run):
        return

    # A declined cost gate or a missing provider persists like index-only.
    run.effective_index_only = run.opts.index_only or run.cost_declined or run.no_provider
    persist_index(run)
    register_clients(run)
    complete_setup(run)
    exit_with_gaps(run)
