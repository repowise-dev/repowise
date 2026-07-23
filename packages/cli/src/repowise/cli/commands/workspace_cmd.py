"""``repowise workspace`` — manage multi-repo workspaces."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.table import Table

from repowise.cli._setup import configure_cli_logging
from repowise.cli.helpers import (
    console,
    find_workspace_root,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
)
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode

if TYPE_CHECKING:
    from repowise.core.workspace.config import WorkspaceConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_workspace(start: Path | None = None) -> tuple[Path, WorkspaceConfig]:  # type: ignore[name-defined]
    """Load the workspace config or abort with a helpful message.

    Returns ``(ws_root, ws_config)``.
    """
    from repowise.core.workspace.config import WorkspaceConfig

    ws_root = find_workspace_root(start)
    if ws_root is None:
        raise click.ClickException(
            "No .repowise-workspace.yaml found. "
            "Run 'repowise init <workspace-dir>' to create a workspace."
        )
    ws_config = WorkspaceConfig.load(ws_root)
    return ws_root, ws_config


# ---------------------------------------------------------------------------
# Command group
# ---------------------------------------------------------------------------


@click.group("workspace")
def workspace_group() -> None:
    """Manage multi-repo workspaces."""


# ---------------------------------------------------------------------------
# workspace list
# ---------------------------------------------------------------------------


@workspace_group.command("list")
@click.argument("path", required=False, default=None)
def workspace_list(path: str | None) -> None:
    """Show all repos in the workspace with their status."""
    from repowise.cli.helpers import get_repowise_dir
    from repowise.core.workspace import check_repo_staleness

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    table = Table(title=f"Workspace: {ws_root.name}")
    table.add_column("Repo", style="cyan", min_width=16)
    table.add_column("Path", style="dim")
    table.add_column("Files", justify="right")
    table.add_column("Symbols", justify="right")
    table.add_column("Indexed", style="dim")
    table.add_column("Status")

    indexed_count = 0

    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        repowise_dir = get_repowise_dir(abs_path)

        label = entry.alias
        if entry.alias == ws_config.default_repo:
            label += " [bold](primary)[/bold]"

        rel_path = entry.path

        if not repowise_dir.exists():
            table.add_row(label, rel_path, "-", "-", "-", "[yellow]not indexed[/yellow]")
            continue

        indexed_count += 1

        # Query file/symbol counts from DB
        file_count, symbol_count = _query_repo_counts(abs_path)

        # Indexed timestamp
        indexed_ago = _format_relative_time(entry.indexed_at)

        # Staleness check
        is_stale, _head, behind = check_repo_staleness(abs_path, entry.last_commit_at_index)

        if is_stale and behind > 0:
            status = f"[yellow]{behind} new commit(s)[/yellow]"
        elif is_stale:
            status = "[yellow]stale[/yellow]"
        elif file_count > 0:
            status = "[green]up to date[/green]"
        else:
            status = "[yellow]empty[/yellow]"

        table.add_row(
            label,
            rel_path,
            str(file_count),
            f"{symbol_count:,}",
            indexed_ago,
            status,
        )

    console.print(table)

    total_repos = len(ws_config.repos)
    summary = f"\n  {indexed_count}/{total_repos} repos indexed."
    if ws_config.default_repo:
        summary += f" Default: {ws_config.default_repo}"
    console.print(summary)


def _query_repo_counts(repo_path: Path) -> tuple[int, int]:
    """Return ``(file_count, symbol_count)`` from a repo's DB, or ``(0, 0)``."""
    from repowise.cli.helpers import get_db_url_for_repo, get_repowise_dir

    db_path = get_repowise_dir(repo_path) / "wiki.db"
    if not db_path.exists():
        return 0, 0

    async def _query() -> tuple[int, int]:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.models import GraphNode, Repository

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        try:
            async with get_session(sf) as session:
                repo_result = await session.execute(
                    sa_select(Repository.id).where(Repository.local_path == str(repo_path))
                )
                repo_id = repo_result.scalar_one_or_none()
                if repo_id is None:
                    return 0, 0
                file_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "file",
                    )
                )
                symbol_result = await session.execute(
                    sa_select(sa_func.count())
                    .select_from(GraphNode)
                    .where(
                        GraphNode.repository_id == repo_id,
                        GraphNode.node_type == "symbol",
                    )
                )
                return file_result.scalar_one(), symbol_result.scalar_one()
        finally:
            await engine.dispose()

    try:
        return run_async(_query())
    except Exception:
        return 0, 0


