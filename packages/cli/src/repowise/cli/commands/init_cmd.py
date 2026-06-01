"""``repowise init`` — full wiki generation for a repository."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from repowise.cli._setup import setup_logging_silence
from repowise.cli.cost_estimator import build_generation_plan, estimate_cost
from repowise.cli.editor_integrations.defaults import get_default_disabled_project_files
from repowise.cli.editor_setup import (
    register_editor_clients,
    resolve_editor_setup_options,
    write_editor_project_files,
)
from repowise.cli.helpers import (
    config_fingerprint,
    console,
    ensure_repowise_dir,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_config,
    save_config_partial,
    save_state,
)
from repowise.cli.providers import (
    build_cost_tracker,
    build_embedder,
    build_vector_store,
    flush_cost_tracker,
    resolve_embedder,
)
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.cli.ui import (
    BRAND,
    MaybeCountColumn,
    RichProgressCallback,
    build_analysis_summary_panel,
    build_completion_panel,
    build_contextual_next_steps,
    format_elapsed,
    interactive_advanced_config,
    interactive_fast_mode_offer,
    interactive_mode_select,
    interactive_primary_select,
    interactive_provider_select,
    interactive_repo_select,
    load_dotenv,
    print_banner,
    print_index_only_intro,
    print_phase_header,
    print_scan_summary,
    quick_repo_scan,
    should_offer_fast_mode,
)

# ---------------------------------------------------------------------------
# Helpers (kept in this file; _resolve_embedder also imported by other cmds)
# ---------------------------------------------------------------------------


class CostGateDeclined(Exception):
    """Raised when the user answers No at the LLM-cost confirmation prompt.

    Carries no payload — the caller just needs to know that generation was
    declined so it can persist state in index-only shape (no docs) and
    return cleanly. Using an exception (vs. a sentinel return value) lets
    us bail out of nested generation flows without rethreading return
    types through every helper.
    """


def _confirm_cost_gate(message: str) -> bool:
    """Render the cost-gate `[y/N]` prompt with visual padding.

    Click's plain ``confirm`` interleaves with the trailing line of any
    prior Rich output (progress-bar frames, status spinners), making the
    `[y/N]` glyphs hard to spot — users have walked past it and approved
    a $14 bill thinking they were still in cost-estimate territory. A
    blank line + horizontal rule cleanly separates the prompt from
    whatever was printed above it.
    """
    console.line()
    console.rule(style="yellow")
    return click.confirm(message, default=False)


def _offer_hook_install(
    console_obj: Any,
    repo_paths: list[Path],
    aliases: list[str] | None = None,
) -> None:
    """Interactively offer to install post-commit hooks for auto-sync.

    For a single repo, asks yes/no.  For multiple repos (workspace), lets the
    user pick which repos to install hooks for.
    """
    if not sys.stdin.isatty():
        return  # Non-interactive — skip

    from repowise.cli.hooks import install, status

    # Filter to repos that don't already have the hook
    candidates: list[tuple[Path, str]] = []
    for i, rp in enumerate(repo_paths):
        label = aliases[i] if aliases else rp.name
        if status(rp) != "installed":
            candidates.append((rp, label))

    if not candidates:
        return  # All already have hooks

    console_obj.print()
    console_obj.print(
        "[bold]Auto-sync:[/bold] Install a post-commit hook to keep the wiki "
        "in sync after every commit?"
    )

    if len(candidates) == 1:
        rp, label = candidates[0]
        if click.confirm(f"  Install post-commit hook for {label}?", default=True):
            result = install(rp)
            console_obj.print(f"  [green]✓[/green] {label}: {result}")
        else:
            console_obj.print("  [dim]Skipped. Run 'repowise hook install' later to set up.[/dim]")
    else:
        # Workspace: show checkboxes-style selection
        console_obj.print("  Select repos (enter numbers, comma-separated, or 'all'):")
        for i, (rp, label) in enumerate(candidates, 1):
            console_obj.print(f"    [{i}] {label}")

        raw = click.prompt(
            "  Repos",
            default="all",
            show_default=True,
        )
        if raw.strip().lower() == "all":
            selected_indices = list(range(len(candidates)))
        elif raw.strip().lower() in ("none", "skip", ""):
            selected_indices = []
        else:
            try:
                selected_indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
            except ValueError:
                selected_indices = []

        installed = 0
        for idx in selected_indices:
            if 0 <= idx < len(candidates):
                rp, label = candidates[idx]
                result = install(rp)
                console_obj.print(f"  [green]✓[/green] {label}: {result}")
                installed += 1

        if installed == 0:
            console_obj.print(
                "  [dim]Skipped. Run 'repowise hook install --workspace' later.[/dim]"
            )


# ``_resolve_embedder`` / ``_build_embedder`` now live in
# ``cli.providers.embedders``. They keep their leading-underscore aliases here
# because several sibling commands (update/reindex/search) import them from
# this module — the aliases preserve that import surface.
_resolve_embedder = resolve_embedder
_build_embedder = build_embedder


# ---------------------------------------------------------------------------
# Persistence — saves PipelineResult to SQLite
# ---------------------------------------------------------------------------


async def _persist_result(
    result: Any,
    repo_path: Path,
) -> None:
    """Persist a PipelineResult to the local SQLite database.

    Handles both index-only (no pages) and full (with pages + FTS) modes.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.pipeline import persist_pipeline_result

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    fts = None
    if result.generated_pages:
        fts = FullTextSearch(engine)
        await fts.ensure_index()

    async with get_session(sf) as session:
        repo = await upsert_repository(
            session,
            name=result.repo_name,
            local_path=str(repo_path),
        )
        # Persist the detected tech stack into the repository's settings
        # blob. Merge into any pre-existing settings so we don't clobber
        # unrelated state (workspace flags, etc.). Done here rather than
        # in upsert_repository so the persistence helper stays
        # signature-stable.
        if getattr(result, "tech_stack", None):
            import json as _json

            try:
                existing = _json.loads(repo.settings_json or "{}")
                if not isinstance(existing, dict):
                    existing = {}
            except Exception:
                existing = {}
            existing["tech_stack"] = result.tech_stack
            repo.settings_json = _json.dumps(existing)
        await persist_pipeline_result(result, session, repo.id)

        # Record a completed GenerationJob so the web UI can show
        # "last synced" / "last re-indexed" timestamps.
        from datetime import UTC as _UTC
        from datetime import datetime

        from repowise.core.persistence.crud import upsert_generation_job

        now = datetime.now(_UTC)
        page_count = len(result.generated_pages) if result.generated_pages else 0
        job = await upsert_generation_job(
            session,
            repository_id=repo.id,
            status="completed",
            total_pages=page_count,
            config={"mode": "full_resync", "source": "cli_init"},
        )
        job.completed_pages = page_count
        job.started_at = now
        job.finished_at = now

    # FTS indexing is done outside the session to avoid SQLite write conflicts
    if fts is not None and result.generated_pages:
        for page in result.generated_pages:
            await fts.index(page.page_id, page.title, page.content)

    await engine.dispose()


# ---------------------------------------------------------------------------
# Workspace generation helper (per-repo)
# ---------------------------------------------------------------------------


