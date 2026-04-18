"""``repowise init`` — full wiki generation for a repository."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import click
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from repowise.cli.cost_estimator import build_generation_plan, estimate_cost
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_repo_path,
    run_async,
    save_config,
    save_state,
)
from repowise.cli.ui import MaybeCountColumn

# ---------------------------------------------------------------------------
# Helpers (kept in this file; _resolve_embedder also imported by other cmds)
# ---------------------------------------------------------------------------


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
            console_obj.print(
                "  [dim]Skipped. Run 'repowise hook install' later to set up.[/dim]"
            )
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


def _resolve_embedder(embedder_flag: str | None) -> str:
    """Auto-detect embedder from env vars, or use the flag value."""
    if embedder_flag:
        return embedder_flag
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "mock"


def _register_mcp_with_claude(console_obj: Any, repo_path: Path) -> None:
    """Register the repowise MCP server and hooks with Claude Desktop and Claude Code."""
    from repowise.cli.mcp_config import (
        install_claude_code_hooks,
        register_with_claude_code,
        register_with_claude_desktop,
    )

    desktop = register_with_claude_desktop(repo_path)
    if desktop:
        console_obj.print(f"  [green]✓[/green] Claude Desktop MCP registered ({desktop})")

    code = register_with_claude_code(repo_path)
    if code:
        console_obj.print(f"  [green]✓[/green] Claude Code MCP registered ({code})")

    hooks = install_claude_code_hooks()
    if hooks:
        console_obj.print(
            f"  [green]✓[/green] Claude Code hooks registered (PreToolUse + PostToolUse)"
        )


def _maybe_generate_claude_md(
    console_obj: Any,
    repo_path: Path,
    *,
    no_claude_md: bool = False,
) -> None:
    """Generate CLAUDE.md if enabled in config and not opted out."""
    cfg = load_config(repo_path)
    enabled = cfg.get("editor_files", {}).get("claude_md", True)
    if no_claude_md:
        # Persist opt-out so 'repowise update' respects it
        ef_cfg = dict(cfg.get("editor_files", {}))
        ef_cfg["claude_md"] = False
        cfg["editor_files"] = ef_cfg
        try:
            import yaml  # type: ignore[import-untyped]

            cfg_path = repo_path / ".repowise" / "config.yaml"
            cfg_path.write_text(
                yaml.dump(cfg, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        except ImportError:
            pass
        return
    if not enabled:
        return
    try:
        with console_obj.status("  Generating .claude/CLAUDE.md…", spinner="dots"):
            run_async(_write_claude_md_async(repo_path))
        console_obj.print("  [green]✓[/green] .claude/CLAUDE.md updated")
    except Exception as exc:
        console_obj.print(f"  [yellow].claude/CLAUDE.md skipped: {exc}[/yellow]")


async def _write_claude_md_async(repo_path: Path) -> None:
    """Fetch data from DB and write CLAUDE.md (async helper)."""
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.generation.editor_files import ClaudeMdGenerator, EditorFileDataFetcher
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.crud import get_repository_by_path

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return  # Not indexed yet — skip silently
            fetcher = EditorFileDataFetcher(session, repo.id, repo_path)
            data = await fetcher.fetch()
    finally:
        await engine.dispose()
    ClaudeMdGenerator().write(repo_path, data)


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
        await persist_pipeline_result(result, session, repo.id)

        # Record a completed GenerationJob so the web UI can show
        # "last synced" / "last re-indexed" timestamps.
        from datetime import datetime, UTC as _UTC
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
) -> list[Any]:
    """Run LLM generation for a single repo in the workspace init flow.

    Returns the list of generated pages.  Raises on unrecoverable errors so
    the caller can catch and log per-repo failures without aborting the whole
    workspace run.
    """
    from repowise.cli.cost_estimator import build_generation_plan, estimate_cost
    from repowise.cli.ui import BRAND, RichProgressCallback
    from repowise.core.generation import GenerationConfig
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.persistence.vector_store import InMemoryVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder
    from repowise.core.pipeline import run_generation
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        create_engine as _ce,
        create_session_factory as _csf,
        get_session as _gs,
        init_db as _idb,
        upsert_repository as _ur,
    )

    # Build embedder
    embedder_impl: Any
    if embedder_name_resolved == "gemini":
        try:
            from repowise.core.providers.embedding.gemini import GeminiEmbedder

            embedder_impl = GeminiEmbedder()
        except Exception:
            embedder_impl = MockEmbedder()
    elif embedder_name_resolved == "openai":
        try:
            from repowise.core.providers.embedding.openai import OpenAIEmbedder

            embedder_impl = OpenAIEmbedder()
        except Exception:
            embedder_impl = MockEmbedder()
    else:
        embedder_impl = MockEmbedder()

    # Build vector store
    lance_dir = repo_path / ".repowise" / "lancedb"
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore

        lance_dir.mkdir(parents=True, exist_ok=True)
        vector_store: Any = LanceDBVectorStore(str(lance_dir), embedder=embedder_impl)
    except ImportError:
        vector_store = InMemoryVectorStore(embedder_impl)

    # Cost estimate
    gen_config = GenerationConfig(max_concurrency=concurrency)
    plans = build_generation_plan(
        result.parsed_files, result.graph_builder, gen_config, skip_tests, skip_infra
    )
    est = estimate_cost(plans, provider.provider_name, provider.model_name)

    console.print(
        f"    Estimated tokens: ~{est.estimated_input_tokens + est.estimated_output_tokens:,} "
        f"(${est.estimated_cost_usd:.2f} USD, {est.total_pages} pages)"
    )

    if (
        est.estimated_cost_usd > 2.00
        and not yes
        and not click.confirm(f"    Cost for {repo_path.name} exceeds $2.00. Continue?")
    ):
        console.print("    [yellow]Skipped.[/yellow]")
        return []

    # Cost tracker (DB-backed when possible)
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    async def _make_cost_tracker() -> CostTracker:
        url = get_db_url_for_repo(repo_path)
        engine = _ce(url)
        await _idb(engine)
        sf = _csf(engine)
        async with _gs(sf) as _sess:
            _repo = await _ur(_sess, name=result.repo_name, local_path=str(repo_path))
            _repo_id = _repo.id
        return CostTracker(session_factory=sf, repo_id=_repo_id)

    try:
        cost_tracker = run_async(_make_cost_tracker())
    except Exception:
        cost_tracker = CostTracker()

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

    return generated_pages


# ---------------------------------------------------------------------------
# Workspace init — multi-repo flow
# ---------------------------------------------------------------------------


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
    yes: bool = False,
    dry_run: bool = False,
    resume: bool = False,
    force: bool = False,
) -> None:
    """Multi-repo workspace initialization.

    Detects repos, prompts for selection and primary, creates a workspace
    config, then runs ingestion on each repo.  When the user selects full or
    advanced mode (interactively) or passes an explicit provider, also runs
    LLM generation per repo.
    """
    import logging

    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    for _logger_name in ("repowise.core", "repowise.server"):
        logging.getLogger(_logger_name).setLevel(logging.ERROR)
    try:
        import structlog

        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    except ImportError:
        pass

    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from repowise.cli.mcp_config import save_mcp_config, save_root_mcp_config
    from repowise.cli.ui import (
        BRAND,
        RichProgressCallback,
        build_completion_panel,
        format_elapsed,
        interactive_advanced_config,
        interactive_mode_select,
        interactive_primary_select,
        interactive_provider_select,
        interactive_repo_select,
        load_dotenv,
        print_banner,
    )
    from repowise.core.pipeline import run_pipeline
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
            embedder_name_resolved = _resolve_embedder(adv.get("embedder") or embedder_name)
        elif not index_only:
            # "full" mode
            provider_name, model = interactive_provider_select(
                console, model, repo_path=primary_repo.path
            )

    # Resolve provider once (shared across all repos for generation)
    provider = None
    if not index_only:
        try:
            provider = resolve_provider(provider_name, model, primary_repo.path)
            console.print(
                f"  Provider: [cyan]{provider.provider_name}[/cyan] / "
                f"Model: [cyan]{provider.model_name}[/cyan]"
            )
            console.print(f"  Embedder: [cyan]{embedder_name_resolved}[/cyan]\n")
        except Exception as exc:
            console.print(f"  [yellow]Provider setup failed ({exc}); falling back to index-only.[/yellow]")
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

    for i, repo in enumerate(selected, 1):
        console.print(
            f"  [{BRAND}][{i}/{len(selected)}][/] Indexing [bold]{repo.alias}[/bold] ({repo.path.name})..."
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
                callback = RichProgressCallback(progress_bar, console)

                result = run_async(
                    run_pipeline(
                        repo.path,
                        commit_depth=resolved_commit_limit,
                        follow_renames=follow_renames,
                        exclude_patterns=exclude_patterns if exclude_patterns else None,
                        include_submodules=include_submodules,
                        generate_docs=False,
                        progress=callback,
                    )
                )

            total_files += result.file_count
            total_symbols += result.symbol_count
            console.print(
                f"    [green]\u2713[/green] {result.file_count} files, "
                f"{result.symbol_count:,} symbols"
            )

        except Exception as exc:
            errors.append((repo.alias, str(exc)))
            console.print(f"    [red]\u2717 Failed: {exc}[/red]\n")
            continue

        # Generation phase (per-repo, only when not index-only)
        if not index_only and provider is not None:
            if dry_run:
                console.print("    [yellow]Dry run — skipping generation for this repo.[/yellow]\n")
            else:
                try:
                    generated_pages = _run_workspace_generation(
                        repo_path=repo.path,
                        result=result,
                        provider=provider,
                        embedder_name_resolved=embedder_name_resolved,
                        concurrency=concurrency,
                        yes=yes,
                        resume=resume,
                        skip_tests=skip_tests,
                        skip_infra=skip_infra,
                        test_run=test_run,
                    )
                    result.generated_pages = generated_pages
                    total_pages += len(generated_pages)
                    console.print(
                        f"    [green]\u2713[/green] Generated {len(generated_pages)} pages\n"
                    )
                except Exception as gen_exc:
                    console.print(f"    [yellow]Generation failed: {gen_exc}[/yellow]\n")
        else:
            console.print()

        # Persist to repo-local DB
        run_async(_persist_result(result, repo.path))

        # Write state.json so `repowise update` knows the base commit
        head = get_head_commit(repo.path)
        pages_count = len(result.generated_pages or [])
        state: dict[str, Any] = {
            "last_sync_commit": head,
            "total_pages": pages_count,
        }
        if not index_only and provider is not None:
            state["provider"] = provider.provider_name
            state["model"] = provider.model_name
        save_state(repo.path, state)

        # Update workspace config with indexing metadata
        from datetime import datetime, timezone

        entry = ws_config.get_repo(repo.alias)
        if entry is not None:
            entry.indexed_at = datetime.now(timezone.utc).isoformat()
            entry.last_commit_at_index = head

        # MCP config + CLAUDE.md per repo
        save_mcp_config(repo.path)
        save_root_mcp_config(repo.path)
        _maybe_generate_claude_md(console, repo.path, no_claude_md=no_claude_md)

        # Persist provider/model config per-repo when doing full generation
        if not index_only and provider is not None:
            save_config(
                repo.path,
                provider.provider_name,
                provider.model_name,
                embedder_name_resolved,
                exclude_patterns=exclude_patterns if exclude_patterns else None,
                commit_limit=resolved_commit_limit,
            )

    # Save workspace config with updated timestamps
    ws_config.save(root)

    # Step 5: Cross-repo analysis (co-changes, package deps, contracts)
    indexed_aliases = [
        repo.alias for repo in selected
        if repo.alias not in [e[0] for e in errors]
    ]
    if len(indexed_aliases) >= 2:
        console.print("  Running cross-repo analysis...")
        try:
            from repowise.core.workspace.update import run_cross_repo_hooks

            run_async(run_cross_repo_hooks(ws_config, root, indexed_aliases))
            console.print("  [green]✓[/green] Cross-repo analysis complete")
        except Exception as exc:
            console.print(f"  [yellow]⚠ Cross-repo analysis failed: {exc}[/yellow]")

    # Step 6: Register MCP for primary repo with Claude
    primary_entry = ws_config.get_primary()
    if primary_entry:
        primary_path = (root / primary_entry.path).resolve()
        _register_mcp_with_claude(console, primary_path)

    # Step 7: Completion summary
    elapsed = time.monotonic() - start
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

    # Offer to install post-commit hooks
    indexed_repos = [
        repo for repo in selected
        if repo.alias not in [e[0] for e in errors]
    ]
    if indexed_repos:
        _offer_hook_install(
            console,
            [r.path for r in indexed_repos],
            aliases=[r.alias for r in indexed_repos],
        )
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
    help="LLM provider name (anthropic, openai, gemini, ollama, mock).",
)
@click.option("--model", default=None, help="Model identifier override.")
@click.option(
    "--embedder",
    "embedder_name",
    default=None,
    type=click.Choice(["gemini", "openai", "mock"]),
    help="Embedder for RAG: gemini | openai | mock (default: auto-detect).",
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
    test_run: bool,
    index_only: bool,
    exclude: tuple[str, ...],
    commit_limit: int | None,
    follow_renames: bool,
    no_claude_md: bool,
    include_submodules: bool,
    init_all: bool,
) -> None:
    """Generate wiki documentation for a codebase.

    PATH defaults to the current directory.
    Use --index-only to run ingestion (AST, graph, git, dead code) without LLM generation.
    """
    from repowise.cli.ui import (
        BRAND,
        RichProgressCallback,
        build_analysis_summary_panel,
        build_completion_panel,
        build_contextual_next_steps,
        format_elapsed,
        interactive_advanced_config,
        interactive_mode_select,
        interactive_provider_select,
        load_dotenv,
        print_banner,
        print_index_only_intro,
        print_phase_header,
        print_scan_summary,
        quick_repo_scan,
    )

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
            test_run=test_run,
            yes=yes,
            dry_run=dry_run,
            resume=resume,
            force=force,
        )
        return

    # If a single repo was found inside the given directory (not at root),
    # redirect to it so the user doesn't have to specify the exact path.
    if len(scan.repos) == 1 and scan.repos[0].path != repo_path:
        repo_path = scan.repos[0].path

    ensure_repowise_dir(repo_path)
    load_dotenv(repo_path)

    # Suppress library/structlog output — progress bars are the only output needed.
    import logging

    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    for _logger_name in ("repowise.core", "repowise.server"):
        logging.getLogger(_logger_name).setLevel(logging.ERROR)

    try:
        import structlog

        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
        )
    except ImportError:
        pass

    # ---- Interactive mode (TTY, no explicit flags) ----
    is_interactive = sys.stdin.isatty() and provider_name is None and not index_only

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
        elif mode == "advanced":
            provider_name, model = interactive_provider_select(console, model, repo_path=repo_path)
            adv = interactive_advanced_config(console, scan=scan_info)
            commit_limit = adv["commit_limit"]
            follow_renames = adv["follow_renames"]
            skip_tests = adv["skip_tests"]
            skip_infra = adv["skip_infra"]
            concurrency = adv["concurrency"]
            exclude = adv["exclude"]
            test_run = adv["test_run"]
            embedder_name = adv.get("embedder") or embedder_name
            include_submodules = adv.get("include_submodules", include_submodules)
            no_claude_md = adv.get("no_claude_md", no_claude_md)
        else:
            provider_name, model = interactive_provider_select(console, model, repo_path=repo_path)

    # Merge exclude_patterns from config.yaml and --exclude/-x flags
    config = load_config(repo_path)
    language = config.get("language", "en")
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
        if not is_interactive:
            console.print(f"[bold]repowise init[/bold] — {repo_path}")
        console.print(
            f"  Provider: [cyan]{provider.provider_name}[/cyan] / Model: [cyan]{provider.model_name}[/cyan]"
        )
        console.print(f"  Embedder: [cyan]{embedder_name_resolved}[/cyan]")
        if language != "en":
            console.print(f"  Language: [cyan]{language}[/cyan]")

        # Validate provider connection
        from repowise.core.providers.llm.base import ProviderError

        with console.status("  Verifying provider connection…", spinner="dots"):
            try:
                run_async(provider.generate("You are a test.", "Reply with OK.", max_tokens=50))
            except ProviderError as exc:
                raise click.ClickException(f"Provider validation failed: {exc}") from exc
        console.print("  [green]✓[/green] Provider connection verified")

    # ---- Phase 1 & 2: Ingestion + Analysis (always) ----
    total_phases = 3 if index_only else 4
    llm_client = provider if not index_only else decision_provider

    from repowise.core.pipeline import run_pipeline

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MaybeCountColumn(),
        TimeElapsedColumn(),
        TextColumn("[green]${task.fields[cost]:.3f}[/green]"),
        console=console,
    ) as progress_bar:
        callback = RichProgressCallback(progress_bar, console)

        # Always run ingestion + analysis first (generate_docs=False).
        # Generation happens separately after cost confirmation.
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
                progress=callback,
            )
        )

    # ---- Analysis summary (shown between analysis and generation) ----
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

    # ---- Phase 3: Generation (full mode only) ----
    if not index_only:
        print_phase_header(
            console,
            3,
            total_phases,
            "Generation",
            f"Generating wiki pages with {provider.provider_name} / {provider.model_name}",
        )

        # Cost estimation
        from repowise.core.generation import GenerationConfig
        gen_config = GenerationConfig(max_concurrency=concurrency, language=language)
        plans = build_generation_plan(
            result.parsed_files, result.graph_builder, gen_config, skip_tests, skip_infra
        )
        est = estimate_cost(plans, provider.provider_name, provider.model_name)

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

        console.print(
            f"  Estimated tokens: ~{est.estimated_input_tokens + est.estimated_output_tokens:,} "
            f"(${est.estimated_cost_usd:.2f} USD)"
        )
        console.print()

        if dry_run:
            console.print("[yellow]Dry run — no pages generated.[/yellow]")
            return

        if (
            est.estimated_cost_usd > 2.00
            and not yes
            and not click.confirm("  Estimated cost exceeds $2.00. Continue?")
        ):
            console.print("[yellow]Aborted.[/yellow]")
            return

        # Build embedder + vector store
        from repowise.core.persistence.vector_store import InMemoryVectorStore
        from repowise.core.providers.embedding.base import MockEmbedder

        embedder_impl: Any
        if embedder_name_resolved == "gemini":
            try:
                from repowise.core.providers.embedding.gemini import GeminiEmbedder

                embedder_impl = GeminiEmbedder()
            except Exception:
                embedder_impl = MockEmbedder()
        elif embedder_name_resolved == "openai":
            try:
                from repowise.core.providers.embedding.openai import OpenAIEmbedder

                embedder_impl = OpenAIEmbedder()
            except Exception:
                embedder_impl = MockEmbedder()
        else:
            embedder_impl = MockEmbedder()

        lance_dir = repo_path / ".repowise" / "lancedb"
        try:
            from repowise.core.persistence.vector_store import LanceDBVectorStore

            lance_dir.mkdir(parents=True, exist_ok=True)
            vector_store: Any = LanceDBVectorStore(str(lance_dir), embedder=embedder_impl)
        except ImportError:
            vector_store = InMemoryVectorStore(embedder_impl)

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
            # is persisted to the llm_costs table.  We need the repo_id from the
            # database row that was created/upserted during _persist_result
            # (which has not run yet), so we look it up or fall back to in-memory.
            from repowise.core.generation.cost_tracker import CostTracker
            from repowise.cli.helpers import get_db_url_for_repo
            from repowise.core.persistence import (
                create_engine as _create_engine,
                create_session_factory as _create_sf,
                get_session as _get_session,
                init_db as _init_db,
                upsert_repository as _upsert_repo,
            )

            async def _make_cost_tracker() -> CostTracker:
                url = get_db_url_for_repo(repo_path)
                engine = _create_engine(url)
                await _init_db(engine)
                sf = _create_sf(engine)
                async with _get_session(sf) as _sess:
                    _repo = await _upsert_repo(
                        _sess,
                        name=result.repo_name,
                        local_path=str(repo_path),
                    )
                    _repo_id = _repo.id
                # Keep engine alive for the duration of generation — it will be
                # disposed by _persist_result's own engine later.
                return CostTracker(session_factory=sf, repo_id=_repo_id)

            try:
                cost_tracker = run_async(_make_cost_tracker())
            except Exception:
                # Fallback to in-memory tracker if DB setup fails
                cost_tracker = CostTracker()

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
        console.print(f"  [green]✓[/green] Generated [bold]{len(generated_pages)}[/bold] pages")

    # ---- Persistence ----
    if index_only:
        print_phase_header(console, 3, total_phases, "Persistence", "Saving to database")
    else:
        print_phase_header(
            console, 4, total_phases, "Persistence", "Saving to database and building search index"
        )

    with console.status("  Persisting to database…", spinner="dots"):
        run_async(_persist_result(result, repo_path))
    console.print("  [green]✓[/green] Database updated")

    # ---- Post-run: config, state, MCP, CLAUDE.md ----
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

    from repowise.cli.mcp_config import save_mcp_config, save_root_mcp_config

    save_mcp_config(repo_path)
    save_root_mcp_config(repo_path)
    _register_mcp_with_claude(console, repo_path)

    _maybe_generate_claude_md(console, repo_path, no_claude_md=no_claude_md)

    # ---- State + config (full mode only) ----
    if not index_only and provider:

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
                    sa_select(sa_func.count())
                    .select_from(Page)
                    .where(Page.repository_id == _repo_id)
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
        total_tokens = sum(p.total_tokens for p in (result.generated_pages or []))
        state["total_tokens"] = total_tokens
        save_state(repo_path, state)

        save_config(
            repo_path,
            provider.provider_name,
            provider.model_name,
            embedder_name_resolved,
            exclude_patterns=exclude_patterns if exclude_patterns else None,
            commit_limit=resolved_commit_limit if commit_limit is not None else None,
        )

    # ---- Completion panel ----
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

    if index_only:
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

    # Offer to install post-commit hook (both index-only and full modes)
    _offer_hook_install(console, [repo_path])