def _format_relative_time(iso_timestamp: str | None) -> str:
    """Format an ISO 8601 timestamp as a human-readable relative string."""
    if not iso_timestamp:
        return "-"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except Exception:
        return iso_timestamp[:10] if len(iso_timestamp) >= 10 else iso_timestamp


# ---------------------------------------------------------------------------
# workspace add
# ---------------------------------------------------------------------------


@workspace_group.command("add")
@click.argument("path")
@click.option("--alias", default=None, help="Short name for the repo (default: directory name).")
@click.option(
    "--index/--no-index",
    "run_index",
    default=True,
    show_default=True,
    help="Run full indexing on the repo after adding it (graph, git, dead code).",
)
@click.option(
    "--docs/--no-docs",
    "run_docs",
    default=None,
    help=(
        "Generate LLM documentation pages after indexing. Defaults to ON when a "
        "provider is configured (in the primary repo's config or via env), OFF "
        "otherwise. Skipped silently when --no-index is passed."
    ),
)
@click.option(
    "--provider", "provider_name", default=None, help="LLM provider name (overrides primary's)."
)
@click.option("--model", default=None, help="Model identifier (overrides primary's).")
@click.option(
    "--concurrency", type=int, default=10, help="Max concurrent LLM calls during doc generation."
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def workspace_add(
    path: str,
    alias: str | None,
    run_index: bool,
    run_docs: bool | None,
    provider_name: str | None,
    model: str | None,
    concurrency: int,
    verbose: bool,
) -> None:
    """Add a repo to the workspace and (by default) index + generate docs for it.

    PATH is a relative or absolute path to a git repository.

    Defaults are designed so the repo immediately appears with complete
    intelligence in the web UI and MCP server:
      - ``--index``  (default ON) runs the full ingestion pipeline
      - ``--docs``   (auto)        generates wiki pages when a provider is
                                    available, otherwise skips with a notice
    Use ``--no-index`` to only register the entry without indexing, or
    ``--no-docs`` to index without LLM generation.
    """
    configure_cli_logging(verbose=verbose)

    from repowise.core.workspace.config import RepoEntry

    repo_path = Path(path).resolve()
    ws_root, ws_config = _require_workspace(Path.cwd())

    # Validate path exists
    if not repo_path.exists():
        raise click.ClickException(f"Path does not exist: {repo_path}")

    # Validate it is a git repo
    if not (repo_path / ".git").exists():
        raise click.ClickException(f"Not a git repository (no .git found): {repo_path}")

    # Default alias to directory name
    if alias is None:
        alias = repo_path.name.lower()

    # Validate alias is not already in workspace
    if ws_config.get_repo(alias) is not None:
        raise click.ClickException(
            f"Alias '{alias}' already exists in this workspace. "
            "Use --alias to specify a different name."
        )

    # Build a relative path from ws_root
    try:
        rel_path = repo_path.relative_to(ws_root).as_posix()
    except ValueError:
        # Repo is outside workspace root — store absolute path as-is
        rel_path = repo_path.as_posix()

    entry = RepoEntry(path=rel_path, alias=alias)

    try:
        ws_config.add_repo(entry)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    ws_config.save(ws_root)
    console.print(f"[green]✓[/green] Added repo '{alias}' ({rel_path}) to workspace.")

    if not run_index:
        console.print(
            "[yellow]Skipping index[/yellow] (--no-index). "
            f"Run [bold]repowise update --repo {alias}[/bold] to index later."
        )
        return

    # Resolve whether docs should run.
    resolved_docs, docs_skip_reason = _resolve_docs_flag(
        run_docs=run_docs,
        provider_name=provider_name,
        ws_root=ws_root,
        ws_config=ws_config,
    )

    _run_index_for_repo(
        repo_path,
        alias,
        ws_root,
        ws_config,
        generate_docs=resolved_docs,
        provider_name=provider_name,
        model=model,
        concurrency=concurrency,
        docs_skip_reason=docs_skip_reason,
    )


def _resolve_docs_flag(
    *,
    run_docs: bool | None,
    provider_name: str | None,
    ws_root: Path,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
) -> tuple[bool, str | None]:
    """Decide whether ``workspace add`` should generate docs by default.

    Priority:
      1. Explicit ``--docs`` or ``--no-docs``.
      2. ``--provider`` flag forces docs ON.
      3. Primary repo's ``.repowise/config.yaml`` has a provider → docs ON,
         reusing the same provider settings.
      4. ``REPOWISE_PROVIDER`` env var or detectable API key → docs ON.
      5. Otherwise docs OFF, with a skip reason for the completion notice.
    """
    if run_docs is True:
        return True, None
    if run_docs is False:
        return False, "--no-docs flag"
    if provider_name is not None:
        return True, None

    # Check primary repo config
    from repowise.cli.helpers import load_config

    primary = ws_config.get_primary()
    if primary is not None:
        primary_path = (ws_root / primary.path).resolve()
        cfg = load_config(primary_path)
        if cfg.get("provider"):
            return True, None

    # Env-detected provider
    import os as _os

    env_provider = _os.environ.get("REPOWISE_PROVIDER")
    if env_provider:
        return True, None
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "KIMI_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        if _os.environ.get(key):
            return True, None

    return False, "no provider configured"


def _inherit_distill_verdict(repo_path: Path, primary_cfg: dict) -> None:
    """Copy the primary repo's explicit distill rewrite-hook verdict.

    ``repowise init`` records ``distill.commands.enabled`` in every repo it
    asks about; a repo added later would otherwise default to enabled (with
    the ``ask`` posture) the moment ``.repowise/`` exists — even after a
    workspace-wide decline. No explicit verdict on the primary → leave the
    new repo's config untouched.
    """
    distill = primary_cfg.get("distill")
    commands = distill.get("commands") if isinstance(distill, dict) else None
    enabled = commands.get("enabled") if isinstance(commands, dict) else None
    if not isinstance(enabled, bool):
        return
    import contextlib

    from repowise.cli.helpers import save_distill_commands_enabled

    # Inheritance is best-effort; never fail an add over it.
    with contextlib.suppress(Exception):
        save_distill_commands_enabled(repo_path, enabled=enabled)


def inherit_workspace_distill_verdict(repo_path: Path) -> None:
    """Best-effort backfill of a workspace member's distill verdict.

    Repos that get ``.repowise/`` outside the init flow (``workspace add
    --no-index`` followed by an update, or first-time indexing via
    ``repowise update``) never recorded a ``distill.commands.enabled``
    verdict, so a globally installed rewrite hook would treat them as
    enabled. Copies the primary repo's explicit verdict when the member has
    none of its own. No-op when the repo has no ``.repowise/`` yet, sits
    outside a workspace, is itself the primary, already holds a verdict, or
    the primary never recorded one.
    """
    import contextlib

    with contextlib.suppress(Exception):
        if not (repo_path / ".repowise").is_dir():
            return
        from repowise.cli.helpers import load_config
        from repowise.core.workspace.config import WorkspaceConfig

        cfg = load_config(repo_path)
        distill = cfg.get("distill")
        commands = distill.get("commands") if isinstance(distill, dict) else None
        if isinstance(commands, dict) and isinstance(commands.get("enabled"), bool):
            return  # repo already has its own verdict
        ws_root = find_workspace_root(repo_path)
        if ws_root is None:
            return
        primary = WorkspaceConfig.load(ws_root).get_primary()
        if primary is None:
            return
        primary_path = (ws_root / primary.path).resolve()
        if primary_path == repo_path.resolve():
            return
        _inherit_distill_verdict(repo_path, load_config(primary_path))


def _run_index_for_repo(
    repo_path: Path,
    alias: str,
    ws_root: Path,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
    *,
    generate_docs: bool = False,
    provider_name: str | None = None,
    model: str | None = None,
    concurrency: int = 10,
    docs_skip_reason: str | None = None,
) -> None:
    """Run the ingestion pipeline on a single repo, optionally with LLM docs.

    Updates the workspace config entry, persists results to the per-repo
    DB, writes ``.repowise/state.json`` (so ``repowise update`` knows the
    base commit), saves provider/model into ``config.yaml`` when docs ran,
    and re-runs cross-repo hooks so contracts/co-changes are fresh.
    """
    from datetime import datetime

    from repowise.cli.helpers import (
        ensure_repowise_dir,
        get_head_commit,
        resolve_provider,
        save_config,
        save_state,
    )
    from repowise.core.workspace.update import run_cross_repo_hooks

    console.print(f"  Indexing [cyan]{alias}[/cyan]…")

    # Reuse the primary repo's provider/embedder/exclude settings when the
    # caller hasn't overridden them.
    primary = ws_config.get_primary()
    primary_cfg: dict = {}
    if primary is not None:
        from repowise.cli.helpers import load_config as _load_cfg

        primary_cfg = _load_cfg((ws_root / primary.path).resolve())

    effective_provider = provider_name or primary_cfg.get("provider")
    effective_model = model or primary_cfg.get("model")
    embedder_name = primary_cfg.get("embedder", "mock")
    exclude_patterns = list(primary_cfg.get("exclude_patterns") or [])
    commit_limit = primary_cfg.get("commit_limit", 500)

    # Resolve the provider once. If docs were requested but provider
    # resolution fails, fall back to index-only with a loud notice instead
    # of silently producing an empty wiki.
    provider = None
    if generate_docs:
        try:
            provider = resolve_provider(
                effective_provider,
                effective_model,
                repo_path=repo_path,
            )
            console.print(
                f"  Provider: [cyan]{provider.provider_name}[/cyan] / "
                f"Model: [cyan]{provider.model_name}[/cyan]"
            )
        except Exception as exc:
            console.print(f"  [yellow]Provider unavailable ({exc}); skipping docs.[/yellow]")
            generate_docs = False
            docs_skip_reason = f"provider failure: {exc}"

    ensure_repowise_dir(repo_path)
    _inherit_distill_verdict(repo_path, primary_cfg)

    async def _do_index() -> tuple[int, int, int]:
        # Shared full-index step (run pipeline, persist, export the curated KG
        # artifact so doc generation can load curated module grouping) — the
        # same helper the workspace updater's first-time indexing uses.
        from repowise.core.pipeline.full_index import index_repo_full

        result = await index_repo_full(
            repo_path,
            commit_depth=int(commit_limit) if commit_limit else 500,
            exclude_patterns=exclude_patterns,
        )
        return result.file_count, result.symbol_count, 0

    try:
        file_count, symbol_count, _ = run_async(_do_index())
        console.print(f"  [green]✓[/green] {file_count} files, {symbol_count:,} symbols")
    except Exception as exc:
        console.print(f"[yellow]Warning:[/yellow] Indexing failed for '{alias}': {exc}")
        return

    # Run LLM doc generation through the existing single-repo init pathway
    # so we get cost gating, cascading, and full parity with `repowise init`.
    generated_pages = 0
    resolved_reasoning = resolve_reasoning(config=primary_cfg)
    if generate_docs and provider is not None:
        try:
            generated_pages = _generate_docs_for_added_repo(
                repo_path=repo_path,
                provider=provider,
                embedder_name=embedder_name,
                concurrency=concurrency,
                reasoning=resolved_reasoning,
                exclude_patterns=exclude_patterns,
            )
            console.print(f"  [green]✓[/green] Generated {generated_pages} pages")
        except Exception as exc:
            console.print(f"  [yellow]Doc generation failed: {exc}[/yellow]")
            docs_skip_reason = f"generation error: {exc}"

    # Persist state.json so `repowise update` has a baseline commit.
    head = get_head_commit(repo_path)
    state: dict = {
        "last_sync_commit": head,
        "total_pages": generated_pages,
        # No template fallback on this path: without a provider the added repo
        # is indexed with no pages at all.
        **docs_mode_state_fields("llm" if generate_docs and provider is not None else "none"),
    }
    if generate_docs and provider is not None:
        state["provider"] = provider.provider_name
        state["model"] = provider.model_name
    save_state(repo_path, state)

    # Persist provider settings into the added repo's config.yaml so future
    # `repowise update` runs don't have to re-prompt.
    if generate_docs and provider is not None:
        save_config(
            repo_path,
            provider.provider_name,
            provider.model_name,
            embedder_name,
            exclude_patterns=exclude_patterns or None,
            commit_limit=int(commit_limit) if commit_limit else None,
            reasoning=resolved_reasoning,
        )

    # Update workspace config entry
    entry = ws_config.get_repo(alias)
    if entry is not None:
        entry.indexed_at = datetime.now(UTC).isoformat()
        entry.last_commit_at_index = head
    ws_config.save(ws_root)

    # Cross-repo hooks — best effort; never fail the add command.
    try:
        run_async(run_cross_repo_hooks(ws_config, ws_root, [alias]))
    except Exception as exc:
        console.print(f"[yellow]Cross-repo hook update skipped: {exc}[/yellow]")

    # Honest completion notice — exact remediation command for the
    # docs-skipped case.
    if resolve_docs_mode(state) == "none":
        reason = docs_skip_reason or "docs disabled"
        console.print(f"\n[yellow]Note:[/yellow] '{alias}' indexed without docs ({reason}).")
        console.print(
            f"  Run [bold]repowise update --repo {alias} --docs[/bold] to generate documentation."
        )


def _generate_docs_for_added_repo(
    *,
    repo_path: Path,
    provider: object,
    embedder_name: str,
    concurrency: int,
    reasoning: str,
    exclude_patterns: list[str],
) -> int:
    """Generate wiki pages for a newly-added workspace repo.

    Lives in this module (rather than importing from init_cmd) to avoid
    circular imports — init_cmd is large and pulls in CLI UI helpers that
    would explode the import graph. Uses the same generation primitives
    as `repowise init`.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.generation import (
        ContextAssembler,
        GenerationConfig,
        PageGenerator,
    )
    from repowise.core.ingestion import (
        ASTParser,
        FileTraverser,
        GraphBuilder,
    )
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_pages_from_generated,
        upsert_repository,
    )

    # Re-parse files. The pipeline persisted graph data already; for doc
    # generation we need parsed files in-memory.
    traverser = FileTraverser(repo_path, extra_exclude_patterns=exclude_patterns or None)
    file_infos = list(traverser.traverse())
    repo_structure = traverser.get_repo_structure()
    parser = ASTParser()
    graph_builder = GraphBuilder(repo_path)
    parsed_files = []
    source_map: dict = {}
    for fi in file_infos:
        try:
            source = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, source)
            parsed_files.append(parsed)
            source_map[fi.path] = source
            graph_builder.add_file(parsed)
        except Exception:
            continue

    from repowise.core.ingestion import wire_tsconfig_resolver

    wire_tsconfig_resolver(graph_builder, repo_path)
    graph_builder.build()

    from repowise.core.repo_config import load_repo_config

    repo_cfg = load_repo_config(repo_path)
    config = GenerationConfig.from_repo_config(
        repo_cfg,
        max_concurrency=concurrency,
        reasoning=reasoning,
        wiki_style=repo_cfg.get("wiki_style", "comprehensive"),
        language=repo_cfg.get("language", "en"),
    )
    assembler = ContextAssembler(config)
    generator = PageGenerator(
        provider, assembler, config, language=config.language, repo_path=repo_path
    )

    async def _do() -> int:
        pages = await generator.generate_all(
            parsed_files,
            source_map,
            graph_builder,
            repo_structure,
            repo_path.name,
            repo_path=repo_path,
        )

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session,
                name=repo_path.name,
                local_path=str(repo_path),
            )
            await upsert_pages_from_generated(session, pages, repo.id)
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for p in pages:
            await fts.index(p.page_id, p.title, p.content)
        await engine.dispose()
        return len(pages)

    return run_async(_do())


# ---------------------------------------------------------------------------
# workspace remove
# ---------------------------------------------------------------------------


@workspace_group.command("remove")
@click.argument("alias")
def workspace_remove(alias: str) -> None:
    """Remove a repo from the workspace config.

    The repo's .repowise/ directory is preserved; only the workspace
    entry is deleted.
    """
    ws_root, ws_config = _require_workspace(Path.cwd())

    entry = ws_config.get_repo(alias)
    if entry is None:
        available = ", ".join(ws_config.repo_aliases()) or "(none)"
        raise click.ClickException(f"No repo with alias '{alias}' found. Available: {available}")

    is_default = alias == ws_config.default_repo

    removed = ws_config.remove_repo(alias)
    if removed is None:
        raise click.ClickException(f"Failed to remove repo '{alias}'.")

    ws_config.save(ws_root)
    console.print(f"[green]✓[/green] Removed repo '{alias}' from workspace.")

    if is_default and ws_config.repos:
        new_default = ws_config.repos[0].alias
        console.print(
            f"[yellow]Note:[/yellow] '{alias}' was the default repo. "
            f"New default is '{new_default}'."
        )
    elif is_default:
        console.print("[yellow]Note:[/yellow] Workspace now has no repos and no default.")

    console.print(f"  (Indexed data at {removed.path}/.repowise/ was [bold]not[/bold] deleted.)")


# ---------------------------------------------------------------------------
# workspace scan
# ---------------------------------------------------------------------------


@workspace_group.command("scan")
@click.argument("path", required=False, default=None)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Auto-add all discovered repos without prompting.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def workspace_scan(path: str | None, yes: bool, verbose: bool) -> None:
    """Scan the workspace root for new repos not yet in the config."""
    configure_cli_logging(verbose=verbose)

    from repowise.core.workspace.config import RepoEntry
    from repowise.core.workspace.scanner import scan_for_repos

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    console.print(f"Scanning [cyan]{ws_root}[/cyan] for git repositories…")
    scan_result = scan_for_repos(ws_root)

    existing_aliases = set(ws_config.repo_aliases())
    existing_paths = {(ws_root / e.path).resolve().as_posix() for e in ws_config.repos}

    new_repos = [
        r
        for r in scan_result.repos
        if r.path.as_posix() not in existing_paths and r.alias not in existing_aliases
    ]

    if not new_repos:
        console.print("[green]No new repositories discovered.[/green]")
        return

    console.print(f"\nFound [bold]{len(new_repos)}[/bold] new repo(s) not in workspace:\n")
    for repo in new_repos:
        indexed_marker = " [green](indexed)[/green]" if repo.has_repowise else ""
        console.print(f"  [cyan]{repo.alias}[/cyan] — {repo.path}{indexed_marker}")

    console.print()

    added = 0
    for repo in new_repos:
        alias = repo.alias

        # Resolve alias collisions
        base_alias = alias
        suffix = 2
        while ws_config.get_repo(alias) is not None:
            alias = f"{base_alias}-{suffix}"
            suffix += 1

        if yes:
            do_add = True
        else:
            do_add = click.confirm(f"Add '{alias}' ({repo.path.relative_to(ws_root)})?")

        if do_add:
            try:
                rel_path = repo.path.relative_to(ws_root).as_posix()
            except ValueError:
                rel_path = repo.path.as_posix()

            entry = RepoEntry(path=rel_path, alias=alias)
            ws_config.add_repo(entry)
            console.print(f"  [green]✓[/green] Added '{alias}'.")
            added += 1

    if added > 0:
        ws_config.save(ws_root)
        console.print(f"\n[green]{added} repo(s) added to workspace.[/green]")
    else:
        console.print("\nNo repos added.")


# ---------------------------------------------------------------------------
# workspace set-default
# ---------------------------------------------------------------------------


@workspace_group.command("set-default")
@click.argument("alias")
def workspace_set_default(alias: str) -> None:
    """Change the default (primary) repo in the workspace."""
    ws_root, ws_config = _require_workspace(Path.cwd())

    entry = ws_config.get_repo(alias)
    if entry is None:
        available = ", ".join(ws_config.repo_aliases()) or "(none)"
        raise click.ClickException(f"No repo with alias '{alias}' found. Available: {available}")

    previous_default = ws_config.default_repo

    # Update is_primary flags on all entries
    for repo_entry in ws_config.repos:
        repo_entry.is_primary = repo_entry.alias == alias

    ws_config.default_repo = alias
    ws_config.save(ws_root)

    if previous_default and previous_default != alias:
        console.print(
            f"[green]✓[/green] Default repo changed from "
            f"'[dim]{previous_default}[/dim]' to '[bold]{alias}[/bold]'."
        )
    else:
        console.print(f"[green]✓[/green] Default repo set to '[bold]{alias}[/bold]'.")


# ---------------------------------------------------------------------------
# workspace diagnostics
# ---------------------------------------------------------------------------


@workspace_group.command("diagnostics")
@click.argument("path", required=False, default=None)
@click.option("--repo", "repo_alias", default=None, help="Limit the report to one repo alias.")
@click.option("--json", "as_json", is_flag=True, help="Emit raw diagnostics JSON.")
def workspace_diagnostics(path: str | None, repo_alias: str | None, as_json: bool) -> None:
    """Explain the cross-repo contract link count.

    Reports, per repo, how many providers and consumers were found, which
    consumers went unmatched and why, and which providers have no consumer —
    the answer to "why are there so few links?". Reads the system graph built
    during 'repowise update --workspace'.
    """
    import json as _json

    from repowise.core.workspace.system_graph import load_system_graph

    start = resolve_repo_path(path)
    ws_root, _ws_config = _require_workspace(start)

    graph = load_system_graph(ws_root)
    if graph is None:
        raise click.ClickException(
            "No system graph found. Run 'repowise update --workspace' to build "
            "cross-repo contracts and diagnostics first."
        )

    diag = graph.diagnostics
    breakdown = diag.repo_breakdown
    unmatched = diag.unmatched_consumers
    orphans = diag.orphan_providers
    if repo_alias:
        breakdown = [r for r in breakdown if r.repo == repo_alias]
        unmatched = [u for u in unmatched if u.repo == repo_alias]
        orphans = [o for o in orphans if o.repo == repo_alias]

    if as_json:
        payload = {
            "total_providers": diag.total_providers,
            "total_consumers": diag.total_consumers,
            "total_links": diag.total_links,
            "weak_link_count": diag.weak_link_count,
            "repo_breakdown": [r.to_dict() for r in breakdown],
            "unmatched_consumers": [u.to_dict() for u in unmatched],
            "unmatched_by_reason": diag.unmatched_by_reason,
            "orphan_providers": [o.to_dict() for o in orphans],
        }
        console.print_json(_json.dumps(payload))
        return

    # Per-repo provider/consumer breakdown
    table = Table(title=f"Contract extraction — {ws_root.name}")
    table.add_column("Repo", style="cyan")
    table.add_column("Providers", justify="right")
    table.add_column("Consumers", justify="right")
    table.add_column("By type", style="dim")
    for r in breakdown:
        by_type = ", ".join(
            f"{t}:{r.providers_by_type.get(t, 0)}/{r.consumers_by_type.get(t, 0)}"
            for t in sorted(set(r.providers_by_type) | set(r.consumers_by_type))
        )
        table.add_row(r.repo, str(r.provider_count), str(r.consumer_count), by_type or "-")
    console.print(table)

    console.print(
        f"\n  [bold]{diag.total_links}[/bold] cross-repo link(s) matched "
        f"from {diag.total_providers} provider(s) and {diag.total_consumers} consumer(s)."
    )
    if diag.weak_link_count:
        console.print(f"  [yellow]{diag.weak_link_count}[/yellow] weak (low-confidence) link(s).")

    # Unmatched consumers grouped by reason
    if unmatched:
        reason_labels = {
            "no_provider": "no matching provider found",
            "internal_only": "provider is same repo + service (intra-service)",
            "unlinked": "matching provider exists but no link formed",
        }
        console.print(f"\n  [bold]{len(unmatched)}[/bold] unmatched consumer(s):")
        for reason, count in sorted(diag.unmatched_by_reason.items()):
            label = reason_labels.get(reason, reason)
            console.print(f"    [yellow]{count}[/yellow] — {label}")

    # Orphan providers
    if orphans:
        console.print(
            f"\n  [bold]{len(orphans)}[/bold] orphan provider(s) (declared, never consumed):"
        )
        for o in orphans[:20]:
            console.print(f"    [dim]{o.repo}[/dim] {o.contract_id} ([dim]{o.file_path}[/dim])")
        if len(orphans) > 20:
            console.print(f"    [dim]... and {len(orphans) - 20} more[/dim]")

    if not unmatched and not orphans:
        console.print(
            "\n  [green]✓[/green] Every consumer matched a provider; no orphan providers."
        )


# ---------------------------------------------------------------------------
# workspace check
# ---------------------------------------------------------------------------


@workspace_group.command("check")
@click.argument("path", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit the raw conformance report as JSON.")
def workspace_check(path: str | None, as_json: bool) -> None:
    """Architecture lint — fail on dependency-rule violations or cycles.

    Checks the declared ``conformance`` rules in ``.repowise-workspace.yaml``
    against the system graph and detects circular service dependencies. Exits
    non-zero when any violation or cycle is found, so it can gate CI. Reads (and
    recomputes from) the system graph built by 'repowise update --workspace', so
    editing rules and re-running picks them up without a full re-index.
    """
    import json as _json
    import sys

    from repowise.core.workspace.conformance import (
        build_conformance_report,
        tags_by_repo_from_config,
    )
    from repowise.core.workspace.system_graph import load_system_graph

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    graph = load_system_graph(ws_root)
    if graph is None:
        raise click.ClickException(
            "No system graph found. Run 'repowise update --workspace' to build "
            "cross-repo relationships first."
        )

    report = build_conformance_report(
        graph,
        ws_config.conformance.rules,
        tags_by_repo_from_config(ws_config),
    )

    if as_json:
        console.print_json(_json.dumps(report.to_dict()))
        if report.has_findings:
            sys.exit(1)
        return

    rule_count = report.rules_evaluated
    if rule_count == 0:
        console.print(
            "[dim]No conformance rules declared.[/dim] Add a [bold]conformance:[/bold] "
            "block to .repowise-workspace.yaml to enforce allowed dependencies."
        )

    # Rule violations
    if report.violations:
        console.print(f"\n[red]✗ {len(report.violations)} architecture rule violation(s):[/red]")
        for v in report.violations:
            rule = f"{v.rule_source} !-> {v.rule_target}"
            console.print(
                f"  [red]{v.source}[/red] -> [red]{v.target}[/red] "
                f"([dim]{v.edge_kind}[/dim]) violates [yellow]{rule}[/yellow]"
            )
            if v.rule_description:
                console.print(f"      [dim]{v.rule_description}[/dim]")

    # Dependency cycles
    if report.cycles:
        console.print(f"\n[red]✗ {len(report.cycles)} dependency cycle(s):[/red]")
        for c in report.cycles:
            loop = " -> ".join([*c.nodes, c.nodes[0]]) if c.nodes else ""
            console.print(f"  [red]{loop}[/red]")

    if not report.has_findings:
        if rule_count:
            console.print(
                f"\n[green]✓[/green] No violations of {rule_count} rule(s); no dependency cycles."
            )
        else:
            console.print("\n[green]✓[/green] No dependency cycles.")
        return

    console.print(
        f"\n[red]Architecture check failed:[/red] {len(report.violations)} violation(s), "
        f"{len(report.cycles)} cycle(s)."
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# workspace metrics
# ---------------------------------------------------------------------------


@workspace_group.command("metrics")
@click.argument("path", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="Emit the raw metrics as JSON.")
def workspace_metrics(path: str | None, as_json: bool) -> None:
    """Architecture metrics — propagation cost, core, and a 1-10 score.

    Computes the standard architecture-complexity metrics over the system graph
    built by 'repowise update --workspace': how coupled the whole system is
    (propagation cost), which services form the cyclic core, and a single
    deterministic 1-10 score. Uses structural edges only; co-change is excluded.
    Declared-rule violations, if any, are folded into the score. CI-friendly
    plain output.
    """
    import json as _json

    from repowise.core.workspace.architecture_metrics import compute_architecture_metrics
    from repowise.core.workspace.conformance import (
        check_conformance,
        tags_by_repo_from_config,
    )
    from repowise.core.workspace.system_graph import load_system_graph

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    graph = load_system_graph(ws_root)
    if graph is None:
        raise click.ClickException(
            "No system graph found. Run 'repowise update --workspace' to build "
            "cross-repo relationships first."
        )

    violations = check_conformance(
        graph, ws_config.conformance.rules, tags_by_repo_from_config(ws_config)
    )
    metrics = compute_architecture_metrics(
        graph,
        conformance_violations=len(violations),
        generated_at=graph.generated_at,
    )

    if as_json:
        console.print_json(_json.dumps(metrics.to_dict()))
        return

    if metrics.node_count == 0:
        console.print(
            "[dim]No services in the system graph yet.[/dim] Run "
            "'repowise update --workspace' after indexing repos with cross-repo "
            "relationships."
        )
        return

    score_color = "green" if metrics.score >= 8 else "yellow" if metrics.score >= 4 else "red"
    console.print(
        f"\n  Architecture score  [bold {score_color}]{metrics.score:.1f}[/bold {score_color}]"
        f" / 10   [dim]({metrics.architecture_type})[/dim]"
    )
    console.print(
        f"  Propagation cost    [bold]{metrics.propagation_cost_pct:.1f}%[/bold]"
        f"   [dim]avg share of other services each one can reach[/dim]"
    )
    if metrics.core_size:
        members = ", ".join(metrics.core_members[:6])
        if len(metrics.core_members) > 6:
            members += f", +{len(metrics.core_members) - 6} more"
        console.print(
            f"  Cyclic core         [bold]{metrics.core_size}[/bold] service(s)"
            f" ([dim]{metrics.core_ratio * 100:.0f}% of {metrics.node_count}[/dim]) — {members}"
        )
    else:
        console.print(
            f"  Cyclic core         [green]none[/green]"
            f"   [dim]({metrics.node_count} services, acyclic structure)[/dim]"
        )
    console.print(f"  Dependency cycles   [bold]{metrics.cycle_count}[/bold]")
    if metrics.conformance_violations:
        console.print(
            f"  Rule violations     [red]{metrics.conformance_violations}[/red]"
            f"   [dim](folded into the score)[/dim]"
        )

    breakdown = metrics.role_breakdown()
    role_labels = {
        "core": "Core",
        "shared": "Shared",
        "control": "Control",
        "peripheral": "Peripheral",
    }
    parts = ", ".join(f"{role_labels[r]} {breakdown.get(r, 0)}" for r in role_labels)
    console.print(f"\n  Service roles: {parts}")