def _run_workspace_generation(
    *,
    repo_path: Path,
    result: Any,
    provider: Any,
    embedder_name_resolved: str,
    concurrency: int,
    yes: bool,
    resume: bool,
    skip_tests: bool,
    skip_infra: bool,
    test_run: bool,
    reasoning: str = "auto",
    onboarding: bool = True,
    coverage_pct: float | None = None,
    harvest_decisions: bool = True,
) -> list[Any]:
    """Run LLM generation for a single repo in the workspace init flow.

    Returns the list of generated pages.  Raises on unrecoverable errors so
    the caller can catch and log per-repo failures without aborting the whole
    workspace run.
    """
    from repowise.cli.cost_estimator import build_generation_plan, estimate_cost
    from repowise.cli.ui import RichProgressCallback
    from repowise.core.generation import GenerationConfig
    from repowise.core.pipeline import run_generation

    # Build embedder + vector store
    embedder_impl: Any = build_embedder(embedder_name_resolved)
    vector_store: Any = build_vector_store(repo_path, embedder_impl)

    # Stash the store on the result so persist's Phase-2C decision dedup
    # matches against — and embeds decisions into — the same store the pages
    # land in (surfacing decisions in search_codebase).
    result.vector_store = vector_store

    # Coverage chooser — interactive when TTY, falls back to the
    # ``coverage`` flag (or the default of 20%) for non-TTY / CI runs.
    # Computes per-option counts + costs from the live ingestion data
    # so the table never lies about what generation will produce.
    from repowise.cli.cost_estimator import compute_coverage_options
    from repowise.cli.coverage_select import interactive_coverage_select

    gen_config = GenerationConfig(
        max_concurrency=concurrency,
        reasoning=resolve_reasoning(reasoning),
        enable_onboarding=onboarding,
        harvest_decisions=harvest_decisions,
    )
    use_interactive_coverage = sys.stdin.isatty() and coverage_pct is None and not yes
    if use_interactive_coverage:
        options = compute_coverage_options(
            parsed_files=result.parsed_files,
            graph_builder=result.graph_builder,
            base_config=gen_config,
            provider_name=provider.provider_name,
            model_name=provider.model_name,
            repo_path=repo_path,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
        )
        chosen = interactive_coverage_select(console, options)
        chosen_pct = chosen.pct
        plans = chosen.plans
        est = chosen.estimate
    else:
        chosen_pct = coverage_pct if coverage_pct is not None else gen_config.coverage_pct
        from dataclasses import replace as _replace

        gen_config_for_plan = _replace(
            gen_config, coverage_pct=chosen_pct, max_pages_pct=chosen_pct
        )
        plans = build_generation_plan(
            result.parsed_files,
            result.graph_builder,
            gen_config_for_plan,
            skip_tests,
            skip_infra,
        )
        est = estimate_cost(
            plans,
            provider.provider_name,
            provider.model_name,
            repo_path=repo_path,
        )

    # Bake the chosen coverage into the gen_config that runs generation,
    # so the page generator's selection layer honors the user's pick.
    from dataclasses import replace as _replace_cfg

    gen_config = _replace_cfg(gen_config, coverage_pct=chosen_pct, max_pages_pct=chosen_pct)

    if est.cost_range is not None:
        cost_str = (
            f"${est.cost_range.low:.2f} - ${est.cost_range.high:.2f} USD "
            f"(median ${est.estimated_cost_usd:.2f})"
        )
        if est.is_calibrated:
            cost_str += " [calibrated]"
    else:
        cost_str = f"${est.estimated_cost_usd:.2f} USD"

    console.print(
        f"    Coverage: {int(chosen_pct * 100)}% / "
        f"~{est.estimated_input_tokens + est.estimated_output_tokens:,} tokens "
        f"({cost_str}, {est.total_pages} pages)"
    )

    if (
        est.estimated_cost_usd > 2.00
        and not yes
        and not _confirm_cost_gate(f"    Cost for {repo_path.name} exceeds $2.00. Continue?")
    ):
        console.print(
            "    [yellow]Skipped.[/yellow] "
            "[dim]Index will be saved without docs; "
            "future `repowise update` runs default to index-only.[/dim]"
        )
        # Sentinel — caller treats this exactly like an index-only run so
        # state.docs_enabled lands as False and the post-commit hook
        # doesn't surprise the user with LLM regen later.
        raise CostGateDeclined()

    # Cost tracker (DB-backed when possible)
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    cost_tracker = build_cost_tracker(repo_path, result.repo_name)
    provider._cost_tracker = cost_tracker

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        TextColumn("[green]${task.fields[cost]:.3f}[/green]"),
        console=console,
    ) as gen_progress:
        gen_callback = RichProgressCallback(gen_progress, console)

        generated_pages = run_async(
            run_generation(
                repo_path=repo_path,
                parsed_files=result.parsed_files,
                source_map=result.source_map,
                graph_builder=result.graph_builder,
                repo_structure=result.repo_structure,
                git_meta_map=result.git_meta_map,
                llm_client=provider,
                embedder=embedder_impl,
                vector_store=vector_store,
                concurrency=concurrency,
                progress=gen_callback,
                resume=resume,
                cost_tracker=cost_tracker,
                generation_config=gen_config,
            )
        )

    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None and provider is not None:
        from repowise.core.generation.knowledge_graph import enrich_knowledge_graph

        try:
            result.knowledge_graph_result = run_async(
                enrich_knowledge_graph(
                    kg_skeleton=kg,
                    llm_client=provider,
                    graph_builder=result.graph_builder,
                    repo_structure=result.repo_structure,
                    tech_stack=result.tech_stack,
                    generated_pages=generated_pages,
                    reasoning=gen_config.reasoning,
                )
            )
        except Exception:
            pass

    # Persist the buffered LLM cost rows in one transaction now that all LLM
    # work (generation + KG enrichment) is done — keeps cost writes out of the
    # contended generation window (issue #326).
    flush_cost_tracker(cost_tracker)

    return generated_pages


# ---------------------------------------------------------------------------
# Workspace init — multi-repo flow
# ---------------------------------------------------------------------------


@dataclass
class _WorkspaceCtx:
    """Run-wide settings shared across per-repo indexing in a workspace init."""

    provider: Any
    ws_config: Any
    editor_options: Any
    index_only: bool
    dry_run: bool
    force: bool
    follow_renames: bool
    include_submodules: bool
    exclude_patterns: list[str]
    skip_tests: bool
    skip_infra: bool
    concurrency: int
    test_run: bool
    yes: bool
    resume: bool
    onboarding: bool
    coverage_pct: float | None
    harvest_decisions: bool
    resolved_reasoning: str
    embedder_name_resolved: str
    resolved_commit_limit: int


@dataclass
class _RepoOutcome:
    """Result of indexing a single repo within a workspace."""

    error: str | None = None
    file_count: int = 0
    symbol_count: int = 0
    pages_generated: int = 0
    docs_outcome: tuple[int, str | None] = (0, None)


