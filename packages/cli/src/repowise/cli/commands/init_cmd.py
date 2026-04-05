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

# ---------------------------------------------------------------------------
# Helpers (kept in this file; _resolve_embedder also imported by other cmds)
# ---------------------------------------------------------------------------


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
    """Register the repowise MCP server with Claude Desktop and Claude Code."""
    from repowise.cli.mcp_config import register_with_claude_code, register_with_claude_desktop

    desktop = register_with_claude_desktop(repo_path)
    if desktop:
        console_obj.print(f"  [green]✓[/green] Claude Desktop MCP registered ({desktop})")

    code = register_with_claude_code(repo_path)
    if code:
        console_obj.print(f"  [green]✓[/green] Claude Code MCP registered ({code})")


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

    # FTS indexing is done outside the session to avoid SQLite write conflicts
    if fts is not None and result.generated_pages:
        for page in result.generated_pages:
            await fts.index(page.page_id, page.title, page.content)

    await engine.dispose()


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
) -> None:
    """Generate wiki documentation for a codebase.

    PATH defaults to the current directory.
    Use --index-only to run ingestion (AST, graph, git, dead code) without LLM generation.
    """
    from repowise.cli.ui import (
        BRAND,
        RichProgressCallback,
        build_completion_panel,
        format_elapsed,
        interactive_advanced_config,
        interactive_mode_select,
        interactive_provider_select,
        load_dotenv,
        print_banner,
        print_index_only_intro,
        print_phase_header,
    )

    start = time.monotonic()
    repo_path = resolve_repo_path(path)

    if not repo_path.is_dir():
        raise click.ClickException(f"Not a directory: {repo_path}")

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

    if is_interactive:
        print_banner(console, repo_name=repo_path.name)
        mode = interactive_mode_select(console)

        if mode == "index_only":
            index_only = True
        elif mode == "advanced":
            provider_name, model = interactive_provider_select(console, model, repo_path=repo_path)
            adv = interactive_advanced_config(console)
            commit_limit = adv["commit_limit"]
            follow_renames = adv["follow_renames"]
            skip_tests = adv["skip_tests"]
            skip_infra = adv["skip_infra"]
            concurrency = adv["concurrency"]
            exclude = adv["exclude"]
            test_run = adv["test_run"]
        else:
            provider_name, model = interactive_provider_select(console, model, repo_path=repo_path)

    # Merge exclude_patterns from config.yaml and --exclude/-x flags
    config = load_config(repo_path)
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
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
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
                generate_docs=False,
                llm_client=llm_client,
                concurrency=concurrency,
                test_run=test_run,
                progress=callback,
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

        gen_config = GenerationConfig(max_concurrency=concurrency)
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
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
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

    _graph = result.graph_builder.graph()
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

    if index_only:
        metrics: list[tuple[str, str]] = [
            ("Files indexed", str(result.file_count)),
            ("Symbols", f"{result.symbol_count:,}"),
            ("Languages", str(len(result.languages))),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            (
                "Graph",
                f"{_graph.number_of_nodes()} nodes · {_graph.number_of_edges()} edges",
            ),
            ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
            ("Decisions", str(_n_decisions)),
        ]
        if result.git_summary:
            metrics.append(
                (
                    "Git history",
                    f"{result.git_summary.files_indexed} files · {result.git_summary.hotspots} hotspots",
                )
            )

        next_steps = [
            ("repowise mcp .", "start MCP server for AI assistants"),
            ("repowise init --provider gemini", "generate full documentation"),
            ("repowise dead-code", "explore dead code findings"),
            ("repowise search <query>", "search the index"),
        ]
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
                    f"{result.git_summary.files_indexed} files · {result.git_summary.hotspots} hotspots",
                )
            )

        from repowise.cli.mcp_config import format_setup_instructions

        console.print()
        console.print(build_completion_panel("repowise init complete", metrics))
        console.print()
        console.print(format_setup_instructions(repo_path))
        console.print()
