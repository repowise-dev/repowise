"""``repowise workspace`` — manage multi-repo workspaces."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    find_workspace_root,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
)

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
@click.option("--provider", "provider_name", default=None, help="LLM provider name (overrides primary's).")
@click.option("--model", default=None, help="Model identifier (overrides primary's).")
@click.option("--concurrency", type=int, default=10, help="Max concurrent LLM calls during doc generation.")
def workspace_add(
    path: str,
    alias: str | None,
    run_index: bool,
    run_docs: bool | None,
    provider_name: str | None,
    model: str | None,
    concurrency: int,
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
    from repowise.core.workspace.config import RepoEntry

    repo_path = Path(path).resolve()
    ws_root, ws_config = _require_workspace(Path.cwd())

    # Validate path exists
    if not repo_path.exists():
        raise click.ClickException(f"Path does not exist: {repo_path}")

    # Validate it is a git repo
    if not (repo_path / ".git").exists():
        raise click.ClickException(
            f"Not a git repository (no .git found): {repo_path}"
        )

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
    from repowise.core.pipeline.orchestrator import run_pipeline
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
                effective_provider, effective_model, repo_path=repo_path,
            )
            console.print(
                f"  Provider: [cyan]{provider.provider_name}[/cyan] / "
                f"Model: [cyan]{provider.model_name}[/cyan]"
            )
        except Exception as exc:
            console.print(
                f"  [yellow]Provider unavailable ({exc}); skipping docs.[/yellow]"
            )
            generate_docs = False
            docs_skip_reason = f"provider failure: {exc}"

    ensure_repowise_dir(repo_path)
    _inherit_distill_verdict(repo_path, primary_cfg)

    async def _do_index() -> tuple[int, int, int]:
        result = await run_pipeline(
            repo_path,
            commit_depth=int(commit_limit) if commit_limit else 500,
            exclude_patterns=exclude_patterns or None,
            generate_docs=False,
        )

        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )
        from repowise.core.persistence.database import resolve_db_url
        from repowise.core.pipeline import persist_pipeline_result

        url = resolve_db_url(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)
        page_count = 0
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session, name=result.repo_name, local_path=str(repo_path)
            )
            await persist_pipeline_result(result, session, repo.id)

        await engine.dispose()

        # Save the curated KG artifact so doc generation can load curated
        # module grouping (matches the `repowise init` idiom in
        # init_cmd/command.py). Without it, generation falls back to raw
        # community grouping and emits wrong module pages.
        from repowise.cli.state_persistence import save_knowledge_graph_json

        kg = getattr(result, "knowledge_graph_result", None)
        if kg is not None:
            save_knowledge_graph_json(repo_path, kg)

        return result.file_count, result.symbol_count, page_count

    try:
        file_count, symbol_count, _ = run_async(_do_index())
        console.print(
            f"  [green]✓[/green] {file_count} files, {symbol_count:,} symbols"
        )
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
        "docs_enabled": bool(generate_docs and provider is not None),
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
    if not state["docs_enabled"]:
        reason = docs_skip_reason or "docs disabled"
        console.print(
            f"\n[yellow]Note:[/yellow] '{alias}' indexed without docs ({reason})."
        )
        console.print(
            f"  Run [bold]repowise update --repo {alias} --docs[/bold] "
            "to generate documentation."
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
        upsert_page_from_generated,
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
    graph_builder.build()

    from repowise.core.repo_config import load_repo_config

    config = GenerationConfig(
        max_concurrency=concurrency,
        reasoning=reasoning,
        wiki_style=load_repo_config(repo_path).get("wiki_style", "comprehensive"),
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
                session, name=repo_path.name, local_path=str(repo_path),
            )
            for p in pages:
                await upsert_page_from_generated(session, p, repo.id)
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
        raise click.ClickException(
            f"No repo with alias '{alias}' found. Available: {available}"
        )

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
        console.print(
            "[yellow]Note:[/yellow] Workspace now has no repos and no default."
        )

    console.print(
        f"  (Indexed data at {removed.path}/.repowise/ was [bold]not[/bold] deleted.)"
    )


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
def workspace_scan(path: str | None, yes: bool) -> None:
    """Scan the workspace root for new repos not yet in the config."""
    from repowise.core.workspace.config import RepoEntry
    from repowise.core.workspace.scanner import scan_for_repos

    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    console.print(f"Scanning [cyan]{ws_root}[/cyan] for git repositories…")
    scan_result = scan_for_repos(ws_root)

    existing_aliases = set(ws_config.repo_aliases())
    existing_paths = {
        (ws_root / e.path).resolve().as_posix()
        for e in ws_config.repos
    }

    new_repos = [
        r for r in scan_result.repos
        if r.path.as_posix() not in existing_paths
        and r.alias not in existing_aliases
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
        raise click.ClickException(
            f"No repo with alias '{alias}' found. Available: {available}"
        )

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