def _ingest_and_generate_repo(repo: Any, idx: int, total: int, ctx: _WorkspaceCtx) -> _RepoOutcome:
    """Index (and optionally generate docs for) one repo in a workspace init.

    Returns a :class:`_RepoOutcome`; on pipeline failure the outcome carries
    ``error`` and nothing is persisted (mirrors the per-repo try/except + continue
    the workspace loop used to do inline).
    """
    from datetime import datetime

    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline

    console.print(
        f"  [{BRAND}][{idx}/{total}][/] Indexing [bold]{repo.alias}[/bold] ({repo.path.name})..."
    )
    ensure_repowise_dir(repo.path)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MaybeCountColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress_bar:
            callback = PhaseTimingRecorder(RichProgressCallback(progress_bar, console))

            _prev_state = load_state(repo.path)
            _prev_kg_fp = (
                _prev_state.get("knowledge_graph", {}).get("fingerprint") if not ctx.force else None
            )

            result = run_async(
                run_pipeline(
                    repo.path,
                    commit_depth=ctx.resolved_commit_limit,
                    follow_renames=ctx.follow_renames,
                    exclude_patterns=ctx.exclude_patterns if ctx.exclude_patterns else None,
                    include_submodules=ctx.include_submodules,
                    generate_docs=False,
                    progress=callback,
                    existing_kg_fingerprint=_prev_kg_fp,
                )
            )
        repo_phase_timings: dict[str, float] = callback.timings
        console.print(
            f"    [green]✓[/green] {result.file_count} files, {result.symbol_count:,} symbols"
        )
    except Exception as exc:
        console.print(f"    [red]✗ Failed: {exc}[/red]\n")
        return _RepoOutcome(error=str(exc))

    provider = ctx.provider
    index_only = ctx.index_only

    # Generation phase (per-repo, only when not index-only).
    # Track per-repo whether the user declined cost so state.docs_enabled
    # reflects the actual choice instead of the original init mode.
    repo_docs_enabled = not index_only and provider is not None
    skip_reason: str | None = None
    if index_only:
        skip_reason = "index-only mode"
    elif provider is None:
        skip_reason = "no provider configured"
    pages_generated = 0
    if not index_only and provider is not None:
        if ctx.dry_run:
            console.print("    [yellow]Dry run — skipping generation for this repo.[/yellow]\n")
            skip_reason = "dry run"
            repo_docs_enabled = False
        else:
            try:
                generated_pages = _run_workspace_generation(
                    repo_path=repo.path,
                    result=result,
                    provider=provider,
                    embedder_name_resolved=ctx.embedder_name_resolved,
                    concurrency=ctx.concurrency,
                    yes=ctx.yes,
                    resume=ctx.resume,
                    skip_tests=ctx.skip_tests,
                    skip_infra=ctx.skip_infra,
                    test_run=ctx.test_run,
                    reasoning=ctx.resolved_reasoning,
                    onboarding=ctx.onboarding,
                    coverage_pct=ctx.coverage_pct,
                    harvest_decisions=ctx.harvest_decisions,
                )
                result.generated_pages = generated_pages
                # (result.vector_store is set inside _run_workspace_generation
                # so the Phase-2C decision dedup can reuse the same store.)
                pages_generated = len(generated_pages)
                console.print(f"    [green]✓[/green] Generated {len(generated_pages)} pages\n")
            except CostGateDeclined:
                repo_docs_enabled = False
                result.generated_pages = []
                skip_reason = "cost gate declined"
            except Exception as gen_exc:
                console.print(f"    [yellow]Generation failed: {gen_exc}[/yellow]\n")
                skip_reason = f"generation error: {gen_exc}"
                repo_docs_enabled = False
    else:
        console.print()

    docs_outcome = (
        len(result.generated_pages or []),
        None if repo_docs_enabled else skip_reason,
    )

    # Persist to repo-local DB
    run_async(_persist_result(result, repo.path))

    # Write state.json so `repowise update` knows the base commit
    head = get_head_commit(repo.path)
    pages_count = len(result.generated_pages or [])
    state: dict[str, Any] = {
        "last_sync_commit": head,
        "total_pages": pages_count,
        "docs_enabled": repo_docs_enabled,
    }
    if repo_docs_enabled and provider is not None:
        state["provider"] = provider.provider_name
        state["model"] = provider.model_name
    if repo_phase_timings:
        state["phase_timings"] = repo_phase_timings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        state["knowledge_graph"] = build_kg_state(kg)
    save_state(repo.path, state)

    if kg is not None:
        save_knowledge_graph_json(repo.path, kg)

    # Update workspace config with indexing metadata
    entry = ctx.ws_config.get_repo(repo.alias)
    if entry is not None:
        entry.indexed_at = datetime.now(UTC).isoformat()
        entry.last_commit_at_index = head

    # MCP config + editor setup files per repo
    write_editor_project_files(
        console,
        repo.path,
        options=ctx.editor_options,
    )

    # Persist provider/model config per-repo when doing full generation
    if not index_only and provider is not None:
        save_config(
            repo.path,
            provider.provider_name,
            provider.model_name,
            ctx.embedder_name_resolved,
            exclude_patterns=ctx.exclude_patterns if ctx.exclude_patterns else None,
            commit_limit=ctx.resolved_commit_limit,
            reasoning=ctx.resolved_reasoning,
        )

    return _RepoOutcome(
        file_count=result.file_count,
        symbol_count=result.symbol_count,
        pages_generated=pages_generated,
        docs_outcome=docs_outcome,
    )


def _run_cross_repo_analysis(ws_config: Any, root: Any, selected: list[Any], errors: list) -> None:
    """Run cross-repo analysis (co-changes, package deps, contracts) when ≥2 repos indexed."""
    indexed_aliases = [repo.alias for repo in selected if repo.alias not in [e[0] for e in errors]]
    if len(indexed_aliases) >= 2:
        console.print("  Running cross-repo analysis...")
        try:
            from repowise.core.workspace.update import run_cross_repo_hooks

            run_async(run_cross_repo_hooks(ws_config, root, indexed_aliases))
            console.print("  [green]✓[/green] Cross-repo analysis complete")
        except Exception as exc:
            console.print(f"  [yellow]⚠ Cross-repo analysis failed: {exc}[/yellow]")


def _show_workspace_completion(
    *,
    selected: list[Any],
    errors: list,
    total_files: int,
    total_symbols: int,
    total_pages: int,
    primary_alias: str,
    elapsed: float,
    index_only: bool,
    provider: Any,
    docs_outcomes: dict[str, tuple[int, str | None]],
) -> None:
    """Render the workspace completion panel + per-repo docs status."""
    metrics: list[tuple[str, str]] = [
        ("Repositories", f"{len(selected) - len(errors)} indexed"),
        ("Total files", str(total_files)),
        ("Total symbols", f"{total_symbols:,}"),
        ("Primary repo", primary_alias),
        ("Elapsed", format_elapsed(elapsed)),
    ]
    if not index_only and provider is not None:
        metrics.insert(3, ("Pages generated", str(total_pages)))
        metrics.insert(4, ("Provider", f"{provider.provider_name} / {provider.model_name}"))
    if errors:
        metrics.append(("Errors", f"{len(errors)} repos failed"))

    if index_only or provider is None:
        next_steps = [
            ("repowise mcp <repo-path>", "start MCP server for a repo"),
            ("repowise status --workspace", "show workspace status"),
            ("repowise init <repo> --provider gemini", "generate full docs for a repo"),
        ]
    else:
        next_steps = [
            ("repowise mcp <repo-path>", "start MCP server for a repo"),
            ("repowise status --workspace", "show workspace status"),
            ("repowise search <query>", "search across all indexed repos"),
        ]

    console.print()
    console.print(
        build_completion_panel("repowise workspace init complete", metrics, next_steps=next_steps)
    )
    console.print()

    # Honest docs status — print a per-repo summary listing exactly which
    # repos generated pages and which were skipped, so the user never has
    # to discover empty Docs/Overview in the web UI on their own.
    docs_skipped = [(alias, reason) for alias, (count, reason) in docs_outcomes.items() if reason]
    if docs_outcomes:
        console.print("[bold]Docs status[/bold]")
        for alias, (count, reason) in docs_outcomes.items():
            if reason:
                console.print(
                    f"  [yellow]✗[/yellow] {alias:<20} [yellow]skipped[/yellow]  [dim]({reason})[/dim]"
                )
            else:
                console.print(f"  [green]✓[/green] {alias:<20} [green]{count} pages[/green]")
        if docs_skipped:
            first = docs_skipped[0][0]
            console.print()
            console.print(
                f"  Run [bold]repowise update --repo {first} --docs[/bold] "
                "to generate docs for a skipped repo."
            )
        console.print()


