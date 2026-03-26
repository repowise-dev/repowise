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


async def _persist_index_only(
    repo_path: Path,
    repo_name: str,
    graph_builder: Any,
    parsed_files: list[Any],
    git_metadata_list: list[dict],
    dead_code_report: Any,
    decision_report: Any = None,
) -> None:
    """Persist graph, symbols, git metadata, dead code, and decisions — no pages."""
    from repowise.core.persistence import (
        batch_upsert_graph_edges,
        batch_upsert_graph_nodes,
        batch_upsert_symbols,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import (
        save_dead_code_findings,
        upsert_git_metadata_bulk,
    )
    from repowise.cli.helpers import get_db_url_for_repo

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    async with get_session(sf) as session:
        repo = await upsert_repository(
            session,
            name=repo_name,
            local_path=str(repo_path),
        )
        repo_id = repo.id

        # Graph nodes
        graph = graph_builder.graph()
        pr = graph_builder.pagerank()
        bc = graph_builder.betweenness_centrality()
        cd = graph_builder.community_detection()
        nodes = []
        for node_path in graph.nodes:
            data = graph.nodes[node_path]
            nodes.append({
                "node_id": node_path,
                "symbol_count": data.get("symbol_count", 0),
                "has_error": data.get("has_error", False),
                "is_test": data.get("is_test", False),
                "is_entry_point": data.get("is_entry_point", False),
                "language": data.get("language", "unknown"),
                "pagerank": pr.get(node_path, 0.0),
                "betweenness": bc.get(node_path, 0.0),
                "community_id": cd.get(node_path, 0),
            })
        if nodes:
            await batch_upsert_graph_nodes(session, repo_id, nodes)

        # Graph edges
        edges = []
        for u, v, data in graph.edges(data=True):
            edges.append({
                "source_node_id": u,
                "target_node_id": v,
                "imported_names_json": json.dumps(data.get("imported_names", [])),
                "edge_type": data.get("edge_type", "imports"),
            })
        if edges:
            await batch_upsert_graph_edges(session, repo_id, edges)

        # Symbols
        all_symbols = []
        for pf in parsed_files:
            for sym in pf.symbols:
                sym.file_path = pf.file_info.path
                all_symbols.append(sym)
        if all_symbols:
            await batch_upsert_symbols(session, repo_id, all_symbols)

        # Git metadata
        if git_metadata_list:
            await upsert_git_metadata_bulk(session, repo_id, git_metadata_list)

        # Dead code findings
        if dead_code_report and dead_code_report.findings:
            await save_dead_code_findings(session, repo_id, dead_code_report.findings)

        # Decision records
        if decision_report and decision_report.decisions:
            import dataclasses as _dc

            from repowise.core.persistence.crud import bulk_upsert_decisions

            await bulk_upsert_decisions(
                session,
                repo_id,
                [_dc.asdict(d) for d in decision_report.decisions],
            )

    await engine.dispose()


def _resolve_embedder(embedder_flag: str | None) -> str:
    """Auto-detect embedder from env vars, or use the flag value."""
    if embedder_flag:
        return embedder_flag
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "mock"


@click.command("init")
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name (anthropic, openai, gemini, ollama, mock).")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--embedder", "embedder_name", default=None,
              type=click.Choice(["gemini", "openai", "mock"]),
              help="Embedder for RAG: gemini | openai | mock (default: auto-detect).")
@click.option("--skip-tests", is_flag=True, default=False, help="Skip test files.")
@click.option("--skip-infra", is_flag=True, default=False, help="Skip infrastructure files.")
@click.option("--dry-run", is_flag=True, default=False, help="Show generation plan without running.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip cost confirmation prompt.")
@click.option("--resume", is_flag=True, default=False, help="Resume from last checkpoint.")
@click.option("--force", is_flag=True, default=False, help="Regenerate all pages, ignoring existing.")
@click.option("--concurrency", type=int, default=5, help="Max concurrent LLM calls.")
@click.option("--test-run", is_flag=True, default=False,
              help="Limit generation to top 10 files by PageRank for quick validation.")