def _workspace_init(
    *,
    scan: Any,
    init_all: bool,
    exclude_patterns: list[str],
    commit_limit: int | None,
    follow_renames: bool,
    no_claude_md: bool,
    include_submodules: bool,
    # Generation params (passed through from init_command)
    provider_name: str | None = None,
    model: str | None = None,
    embedder_name: str | None = None,
    index_only: bool = False,
    skip_tests: bool = False,
    skip_infra: bool = False,
    concurrency: int = 5,
    test_run: bool = False,
    reasoning: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
    resume: bool = False,
    force: bool = False,
    onboarding: bool = True,
    coverage_pct: float | None = None,
    harvest_decisions: bool = True,
) -> None:
    """Multi-repo workspace initialization.

    Detects repos, prompts for selection and primary, creates a workspace
    config, then runs ingestion on each repo.  When the user selects full or
    advanced mode (interactively) or passes an explicit provider, also runs
    LLM generation per repo.
    """
    setup_logging_silence()

    from repowise.core.workspace import RepoEntry, WorkspaceConfig

    start = time.monotonic()
    root = scan.root

    print_banner(console, repo_name=f"Workspace: {root.name}")
    console.print(f"  Detected [bold]{len(scan.repos)}[/bold] repositories in {root}\n")

    # Step 1: Select repos to index
    if init_all:
        selected = list(scan.repos)
    else:
        selected = interactive_repo_select(console, scan.repos)

    if not selected:
        console.print("[yellow]No repositories selected. Aborting.[/yellow]")
        return

    # Step 2: Select primary repo
    if init_all:
        primary_alias = selected[0].alias
    else:
        primary_alias = interactive_primary_select(console, selected)

    # Determine root path (for provider resolution + dotenv)
    primary_repo = next((r for r in selected if r.alias == primary_alias), selected[0])
    load_dotenv(primary_repo.path)

    # Step 2b: Mode selection + provider setup
    # When running interactively with no explicit flags, present the mode menu.
    is_interactive = sys.stdin.isatty() and provider_name is None and not index_only

    embedder_name_resolved = _resolve_embedder(embedder_name)

    if is_interactive:
        mode = interactive_mode_select(console)
        if mode == "index_only":
            index_only = True
        elif mode == "advanced":
            provider_name, model = interactive_provider_select(
                console, model, repo_path=primary_repo.path
            )
            adv = interactive_advanced_config(console)
            commit_limit = adv.get("commit_limit") or commit_limit
            follow_renames = adv.get("follow_renames", follow_renames)
            skip_tests = adv.get("skip_tests", skip_tests)
            skip_infra = adv.get("skip_infra", skip_infra)
            concurrency = adv.get("concurrency", concurrency)
            if adv.get("exclude"):
                exclude_patterns = list(exclude_patterns) + list(adv["exclude"])
            test_run = adv.get("test_run", test_run)
            reasoning = adv.get("reasoning") or reasoning
            embedder_name_resolved = _resolve_embedder(adv.get("embedder") or embedder_name)
        elif not index_only:
            # "full" mode
            provider_name, model = interactive_provider_select(
                console, model, repo_path=primary_repo.path
            )

    # Resolve provider once (shared across all repos for generation)
    primary_cfg = load_config(primary_repo.path)
    resolved_reasoning = resolve_reasoning(reasoning, primary_cfg)
    provider = None
    if not index_only:
        try:
            provider = resolve_provider(provider_name, model, primary_repo.path)
            # Re-resolve the embedder now that interactive_provider_select
            # may have set the provider's API key in os.environ. Without
            # this, full-mode runs would display "mock" forever because
            # the initial resolution happened before the key was available.
            embedder_name_resolved = _resolve_embedder(embedder_name)
            console.print(
                f"  Provider: [cyan]{provider.provider_name}[/cyan] / "
                f"Model: [cyan]{provider.model_name}[/cyan]"
            )
            console.print(f"  Embedder: [cyan]{embedder_name_resolved}[/cyan]\n")
            if resolved_reasoning != "auto":
                console.print(f"  Reasoning: [cyan]{resolved_reasoning}[/cyan]\n")
        except Exception as exc:
            console.print(
                f"  [yellow]Provider setup failed ({exc}); falling back to index-only.[/yellow]"
            )
            index_only = True
            provider = None

    # Step 3: Create workspace config
    entries = [
        RepoEntry(
            path=repo.path.relative_to(root).as_posix(),
            alias=repo.alias,
            is_primary=(repo.alias == primary_alias),
        )
        for repo in selected
    ]
    ws_config = WorkspaceConfig(
        version=1,
        repos=entries,
        default_repo=primary_alias,
    )
    config_path = ws_config.save(root)
    console.print(f"  [green]\u2713[/green] Created {config_path.name}")
    console.print()

    # Step 4: Index each selected repo (always generate_docs=False; generation is separate)
    resolved_commit_limit = max(1, min(commit_limit or 500, 5000))
    total_files = 0
    total_symbols = 0
    total_pages = 0
    errors: list[tuple[str, str]] = []
    # Per-repo docs outcome, surfaced in the completion panel so the user
    # never has to guess why the web UI is missing pages for some repos.
    # Maps alias -> (generated_count, skip_reason | None)
    docs_outcomes: dict[str, tuple[int, str | None]] = {}
    editor_options = resolve_editor_setup_options(
        console,
        disabled_project_files=get_default_disabled_project_files(
            no_claude_md=no_claude_md,
        ),
    )

    ctx = _WorkspaceCtx(
        provider=provider,
        ws_config=ws_config,
        editor_options=editor_options,
        index_only=index_only,
        dry_run=dry_run,
        force=force,
        follow_renames=follow_renames,
        include_submodules=include_submodules,
        exclude_patterns=exclude_patterns,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        concurrency=concurrency,
        test_run=test_run,
        yes=yes,
        resume=resume,
        onboarding=onboarding,
        coverage_pct=coverage_pct,
        harvest_decisions=harvest_decisions,
        resolved_reasoning=resolved_reasoning,
        embedder_name_resolved=embedder_name_resolved,
        resolved_commit_limit=resolved_commit_limit,
    )

    for i, repo in enumerate(selected, 1):
        outcome = _ingest_and_generate_repo(repo, i, len(selected), ctx)
        if outcome.error:
            errors.append((repo.alias, outcome.error))
            continue
        total_files += outcome.file_count
        total_symbols += outcome.symbol_count
        total_pages += outcome.pages_generated
        docs_outcomes[repo.alias] = outcome.docs_outcome

    # Save workspace config with updated timestamps
    ws_config.save(root)

    # Step 5: Cross-repo analysis (co-changes, package deps, contracts)
    _run_cross_repo_analysis(ws_config, root, selected, errors)

    # Step 6: Register primary repo with configured editor clients
    primary_entry = ws_config.get_primary()
    if primary_entry:
        primary_path = (root / primary_entry.path).resolve()
        register_editor_clients(console, primary_path)

    # Step 7: Completion summary
    elapsed = time.monotonic() - start
    _show_workspace_completion(
        selected=selected,
        errors=errors,
        total_files=total_files,
        total_symbols=total_symbols,
        total_pages=total_pages,
        primary_alias=primary_alias,
        elapsed=elapsed,
        index_only=index_only,
        provider=provider,
        docs_outcomes=docs_outcomes,
    )

    # Offer to install post-commit hooks
    indexed_repos = [repo for repo in selected if repo.alias not in [e[0] for e in errors]]
    if indexed_repos:
        _offer_hook_install(
            console,
            [r.path for r in indexed_repos],
            aliases=[r.alias for r in indexed_repos],
        )
    console.print()


# ---------------------------------------------------------------------------
# Single-repo init — phase helpers (called by init_command)
# ---------------------------------------------------------------------------


def _show_analysis_summary(console: Any, result: Any) -> None:
    """Render the analysis-complete interstitial shown before generation."""
    _graph = result.graph_builder.graph()
    _dc_unreachable_pre = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unreachable_file"
    )
    _dc_unused_pre = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unused_export"
    )
    _dc_lines_pre = result.dead_code_report.deletable_lines if result.dead_code_report else 0
    _n_decisions_pre = (
        sum(result.decision_report.by_source.values()) if result.decision_report else 0
    )
    _lang_dist = result.repo_structure.root_language_distribution
    _lang_summary = ""
    if _lang_dist:
        _top = sorted(_lang_dist.items(), key=lambda x: -x[1])[:4]
        _lang_summary = ", ".join(f"{lang} {pct:.0%}" for lang, pct in _top)
        if len(_lang_dist) > 4:
            _lang_summary += f" +{len(_lang_dist) - 4} more"

    # Community count (best-effort)
    _community_count = 0
    try:
        if hasattr(result.graph_builder, "communities"):
            _community_count = len(result.graph_builder.communities())
    except Exception:
        pass

    console.print()
    console.print(
        build_analysis_summary_panel(
            file_count=result.file_count,
            symbol_count=result.symbol_count,
            graph_nodes=_graph.number_of_nodes(),
            graph_edges=_graph.number_of_edges(),
            dead_unreachable=_dc_unreachable_pre,
            dead_unused=_dc_unused_pre,
            dead_lines=_dc_lines_pre,
            decision_count=_n_decisions_pre,
            git_files=result.git_summary.files_indexed if result.git_summary else 0,
            hotspot_count=result.git_summary.hotspots
            if result.git_summary and hasattr(result.git_summary, "hotspots")
            else 0,
            community_count=_community_count,
            lang_summary=_lang_summary,
        )
    )


def _run_generation_phase(
    *,
    repo_path: Path,
    result: Any,
    provider: Any,
    total_phases: int,
    concurrency: int,
    language: str,
    resolved_reasoning: str,
    onboarding: bool,
    tier1_top_n: int | None,
    harvest_decisions: bool,
    coverage_pct: float | None,
    yes: bool,
    dry_run: bool,
    skip_tests: bool,
    skip_infra: bool,
    embedder_name_resolved: str,
    resume: bool,
) -> tuple[bool, bool]:
    """Run the LLM generation phase for a single-repo init.

    Returns ``(stop, cost_declined)``: ``stop`` is True when this was a dry run
    and the caller should return immediately; ``cost_declined`` is True when the
    user declined the cost gate (generation skipped, index still saved). Mutates
    ``result`` in place with the generated pages, vector store, and enriched KG.
    """
    print_phase_header(
        console,
        3,
        total_phases,
        "Generation",
        f"Generating wiki pages with {provider.provider_name} / {provider.model_name}",
    )

    # Cost estimation + coverage selection. The coverage chooser
    # is rendered interactively when stdin is a TTY and no explicit
    # ``--coverage`` flag was passed; otherwise the configured
    # percentage drives a single non-interactive estimate.
    from dataclasses import replace as _replace_cfg

    from repowise.cli.cost_estimator import compute_coverage_options
    from repowise.cli.coverage_select import interactive_coverage_select
    from repowise.core.generation import GenerationConfig

    gen_config = GenerationConfig(
        max_concurrency=concurrency,
        language=language,
        reasoning=resolved_reasoning,
        enable_onboarding=onboarding,
        tier1_top_n=tier1_top_n,
        harvest_decisions=harvest_decisions,
    )
    if sys.stdin.isatty() and coverage_pct is None and not yes:
        options = compute_coverage_options(
            parsed_files=result.parsed_files,
            graph_builder=result.graph_builder,
            base_config=gen_config,
            provider_name=provider.provider_name,
            model_name=provider.model_name,
            repo_path=repo_path,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
        )
        chosen = interactive_coverage_select(console, options)
        chosen_pct = chosen.pct
        plans = chosen.plans
        est = chosen.estimate
    else:
        chosen_pct = coverage_pct if coverage_pct is not None else gen_config.coverage_pct
        gen_config_for_plan = _replace_cfg(
            gen_config, coverage_pct=chosen_pct, max_pages_pct=chosen_pct
        )
        plans = build_generation_plan(
            result.parsed_files,
            result.graph_builder,
            gen_config_for_plan,
            skip_tests,
            skip_infra,
        )
        est = estimate_cost(
            plans,
            provider.provider_name,
            provider.model_name,
            repo_path=repo_path,
        )

    gen_config = _replace_cfg(gen_config, coverage_pct=chosen_pct, max_pages_pct=chosen_pct)

    table = Table(title="Generation Plan", border_style=BRAND)
    table.add_column("Page Type", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Level", justify="right")
    for plan in est.plans:
        table.add_row(plan.page_type, str(plan.count), str(plan.level))
    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{est.total_pages}[/bold]", "")
    console.print(table)

    # Language breakdown
    lang_dist = result.repo_structure.root_language_distribution
    if lang_dist:
        lang_items = sorted(lang_dist.items(), key=lambda x: -x[1])[:6]
        lang_parts = [f"{lang} {pct:.0%}" for lang, pct in lang_items]
        console.print(f"  Languages: {', '.join(lang_parts)}")

    if est.cost_range is not None:
        cost_str = (
            f"${est.cost_range.low:.2f} - ${est.cost_range.high:.2f} USD "
            f"(median ${est.estimated_cost_usd:.2f})"
        )
        if est.is_calibrated:
            cost_str += " [calibrated]"
    else:
        cost_str = f"${est.estimated_cost_usd:.2f} USD"

    console.print(
        f"  Coverage: {int(chosen_pct * 100)}% / "
        f"~{est.estimated_input_tokens + est.estimated_output_tokens:,} tokens "
        f"({cost_str})"
    )
    if onboarding:
        console.print(
            "  [cyan]Onboarding collection:[/cyan] "
            "[dim]up to 8 curated pages — Project Overview, Architecture Guide, "
            "Getting Started, Codebase Map, Key Concepts, How It Works, "
            "Development Guide, Active Landscape "
            "(slots without enough signal are skipped).[/dim]"
        )
    else:
        console.print("  [dim]Onboarding collection: disabled (--no-onboarding).[/dim]")
    console.print()

    if dry_run:
        console.print("[yellow]Dry run — no pages generated.[/yellow]")
        return True, False

    cost_declined = (
        est.estimated_cost_usd > 2.00
        and not yes
        and not _confirm_cost_gate("  Estimated cost exceeds $2.00. Continue?")
    )
    if cost_declined:
        console.print(
            "[yellow]Skipped LLM generation.[/yellow] "
            "[dim]Index/graph/git/dead-code will be saved; future "
            "`repowise update` runs default to index-only so the "
            "post-commit hook won't trigger LLM regen.[/dim]"
        )

    if not cost_declined:
        # Build embedder + vector store
        embedder_impl: Any = build_embedder(embedder_name_resolved)
        vector_store: Any = build_vector_store(repo_path, embedder_impl)

        # Run generation via the pipeline's generation function
        from repowise.core.pipeline import run_generation

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MaybeCountColumn(),
            TimeElapsedColumn(),
            TextColumn("[green]${task.fields[cost]:.3f}[/green]"),
            console=console,
        ) as gen_progress:
            gen_callback = RichProgressCallback(gen_progress, console)

            # Construct a CostTracker backed by the real DB so every LLM call
            # is persisted to the llm_costs table.  The engine stays open for
            # the duration of generation and is disposed by _persist_result's
            # own engine later. Falls back to an in-memory tracker on failure.
            cost_tracker = build_cost_tracker(repo_path, result.repo_name)

            # Attach tracker to provider unconditionally (all providers now
            # accept _cost_tracker as an attribute)
            provider._cost_tracker = cost_tracker

            generated_pages = run_async(
                run_generation(
                    repo_path=repo_path,
                    parsed_files=result.parsed_files,
                    source_map=result.source_map,
                    graph_builder=result.graph_builder,
                    repo_structure=result.repo_structure,
                    git_meta_map=result.git_meta_map,
                    llm_client=provider,
                    embedder=embedder_impl,
                    vector_store=vector_store,
                    concurrency=concurrency,
                    progress=gen_callback,
                    resume=resume,
                    cost_tracker=cost_tracker,
                    generation_config=gen_config,
                )
            )

        result.generated_pages = generated_pages
        # Thread the shared vector store onto the result so the decision
        # semantic-dedup pass (Phase 2C) matches against it + embeds
        # decisions into it (also surfacing them in search_codebase).
        result.vector_store = vector_store
        console.print(f"  [green]✓[/green] Generated [bold]{len(generated_pages)}[/bold] pages")

        kg = getattr(result, "knowledge_graph_result", None)
        if kg is not None:
            from repowise.core.generation.knowledge_graph import enrich_knowledge_graph

            with console.status("  Enriching knowledge graph (layers + tour)…", spinner="dots"):
                try:
                    result.knowledge_graph_result = run_async(
                        enrich_knowledge_graph(
                            kg_skeleton=kg,
                            llm_client=provider,
                            graph_builder=result.graph_builder,
                            repo_structure=result.repo_structure,
                            tech_stack=result.tech_stack,
                            generated_pages=generated_pages,
                            reasoning=gen_config.reasoning,
                        )
                    )
                    _enriched_kg = result.knowledge_graph_result
                    console.print(
                        f"  [green]✓[/green] KG enriched: "
                        f"{len(_enriched_kg.layers)} layers, "
                        f"{len(_enriched_kg.tour)} tour steps"
                    )
                except Exception as _kg_enrich_err:
                    console.print(f"  [yellow]KG enrichment skipped: {_kg_enrich_err}[/yellow]")

        # Persist the buffered LLM cost rows in one transaction now that all LLM
        # work (generation + KG enrichment) is done — keeps cost writes out of
        # the contended generation window (issue #326).
        flush_cost_tracker(cost_tracker)

    return False, cost_declined