@click.option(
    "--index-only",
    is_flag=True,
    default=False,
    help="Index files, git history, graph, and dead code — skip LLM page generation.",
)
@click.option(
    "--exclude", "-x",
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
) -> None:
    """Generate wiki documentation for a codebase.

    PATH defaults to the current directory.
    Use --index-only to run ingestion (AST, graph, git, dead code) without LLM generation.
    """
    from repowise.cli.ui import (
        BRAND,
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

    # Load saved API keys from .repowise/.env (won't overwrite existing env vars)
    load_dotenv(repo_path)

    # ---- Interactive mode (TTY, no explicit flags) ----
    is_interactive = sys.stdin.isatty() and provider_name is None and not index_only

    if is_interactive:
        print_banner(console, repo_name=repo_path.name)
        mode = interactive_mode_select(console)

        if mode == "index_only":
            index_only = True
        elif mode == "advanced":
            # Provider setup first, then advanced config
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
            # Full mode — just provider setup
            provider_name, model = interactive_provider_select(console, model, repo_path=repo_path)

    # Merge exclude_patterns from config.yaml and --exclude/-x flags
    config = load_config(repo_path)
    exclude_patterns: list[str] = list(config.get("exclude_patterns") or []) + list(exclude)

    # Resolve commit limit: CLI flag → config.yaml → default (500)
    resolved_commit_limit: int = commit_limit or config.get("commit_limit") or 500
    resolved_commit_limit = max(1, min(resolved_commit_limit, 5000))
    # Persist to config so `repowise update` picks it up automatically
    if commit_limit is not None:
        config["commit_limit"] = resolved_commit_limit

    # Resolve follow_renames: CLI flag → config.yaml
    resolved_follow_renames: bool = follow_renames or config.get("follow_renames", False)
    if follow_renames:
        config["follow_renames"] = True

    embedder = _resolve_embedder(embedder_name)

    # Compute phase counts for headers
    total_phases = 3 if index_only else 4

    if index_only:
        provider = None
        # Still try to resolve a provider for decision extraction (no page generation)
        decision_provider = None
        try:
            if provider_name or (sys.stdin.isatty() is False):
                decision_provider = resolve_provider(provider_name, model, repo_path)
            elif any(os.environ.get(k) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")):
                decision_provider = resolve_provider(provider_name, model, repo_path)
        except Exception:
            pass  # No provider available — inline markers only

        has_provider = decision_provider is not None
        if is_interactive:
            print_index_only_intro(console, has_provider=has_provider)
        else:
            console.print(f"[bold]repowise index-only[/bold] — {repo_path}")
            console.print("[yellow]Skipping LLM page generation (--index-only)[/yellow]")
            if decision_provider:
                console.print(f"Decision extraction provider: [cyan]{decision_provider.provider_name}[/cyan]")
    else:
        # Non-interactive path: resolve provider from flags/env
        if not is_interactive and provider_name is None and sys.stdin.isatty():
            # Fallback for TTY without interactive mode (shouldn't happen, but safety)
            from repowise.cli.ui import interactive_provider_select as _ips
            provider_name, model = _ips(console, model)

        provider = resolve_provider(provider_name, model, repo_path)
        if not is_interactive:
            console.print(f"[bold]repowise init[/bold] — {repo_path}")
        console.print(f"  Provider: [cyan]{provider.provider_name}[/cyan] / Model: [cyan]{provider.model_name}[/cyan]")
        console.print(f"  Embedder: [cyan]{embedder}[/cyan]")

        # Validate API key with a lightweight call before ingesting
        from repowise.core.providers.base import ProviderError

        with console.status("  Verifying provider connection…", spinner="dots"):
            try:
                run_async(provider.generate("You are a test.", "Reply with OK.", max_tokens=50))
            except ProviderError as exc:
                raise click.ClickException(f"Provider validation failed: {exc}")
        console.print("  [green]✓[/green] Provider connection verified")

    # ---- Phase 1: Ingestion ----
    print_phase_header(
        console, 1, total_phases, "Ingestion",
        "Parsing source files and building the dependency graph",
    )
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from repowise.core.generation import GenerationConfig
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    config = GenerationConfig(max_concurrency=concurrency)

    git_future = None
    git_summary = None
    git_metadata_list: list[dict] = []
    git_meta_map: dict[str, dict] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_traverse = progress.add_task("Traversing files...", total=None)
        task_parse = progress.add_task("Parsing files...", total=None, visible=False)
        task_graph = progress.add_task("Building dependency graph...", total=1, visible=False)
        task_git = progress.add_task("Indexing file history...", total=None, visible=False)
        task_cochange = progress.add_task("Analyzing co-changes...", total=None, visible=False)

        # Start git indexing in a background thread immediately — it uses
        # git ls-files for its own file list, fully independent of traversal.
        git_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="git-idx")
        try:
            from repowise.core.ingestion.git_indexer import GitIndexer

            git_indexer = GitIndexer(
                repo_path,
                commit_limit=resolved_commit_limit,
                follow_renames=resolved_follow_renames,
            )

            def _run_git_indexing() -> tuple:
                def on_start(total: int) -> None:
                    progress.update(task_git, total=total, visible=True)

                def on_file_done() -> None:
                    progress.advance(task_git)

                def on_co_change_start(total: int) -> None:
                    progress.update(task_cochange, total=total, visible=True)

                def on_commit_done() -> None:
                    progress.advance(task_cochange)

                return run_async(
                    git_indexer.index_repo(
                        "",
                        on_start=on_start,
                        on_file_done=on_file_done,
                        on_commit_done=on_commit_done,
                        on_co_change_start=on_co_change_start,
                    )
                )

            git_future = git_executor.submit(_run_git_indexing)
        except Exception as exc:
            progress.console.print(f"[yellow]Git indexing skipped: {exc}[/yellow]")

        # Traverse: walk directory tree (sequential), then process files
        # with parallel I/O (stat + header reads are the bottleneck on Windows).
        traverser = FileTraverser(repo_path, extra_exclude_patterns=exclude_patterns or None)
        all_paths = list(traverser._walk())  # fast directory enumeration
        progress.update(task_traverse, total=len(all_paths))

        file_infos: list[Any] = []
        with ThreadPoolExecutor(max_workers=8) as io_pool:
            futures = [io_pool.submit(traverser._build_file_info, p) for p in all_paths]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception:
                    result = None
                if result is not None:
                    file_infos.append(result)
                progress.advance(task_traverse)

        repo_structure = traverser.get_repo_structure(file_infos)

        # Filter
        if skip_tests:
            file_infos = [fi for fi in file_infos if not fi.is_test]
        if skip_infra:
            file_infos = [fi for fi in file_infos if fi.language not in ("dockerfile", "makefile", "terraform", "shell")]

        # Parse (sequential — GraphBuilder is not thread-safe)
        progress.update(task_parse, total=len(file_infos), visible=True)
        parser = ASTParser()
        parsed_files: list[Any] = []
        source_map: dict[str, bytes] = {}
        graph_builder = GraphBuilder()

        for fi in file_infos:
            try:
                source = Path(fi.abs_path).read_bytes()
                parsed = parser.parse_file(fi, source)
                parsed_files.append(parsed)
                source_map[fi.path] = source
                graph_builder.add_file(parsed)
            except Exception:
                pass  # skip unparseable files
            progress.advance(task_parse)
        progress.update(task_parse, completed=len(file_infos))

        # Build graph
        progress.update(task_graph, visible=True)
        graph_builder.build()
        progress.update(task_graph, completed=1)

        # Wait for git indexing to complete
        if git_future is not None:
            try:
                git_summary, git_metadata_list = git_future.result()
                git_meta_map = {m["file_path"]: m for m in git_metadata_list}
                graph_builder.add_co_change_edges(git_meta_map)
            except Exception as exc:
                progress.console.print(f"[yellow]Git indexing failed: {exc}[/yellow]")

        git_executor.shutdown(wait=False)

    if traverser._oversized_skip_count:
        console.print(
            f"  Skipped [yellow]{traverser._oversized_skip_count}[/yellow] oversized files "
            f"(>{traverser.max_file_size_bytes // 1024} KB)"
        )
    console.print(f"  [green]✓[/green] Ingested [bold]{len(parsed_files)}[/bold] files")
    if git_summary:
        console.print(
            f"  [green]✓[/green] Git: [bold]{git_summary.files_indexed}[/bold] files "
            f"· {git_summary.hotspots} hotspots · {git_summary.stable_files} stable "
            f"({git_summary.duration_seconds:.1f}s)"
        )

    # ---- Test-run: limit to top 10 files by PageRank ----
    if test_run and not index_only:
        import networkx as nx  # noqa: PLC0415

        graph = graph_builder.graph()
        try:
            ranks = nx.pagerank(graph)
        except Exception:
            ranks = {}
        parsed_files = sorted(
            parsed_files,
            key=lambda pf: ranks.get(pf.file_info.path, 0),
            reverse=True,
        )[:10]
        console.print(f"[yellow]Test run: limiting to {len(parsed_files)} files[/yellow]")

    # ---- Phase 2: Analysis ----
    print_phase_header(
        console, 2, total_phases, "Analysis",
        "Dead code detection and architectural decision extraction",
    )

    dead_code_report = None
    try:
        from repowise.core.analysis.dead_code import DeadCodeAnalyzer

        analyzer = DeadCodeAnalyzer(graph_builder.graph(), git_meta_map)
        dead_code_report = analyzer.analyze()
        unreachable = sum(
            1 for f in dead_code_report.findings if f.kind.value == "unreachable_file"
        )
        unused_exports = sum(
            1 for f in dead_code_report.findings if f.kind.value == "unused_export"
        )
        console.print(
            f"  [green]✓[/green] Dead code: [yellow]{unreachable}[/yellow] unreachable files "
            f"· [yellow]{unused_exports}[/yellow] unused exports "
            f"(~{dead_code_report.deletable_lines:,} lines)"
        )
    except Exception as exc:
        console.print(f"  [yellow]Dead code detection skipped: {exc}[/yellow]")

    decision_report = None
    try:
        from repowise.core.analysis.decision_extractor import DecisionExtractor

        # Use decision_provider in index-only mode (provider is None but
        # decision_provider may still be set for decision extraction)
        _decision_llm = provider or (decision_provider if index_only else None)
        extractor = DecisionExtractor(
            repo_path=repo_path,
            provider=_decision_llm,
            graph=graph_builder.graph(),
            git_meta_map=git_meta_map,
            parsed_files=parsed_files,
        )
        decision_report = run_async(extractor.extract_all())
        inline = decision_report.by_source.get("inline_marker", 0)
        readme = decision_report.by_source.get("readme_mining", 0)
        git_arch = decision_report.by_source.get("git_archaeology", 0)
        console.print(
            f"  [green]✓[/green] Decisions: [green]{inline}[/green] inline "
            f"· [yellow]{readme}[/yellow] from docs · [yellow]{git_arch}[/yellow] from git"
        )
    except Exception as exc:
        console.print(f"  [yellow]Decision extraction skipped: {exc}[/yellow]")

    # ---- Index-only: persist and show completion panel ----
    if index_only:
        print_phase_header(console, 3, total_phases, "Persistence", "Saving to database")

        with console.status("  Persisting index…", spinner="dots"):
            run_async(_persist_index_only(
                repo_path=repo_path,
                repo_name=repo_path.name,
                graph_builder=graph_builder,
                parsed_files=parsed_files,
                git_metadata_list=git_metadata_list,
                dead_code_report=dead_code_report,
                decision_report=decision_report,
            ))
        # Persist commit_limit to config so `repowise update` picks it up
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
                pass  # No yaml — commit_limit will use default next time

        # MCP config
        from repowise.cli.mcp_config import save_mcp_config
        save_mcp_config(repo_path)

        elapsed = time.monotonic() - start

        # Collect stats for the completion panel
        _graph = graph_builder.graph()
        _langs = {fi.language for fi in file_infos if hasattr(fi, "language") and fi.language}
        _dc_unreachable = sum(1 for f in (dead_code_report.findings if dead_code_report else []) if f.kind.value == "unreachable_file")
        _dc_unused = sum(1 for f in (dead_code_report.findings if dead_code_report else []) if f.kind.value == "unused_export")
        _n_decisions = sum(decision_report.by_source.values()) if decision_report else 0

        metrics: list[tuple[str, str]] = [
            ("Files indexed", str(len(parsed_files))),
            ("Symbols", f"{sum(len(pf.symbols) for pf in parsed_files):,}"),
            ("Languages", str(len(_langs))),
            ("Elapsed", format_elapsed(elapsed)),
            ("", ""),
            ("Graph", f"{_graph.number_of_nodes()} nodes · {_graph.number_of_edges()} edges"),
            ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
            ("Decisions", str(_n_decisions)),
        ]
        if git_summary:
            metrics.append(("Git history", f"{git_summary.files_indexed} files · {git_summary.hotspots} hotspots"))

        next_steps = [
            ("repowise mcp .", "start MCP server for AI assistants"),
            ("repowise init --provider gemini", "generate full documentation"),
            ("repowise dead-code", "explore dead code findings"),
            ("repowise search <query>", "search the index"),
        ]
        console.print()
        console.print(build_completion_panel("repowise index complete", metrics, next_steps=next_steps))
        console.print()
        return

    # ---- Phase 3: Generation ----
    print_phase_header(
        console, 3, total_phases, "Generation",
        f"Generating wiki pages with {provider.provider_name} / {provider.model_name}",
    )

    plans = build_generation_plan(parsed_files, graph_builder, config, skip_tests, skip_infra)
    est = estimate_cost(plans, provider.provider_name, provider.model_name)

    from rich.panel import Panel as _Panel

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

    if est.estimated_cost_usd > 2.00 and not yes:
        if not click.confirm("  Estimated cost exceeds $2.00. Continue?"):
            console.print("[yellow]Aborted.[/yellow]")
            return

    # ---- Generate pages ----
    from repowise.core.generation import ContextAssembler, JobSystem, PageGenerator
    from repowise.core.persistence.embedder import MockEmbedder
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    assembler = ContextAssembler(config)

    # Build vector store for RAG context (B1)
    embedder_impl: Any
    embedder_resolved = _resolve_embedder(embedder_name)
    if embedder_resolved == "gemini":
        try:
            from repowise.core.persistence.gemini_embedder import GeminiEmbedder
            embedder_impl = GeminiEmbedder()
        except Exception:
            embedder_impl = MockEmbedder()
    elif embedder_resolved == "openai":
        try:
            from repowise.core.persistence.openai_embedder import OpenAIEmbedder
            embedder_impl = OpenAIEmbedder()
        except Exception:
            embedder_impl = MockEmbedder()
    else:
        embedder_impl = MockEmbedder()
    # Try LanceDB for persistent vector storage, fall back to in-memory
    lance_dir = repo_path / ".repowise" / "lancedb"
    try:
        from repowise.core.persistence.vector_store import LanceDBVectorStore

        lance_dir.mkdir(parents=True, exist_ok=True)
        vector_store = LanceDBVectorStore(str(lance_dir), embedder=embedder_impl)
    except ImportError:
        vector_store = InMemoryVectorStore(embedder_impl)

    generator = PageGenerator(provider, assembler, config, vector_store=vector_store)

    job_system = None
    jobs_dir = Path(config.jobs_dir)
    if not jobs_dir.is_absolute():
        jobs_dir = repo_path / jobs_dir
    job_system = JobSystem(jobs_dir)

    repo_name = repo_path.name

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as gen_progress:
        task_gen = gen_progress.add_task("Generating pages...", total=est.total_pages)

        def on_page_done(page_type: str) -> None:
            gen_progress.advance(task_gen)
            gen_progress.update(task_gen, description=f"Generating pages... [{page_type}]")

        generated_pages = run_async(
            generator.generate_all(
                parsed_files,
                source_map,
                graph_builder,
                repo_structure,
                repo_name,
                job_system=job_system,
                on_page_done=on_page_done,
                git_meta_map=git_meta_map if git_meta_map else None,
            )
        )

    console.print(f"  [green]✓[/green] Generated [bold]{len(generated_pages)}[/bold] pages")

    # ---- Phase 4: Persistence ----
    print_phase_header(
        console, 4, total_phases, "Persistence",
        "Saving to database and building search index",
    )
    async def _persist() -> None:
        from repowise.core.persistence import (
            FullTextSearch,
            batch_upsert_graph_nodes,
            batch_upsert_graph_edges,
            batch_upsert_symbols,
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_page_from_generated,
            upsert_repository,
        )
        from repowise.core.persistence.crud import (
            save_dead_code_findings,
            upsert_git_metadata_bulk,
        )

        from repowise.cli.helpers import get_db_url_for_repo

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(
                session,
                name=repo_name,
                local_path=str(repo_path),
            )
            repo_id = repo.id

            for page in generated_pages:
                await upsert_page_from_generated(session, page, repo_id)

            # Graph nodes
            graph = graph_builder.graph()
            pr = graph_builder.pagerank()
            bc = graph_builder.betweenness_centrality()
            cd = graph_builder.community_detection()
            nodes = []
            for node_path in graph.nodes:
                data = graph.nodes[node_path]
                nodes.append({
                    "node_id": node_path,
                    "symbol_count": data.get("symbol_count", 0),
                    "has_error": data.get("has_error", False),
                    "is_test": data.get("is_test", False),
                    "is_entry_point": data.get("is_entry_point", False),
                    "language": data.get("language", "unknown"),
                    "pagerank": pr.get(node_path, 0.0),
                    "betweenness": bc.get(node_path, 0.0),
                    "community_id": cd.get(node_path, 0),
                })
            if nodes:
                await batch_upsert_graph_nodes(session, repo_id, nodes)

            # Graph edges
            edges = []
            for u, v, data in graph.edges(data=True):
                edges.append({
                    "source_node_id": u,
                    "target_node_id": v,
                    "imported_names_json": json.dumps(data.get("imported_names", [])),
                    "edge_type": data.get("edge_type", "imports"),
                })
            if edges:
                await batch_upsert_graph_edges(session, repo_id, edges)

            # Symbols — pass actual Symbol objects (duck-typed)
            all_symbols = []
            for pf in parsed_files:
                for sym in pf.symbols:
                    # Attach file_path to symbol for batch_upsert_symbols
                    sym.file_path = pf.file_info.path
                    all_symbols.append(sym)
            if all_symbols:
                await batch_upsert_symbols(session, repo_id, all_symbols)

            # Git metadata
            if git_metadata_list:
                await upsert_git_metadata_bulk(session, repo_id, git_metadata_list)

            # Dead code findings
            if dead_code_report and dead_code_report.findings:
                await save_dead_code_findings(
                    session, repo_id, dead_code_report.findings
                )

            # Decision records
            if decision_report and decision_report.decisions:
                import dataclasses as _dc

                from repowise.core.persistence.crud import bulk_upsert_decisions

                await bulk_upsert_decisions(
                    session,
                    repo_id,
                    [_dc.asdict(d) for d in decision_report.decisions],
                )

        # FTS indexing
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page in generated_pages:
            await fts.index(page.page_id, page.title, page.content)

        await engine.dispose()

    with console.status("  Persisting to database…", spinner="dots"):
        run_async(_persist())
    console.print("  [green]✓[/green] Database updated")

    # ---- State ----
    # Query actual DB page count (not just current job's pages)
    async def _count_db_pages() -> int:
        from sqlalchemy import func as sa_func, select as sa_select

        from repowise.core.persistence import create_engine, create_session_factory, get_session
        from repowise.core.persistence.models import Page, Repository

        from repowise.cli.helpers import get_db_url_for_repo as _get_url

        _engine = create_engine(_get_url(repo_path))
        _sf = create_session_factory(_engine)
        async with get_session(_sf) as _sess:
            repo_result = await _sess.execute(
                sa_select(Repository.id).where(
                    Repository.local_path == str(repo_path)
                )
            )
            _repo_id = repo_result.scalar_one_or_none()
            if _repo_id is None:
                await _engine.dispose()
                return len(generated_pages)  # fallback

            result = await _sess.execute(
                sa_select(sa_func.count()).select_from(Page).where(
                    Page.repository_id == _repo_id
                )
            )
            count = result.scalar_one()
        await _engine.dispose()
        return count

    head = get_head_commit(repo_path)
    state = load_state(repo_path)
    state["last_sync_commit"] = head
    state["total_pages"] = run_async(_count_db_pages())
    state["provider"] = provider.provider_name
    state["model"] = provider.model_name
    total_tokens = sum(p.total_tokens for p in generated_pages)
    state["total_tokens"] = total_tokens
    save_state(repo_path, state)

    save_config(
        repo_path,
        provider.provider_name,
        provider.model_name,
        embedder,
        exclude_patterns=exclude_patterns if exclude_patterns else None,
        commit_limit=resolved_commit_limit if commit_limit is not None else None,
    )

    # ---- Completion ----
    from repowise.cli.mcp_config import format_setup_instructions, save_mcp_config

    save_mcp_config(repo_path)

    elapsed = time.monotonic() - start

    _dc_unreachable = sum(1 for f in (dead_code_report.findings if dead_code_report else []) if f.kind.value == "unreachable_file")
    _dc_unused = sum(1 for f in (dead_code_report.findings if dead_code_report else []) if f.kind.value == "unused_export")
    _n_decisions = sum(decision_report.by_source.values()) if decision_report else 0

    metrics = [
        ("Pages generated", str(len(generated_pages))),
        ("Total tokens", f"{total_tokens:,}"),
        ("Provider", f"{provider.provider_name} / {provider.model_name}"),
        ("Elapsed", format_elapsed(elapsed)),
        ("", ""),
        ("Dead code", f"{_dc_unreachable} unreachable · {_dc_unused} unused exports"),
        ("Decisions", str(_n_decisions)),
    ]
    if git_summary:
        metrics.append(("Git history", f"{git_summary.files_indexed} files · {git_summary.hotspots} hotspots"))

    console.print()
    console.print(build_completion_panel("repowise init complete", metrics))
    console.print()
    console.print(format_setup_instructions(repo_path))
    console.print()