def _save_full_state_and_config(
    *,
    repo_path: Path,
    result: Any,
    provider: Any,
    phase_timings: dict[str, float],
    embedder_name_resolved: str,
    exclude_patterns: list[str],
    commit_limit: int | None,
    resolved_commit_limit: int,
    resolved_reasoning: str,
) -> None:
    """Persist state.json + config for a completed full-mode (docs) init run."""

    async def _count_db_pages() -> int:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from repowise.cli.helpers import get_db_url_for_repo as _get_url
        from repowise.core.persistence import create_engine, create_session_factory, get_session
        from repowise.core.persistence.models import Page, Repository

        _engine = create_engine(_get_url(repo_path))
        _sf = create_session_factory(_engine)
        async with get_session(_sf) as _sess:
            repo_result = await _sess.execute(
                sa_select(Repository.id).where(Repository.local_path == str(repo_path))
            )
            _repo_id = repo_result.scalar_one_or_none()
            if _repo_id is None:
                await _engine.dispose()
                return len(result.generated_pages or [])

            count_result = await _sess.execute(
                sa_select(sa_func.count()).select_from(Page).where(Page.repository_id == _repo_id)
            )
            count = count_result.scalar_one()
        await _engine.dispose()
        return count

    head = get_head_commit(repo_path)
    state = load_state(repo_path)
    state["last_sync_commit"] = head
    state["total_pages"] = run_async(_count_db_pages())
    state["provider"] = provider.provider_name
    state["model"] = provider.model_name
    state["docs_enabled"] = True
    total_tokens = sum(p.total_tokens for p in (result.generated_pages or []))
    state["total_tokens"] = total_tokens
    if phase_timings:
        state["phase_timings"] = phase_timings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        state["knowledge_graph"] = build_kg_state(kg)
    save_state(repo_path, state)

    if kg is not None:
        save_knowledge_graph_json(repo_path, kg)

    save_config(
        repo_path,
        provider.provider_name,
        provider.model_name,
        embedder_name_resolved,
        exclude_patterns=exclude_patterns if exclude_patterns else None,
        commit_limit=resolved_commit_limit if commit_limit is not None else None,
        reasoning=resolved_reasoning,
    )

    # Re-save state with the fingerprint now that config.yaml is written.
    state["config_fingerprint"] = config_fingerprint(repo_path)
    save_state(repo_path, state)


def _show_completion(
    *,
    repo_path: Path,
    result: Any,
    start: float,
    effective_index_only: bool,
    run_mode: str,
    provider: Any,
) -> None:
    """Render the final completion panel (index-only or full mode)."""
    elapsed = time.monotonic() - start

    _graph_final = result.graph_builder.graph()
    _dc_unreachable = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unreachable_file"
    )
    _dc_unused = sum(
        1
        for f in (result.dead_code_report.findings if result.dead_code_report else [])
        if f.kind.value == "unused_export"
    )
    _n_decisions = sum(result.decision_report.by_source.values()) if result.decision_report else 0
    _hotspot_count_final = (
        result.git_summary.hotspots
        if result.git_summary and hasattr(result.git_summary, "hotspots")
        else 0
    )

    # Find top hotspot file for contextual next steps
    _top_hotspot = ""
    if result.git_meta_map:
        _by_churn = sorted(
            result.git_meta_map.items(),
            key=lambda x: x[1].get("commit_count", 0),
            reverse=True,
        )
        if _by_churn:
            _top_hotspot = _by_churn[0][0]
            # Shorten to basename for display
            if "/" in _top_hotspot:
                _top_hotspot = _top_hotspot.rsplit("/", 1)[-1]

    # Build a compact language summary for the completion panel
    _lang_dist_final = result.repo_structure.root_language_distribution
    if _lang_dist_final:
        _top_final = sorted(_lang_dist_final.items(), key=lambda x: -x[1])[:4]
        _lang_summary_final = ", ".join(f"{lang} {pct:.0%}" for lang, pct in _top_final)
        if len(_lang_dist_final) > 4:
            _lang_summary_final += f" +{len(_lang_dist_final) - 4} more"
    else:
        _lang_summary_final = str(len(result.languages))

    if effective_index_only:
        metrics: list[tuple[str, str]] = [
            ("Files indexed", str(result.file_count)),
            ("Symbols", f"{result.symbol_count:,}"),
            ("Languages", _lang_summary_final),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            (
                "Graph",
                f"{_graph_final.number_of_nodes()} nodes · {_graph_final.number_of_edges()} edges",
            ),
            ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
            ("Decisions", str(_n_decisions)),
        ]
        if result.git_summary:
            metrics.append(
                (
                    "Git history",
                    f"{result.git_summary.files_indexed} files · {_hotspot_count_final} hotspots",
                )
            )

        next_steps = build_contextual_next_steps(
            index_only=True,
            fast_mode=(run_mode == "fast"),
            dead_unreachable=_dc_unreachable,
            dead_unused=_dc_unused,
            hotspot_count=_hotspot_count_final,
            decision_count=_n_decisions,
            top_hotspot=_top_hotspot,
        )
        console.print()
        console.print(
            build_completion_panel("repowise index complete", metrics, next_steps=next_steps)
        )
        console.print()
    else:
        total_tokens = sum(p.total_tokens for p in (result.generated_pages or []))
        metrics = [
            ("Pages generated", str(len(result.generated_pages or []))),
            ("Total tokens", f"{total_tokens:,}"),
            ("Provider", f"{provider.provider_name} / {provider.model_name}"),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
            ("Decisions", str(_n_decisions)),
        ]
        if result.git_summary:
            metrics.append(
                (
                    "Git history",
                    f"{result.git_summary.files_indexed} files · {_hotspot_count_final} hotspots",
                )
            )

        next_steps = build_contextual_next_steps(
            index_only=False,
            dead_unreachable=_dc_unreachable,
            dead_unused=_dc_unused,
            hotspot_count=_hotspot_count_final,
            decision_count=_n_decisions,
            top_hotspot=_top_hotspot,
        )

        from repowise.cli.mcp_config import format_setup_instructions

        console.print()
        console.print(
            build_completion_panel("repowise init complete", metrics, next_steps=next_steps)
        )
        console.print()
        console.print(format_setup_instructions(repo_path))
        console.print()


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command("init")
@click.argument("path", required=False, default=None)
@click.option(
    "--provider",
    "provider_name",
    default=None,
    help=(
        "LLM provider name (anthropic, openai, openrouter, gemini, "
        "deepseek, ollama, litellm, mock)."
    ),
)
@click.option("--model", default=None, help="Model identifier override.")
@click.option(
    "--embedder",
    "embedder_name",
    default=None,
    type=click.Choice(["gemini", "openai", "openrouter", "ollama", "mock"]),
    help="Embedder for RAG: gemini | openai | openrouter | ollama | mock (default: auto-detect).",
)
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files.")
@click.option("--skip-infra", is_flag=True, default=False, help="Skip infrastructure files.")
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show generation plan without running."
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip cost confirmation prompt.")
@click.option("--resume", is_flag=True, default=False, help="Resume from last checkpoint.")
@click.option(
    "--force", is_flag=True, default=False, help="Regenerate all pages, ignoring existing."
)
@click.option("--concurrency", type=int, default=5, help="Max concurrent LLM calls.")
@click.option(
    "--reasoning",
    type=click.Choice(["auto", "off", "minimal"]),
    default=None,
    help="Reasoning mode for supported providers: auto, off, or minimal. Default: auto.",
)
@click.option(
    "--test-run",
    is_flag=True,
    default=False,
    help="Limit generation to top 10 files by PageRank for quick validation.",
)
@click.option(
    "--index-only",
    is_flag=True,
    default=False,
    help="Index files, git history, graph, and dead code — skip LLM page generation.",
)
@click.option(
    "--mode",
    "run_mode",
    type=click.Choice(["standard", "fast"]),
    default="standard",
    help=(
        "Pipeline depth. 'fast' indexes graph + essential git only (no per-file "
        "blame, no co-change, no LLM docs) for a quick first pass on very large "
        "repos; backfill the rest later. Default: standard."
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
    help="Max commits to analyze per file and for co-change detection (default: 500, max: 5000). Saved to config.",
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
    "--include-submodules",
    is_flag=True,
    default=False,
    help="Include git submodule directories (excluded by default).",
)
@click.option(
    "--all",
    "init_all",
    is_flag=True,
    default=False,
    help="In multi-repo mode, index all detected repos without prompting.",
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
    "--coverage",
    "coverage_pct",
    type=float,
    default=None,
    metavar="PCT",
    help=(
        "Documentation coverage as a fraction of repo files (e.g. 0.10, 0.20, "
        "0.50). Bypasses the interactive coverage chooser. Default when "
        "interactive: prompt; otherwise 0.20."
    ),
)
@click.option(
    "--harvest-decisions/--no-harvest-decisions",
    "harvest_decisions",
    default=True,
    help=(
        "Harvest candidate architectural decisions from LLM page generation "
        "(file pages). Each harvested decision is verified against the file's "
        "source before storage. The model emits a decision only on a genuine "
        "hit, so the token cost lands only on files that carry one. Default: on."
    ),
)
def init_command(
    path: str | None,
    provider_name: str | None,
    model: str | None,
    embedder_name: str | None,
    skip_tests: bool,
    skip_infra: bool,
    dry_run: bool,
    yes: bool,
    resume: bool,
    force: bool,
    concurrency: int,
    reasoning: str | None,
    test_run: bool,
    index_only: bool,
    run_mode: str,
    exclude: tuple[str, ...],
    commit_limit: int | None,
    follow_renames: bool,
    no_claude_md: bool,
    include_submodules: bool,
    init_all: bool,
    onboarding: bool,
    coverage_pct: float | None,
    harvest_decisions: bool,
) -> None:
    """Generate wiki documentation for a codebase.

    PATH defaults to the current directory.
    Use --index-only to run ingestion (AST, graph, git, dead code) without LLM generation.
    Use --mode fast for a quick graph + essential-git index of a very large repo.
    """
    # --mode fast is a graph + essential-git index with no LLM work, so it
    # implies index-only on the CLI side; the orchestrator mode below switches
    # the git tier to ESSENTIAL.
    if run_mode == "fast":
        index_only = True
    start = time.monotonic()
    repo_path = resolve_repo_path(path)

    if not repo_path.is_dir():
        raise click.ClickException(f"Not a directory: {repo_path}")

    # ---- Workspace detection ----
    # If the path contains multiple git repos (and is not itself a single repo),
    # branch into the multi-repo workspace flow.
    from repowise.core.workspace import scan_for_repos

    scan = scan_for_repos(repo_path, include_submodules=include_submodules)
    if len(scan.repos) > 1:
        _workspace_init(
            scan=scan,
            init_all=init_all,
            exclude_patterns=list(exclude),
            commit_limit=commit_limit,
            follow_renames=follow_renames,
            no_claude_md=no_claude_md,
            include_submodules=include_submodules,
            provider_name=provider_name,
            model=model,
            embedder_name=embedder_name,
            index_only=index_only,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            concurrency=concurrency,
            reasoning=reasoning,
            test_run=test_run,
            yes=yes,
            dry_run=dry_run,
            resume=resume,
            force=force,
            onboarding=onboarding,
            coverage_pct=coverage_pct,
            harvest_decisions=harvest_decisions,
        )
        return

    # If a single repo was found inside the given directory (not at root),
    # redirect to it so the user doesn't have to specify the exact path.
    if len(scan.repos) == 1 and scan.repos[0].path != repo_path:
        repo_path = scan.repos[0].path

    ensure_repowise_dir(repo_path)
    load_dotenv(repo_path)

    # Suppress library/structlog output — progress bars are the only output needed.
    setup_logging_silence()

    # ---- Interactive mode (TTY, no explicit flags) ----
    is_interactive = sys.stdin.isatty() and provider_name is None and not index_only

    # Tiered doc generation cap (set in advanced mode); None = every selected
    # file page is a full-LLM tier-1 page (unchanged behaviour).
    tier1_top_n: int | None = None

    # Pre-scan for interactive mode — fast stats to inform choices
    scan_info = None
    if is_interactive:
        print_banner(console, repo_name=repo_path.name)
        with console.status("  Scanning repository…", spinner="dots"):
            scan_info = quick_repo_scan(repo_path)
        print_scan_summary(console, scan_info)
        mode = interactive_mode_select(console)

        if mode == "index_only":
            index_only = True
            # On a large repo, an index-only run is exactly the case where the
            # fast tier (essential git, no blame/co-change) pays off — offer it,
            # defaulting to yes since docs are already opted out.
            if (
                run_mode != "fast"
                and should_offer_fast_mode(scan_info)
                and interactive_fast_mode_offer(console, scan_info, default_fast=True)
            ):
                run_mode = "fast"
        elif mode == "advanced":
            provider_name, model = interactive_provider_select(console, model, repo_path=repo_path)
            adv = interactive_advanced_config(console, scan=scan_info, allow_fast=True)
            commit_limit = adv["commit_limit"]
            follow_renames = adv["follow_renames"]
            skip_tests = adv["skip_tests"]
            skip_infra = adv["skip_infra"]
            concurrency = adv["concurrency"]
            reasoning = adv.get("reasoning") or reasoning
            exclude = adv["exclude"]
            test_run = adv["test_run"]
            embedder_name = adv.get("embedder") or embedder_name
            include_submodules = adv.get("include_submodules", include_submodules)
            run_mode = adv.get("run_mode", run_mode)
            tier1_top_n = adv.get("tier1_top_n")
            if run_mode == "fast":
                index_only = True
        else:
            provider_name, model = interactive_provider_select(console, model, repo_path=repo_path)
            # Full mode picked, but on a large repo offer the quick path too.
            # Default no here — the user explicitly asked for docs.
            if (
                run_mode != "fast"
                and should_offer_fast_mode(scan_info)
                and interactive_fast_mode_offer(console, scan_info, default_fast=False)
            ):
                run_mode = "fast"
                index_only = True

    editor_options = resolve_editor_setup_options(
        console,
        disabled_project_files=get_default_disabled_project_files(
            no_claude_md=no_claude_md,
        ),
        prompt_for_project_files=is_interactive and not index_only,
    )

    # Merge exclude_patterns from config.yaml and --exclude/-x flags
    config = load_config(repo_path)
    language = config.get("language", "en")
    resolved_reasoning = resolve_reasoning(reasoning, config)
    exclude_patterns: list[str] = list(config.get("exclude_patterns") or []) + list(exclude)

    # Resolve commit limit: CLI flag → config.yaml → default (500)
    resolved_commit_limit: int = commit_limit or config.get("commit_limit") or 500
    resolved_commit_limit = max(1, min(resolved_commit_limit, 5000))
    if commit_limit is not None:
        config["commit_limit"] = resolved_commit_limit

    # Resolve follow_renames: CLI flag → config.yaml
    resolved_follow_renames: bool = follow_renames or config.get("follow_renames", False)
    if follow_renames:
        config["follow_renames"] = True

    embedder_name_resolved = _resolve_embedder(embedder_name)

    # ---- Resolve provider ----
    provider = None
    decision_provider = None

    if index_only:
        try:
            if (
                provider_name
                or (sys.stdin.isatty() is False)
                or any(
                    os.environ.get(k)
                    for k in (
                        "GEMINI_API_KEY",
                        "GOOGLE_API_KEY",
                        "OPENAI_API_KEY",
                        "ANTHROPIC_API_KEY",
                    )
                )
            ):
                decision_provider = resolve_provider(provider_name, model, repo_path)
        except Exception:
            pass

        has_provider = decision_provider is not None
        if is_interactive:
            print_index_only_intro(console, has_provider=has_provider)
        else:
            console.print(f"[bold]repowise index-only[/bold] — {repo_path}")
            console.print("[yellow]Skipping LLM page generation (--index-only)[/yellow]")
            if decision_provider:
                console.print(
                    f"Decision extraction provider: [cyan]{decision_provider.provider_name}[/cyan]"
                )
    else:
        if not is_interactive and provider_name is None and sys.stdin.isatty():
            from repowise.cli.ui import interactive_provider_select as _ips

            provider_name, model = _ips(console, model)

        provider = resolve_provider(provider_name, model, repo_path)
        # resolve_provider / interactive_provider_select may have just set
        # the API key in os.environ. Re-resolve the embedder so the
        # display (and the embed path below) honors the key the user just
        # pasted, rather than the pre-prompt "mock" fallback.
        embedder_name_resolved = _resolve_embedder(embedder_name)
        if not is_interactive:
            console.print(f"[bold]repowise init[/bold] — {repo_path}")
        console.print(
            f"  Provider: [cyan]{provider.provider_name}[/cyan] / Model: [cyan]{provider.model_name}[/cyan]"
        )
        console.print(f"  Embedder: [cyan]{embedder_name_resolved}[/cyan]")
        if language != "en":
            console.print(f"  Language: [cyan]{language}[/cyan]")
        if resolved_reasoning != "auto":
            console.print(f"  Reasoning: [cyan]{resolved_reasoning}[/cyan]")

        # Validate provider connection
        from repowise.core.providers.llm.base import ProviderError

        with console.status("  Verifying provider connection…", spinner="dots"):
            try:
                run_async(
                    provider.generate(
                        "You are a test.",
                        "Reply with OK.",
                        max_tokens=50,
                        reasoning=resolved_reasoning,
                    )
                )
            except ProviderError as exc:
                raise click.ClickException(f"Provider validation failed: {exc}") from exc
        console.print("  [green]✓[/green] Provider connection verified")

    # ---- Phase 1 & 2: Ingestion + Analysis (always) ----
    total_phases = 3 if index_only else 4
    # Tracks whether the user declined the LLM cost gate. When True we
    # skip generation but still persist the index/graph/git/dead-code so
    # the run isn't wasted, and propagate the choice to state.docs_enabled
    # so subsequent updates default to index-only.
    cost_declined = False
    llm_client = provider if not index_only else decision_provider

    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    orchestrator_mode = OrchestratorMode.FAST if run_mode == "fast" else OrchestratorMode.STANDARD

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        TextColumn("[green]${task.fields[cost]:.3f}[/green]"),
        console=console,
    ) as progress_bar:
        rich_callback = RichProgressCallback(progress_bar, console)
        # Wrap the Rich callback so we can record per-phase wall-clock
        # durations without changing the pipeline API. Timings get
        # persisted to state.json below.
        callback = PhaseTimingRecorder(rich_callback)

        # Always run ingestion + analysis first (generate_docs=False).
        # Generation happens separately after cost confirmation.
        _prev_state = load_state(repo_path)
        _prev_kg_fp = (
            _prev_state.get("knowledge_graph", {}).get("fingerprint") if not force else None
        )

        result = run_async(
            run_pipeline(
                repo_path,
                commit_depth=resolved_commit_limit,
                follow_renames=resolved_follow_renames,
                skip_tests=skip_tests,
                skip_infra=skip_infra,
                exclude_patterns=exclude_patterns if exclude_patterns else None,
                include_submodules=include_submodules,
                generate_docs=False,
                llm_client=llm_client,
                concurrency=concurrency,
                test_run=test_run,
                mode=orchestrator_mode,
                progress=callback,
                existing_kg_fingerprint=_prev_kg_fp,
            )
        )

    # Surface per-phase timing data to the caller — both for the
    # state.json persistence below and for any future "profile" tooling
    # that wants to introspect a run.
    phase_timings: dict[str, float] = callback.timings

    # ---- Analysis summary (shown between analysis and generation) ----
    _show_analysis_summary(console, result)

    # ---- Phase 3: Generation (full mode only) ----
    if not index_only:
        gen_stop, cost_declined = _run_generation_phase(
            repo_path=repo_path,
            result=result,
            provider=provider,
            total_phases=total_phases,
            concurrency=concurrency,
            language=language,
            resolved_reasoning=resolved_reasoning,
            onboarding=onboarding,
            tier1_top_n=tier1_top_n,
            harvest_decisions=harvest_decisions,
            coverage_pct=coverage_pct,
            yes=yes,
            dry_run=dry_run,
            skip_tests=skip_tests,
            skip_infra=skip_infra,
            embedder_name_resolved=embedder_name_resolved,
            resume=resume,
        )
        if gen_stop:
            return

    # ---- Persistence ----
    # `cost_declined` short-circuits any further LLM work for the rest of
    # this run, so persistence/state below treat it as index-only.
    effective_index_only = index_only or cost_declined
    if effective_index_only:
        print_phase_header(console, 3, total_phases, "Persistence", "Saving to database")
    else:
        print_phase_header(
            console, 4, total_phases, "Persistence", "Saving to database and building search index"
        )

    with console.status("  Persisting to database…", spinner="dots"):
        run_async(_persist_result(result, repo_path))
    console.print("  [green]✓[/green] Database updated")

    # Persist the onboarding choice so subsequent `repowise update` runs
    # honor it without re-passing the flag. Default True is omitted to keep
    # config files tidy — only the override is recorded.
    if not onboarding:
        cfg = load_config(repo_path)
        cfg["enable_onboarding"] = False
        try:
            import yaml  # type: ignore[import-untyped]

            cfg_path = repo_path / ".repowise" / "config.yaml"
            cfg_path.write_text(
                yaml.dump(cfg, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        except ImportError:
            pass

    # ---- Post-run: config, state, MCP, editor project files ----
    if commit_limit is not None:
        cfg = load_config(repo_path)
        cfg["commit_limit"] = resolved_commit_limit
        try:
            import yaml  # type: ignore[import-untyped]

            cfg_path = repo_path / ".repowise" / "config.yaml"
            cfg_path.write_text(
                yaml.dump(cfg, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        except ImportError:
            pass

    write_editor_project_files(
        console,
        repo_path,
        options=editor_options,
    )
    register_editor_clients(console, repo_path)

    # ---- State (always) ----
    # Even in index-only mode we persist `last_sync_commit` so that a
    # subsequent `repowise update` (e.g. fired by the post-commit hook) has
    # a baseline to diff against. Without this, index-only users hit
    # "No previous sync found" on every update.
    head = get_head_commit(repo_path)
    base_state = load_state(repo_path)
    base_state["last_sync_commit"] = head
    base_state["docs_enabled"] = not effective_index_only and provider is not None
    if phase_timings:
        base_state["phase_timings"] = phase_timings
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        base_state["knowledge_graph"] = build_kg_state(kg)
        save_knowledge_graph_json(repo_path, kg)
    if effective_index_only or provider is None:
        # Index-only mode skips save_config(); persist exclude_patterns/commit_limit here.
        save_config_partial(
            repo_path,
            exclude_patterns=exclude_patterns if exclude_patterns else None,
            commit_limit=resolved_commit_limit if commit_limit is not None else None,
        )
        # Fingerprint after config writes so the first update doesn't false-positive.
        base_state["config_fingerprint"] = config_fingerprint(repo_path)
        save_state(repo_path, base_state)

    # ---- State + config (full mode only) ----
    if not effective_index_only and provider:
        _save_full_state_and_config(
            repo_path=repo_path,
            result=result,
            provider=provider,
            phase_timings=phase_timings,
            embedder_name_resolved=embedder_name_resolved,
            exclude_patterns=exclude_patterns,
            commit_limit=commit_limit,
            resolved_commit_limit=resolved_commit_limit,
            resolved_reasoning=resolved_reasoning,
        )

    # ---- Completion panel ----
    _show_completion(
        repo_path=repo_path,
        result=result,
        start=start,
        effective_index_only=effective_index_only,
        run_mode=run_mode,
        provider=provider,
    )

    # Offer to install post-commit hook (both index-only and full modes)
    _offer_hook_install(console, [repo_path])
