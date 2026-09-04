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
    resolve_max_file_pages,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
)
from repowise.cli.output import emit_json, format_option, json_option, resolve_format
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


def _format_age(generated_at: str | None) -> str:
    """Render an ISO timestamp as a short age, or say it is missing."""
    from datetime import datetime

    if not generated_at:
        return "never built"
    try:
        stamped = datetime.fromisoformat(generated_at)
    except ValueError:
        return f"stamped {generated_at}"
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - stamped).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    if seconds < 172800:
        return f"{round(seconds / 3600)} hours ago"
    return f"{round(seconds / 86400)} days ago"


def _report_artifact_provenance(
    ws_root: Path,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
    generated_at: str | None,
    artifact_repos: list[str],
) -> None:
    """Print how old the artifact is, and flag config/artifact disagreement.

    Commands that read a persisted artifact otherwise present it as the state
    of the tree right now. When the config and the artifact list different
    repos, that gap is itself the finding — it means the artifact describes a
    workspace that no longer exists, or that the config lost a repo.
    """
    console.print(f"[dim]Read from .repowise-workspace/, built {_format_age(generated_at)}.[/dim]")

    configured = {e.alias for e in ws_config.repos}
    covered = set(artifact_repos)
    missing_from_config = sorted(covered - configured)
    missing_from_artifact = sorted(configured - covered)
    if missing_from_config:
        console.print(
            f"[yellow]![/yellow] Covers {', '.join(missing_from_config)}, which "
            f"{'is' if len(missing_from_config) == 1 else 'are'} no longer in "
            f"{ws_root.name}/.repowise-workspace.yaml."
        )
    if missing_from_artifact:
        console.print(
            f"[yellow]![/yellow] {', '.join(missing_from_artifact)} "
            f"{'is' if len(missing_from_artifact) == 1 else 'are'} configured but "
            "absent from this artifact; re-run 'repowise update --workspace'."
        )


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
    "--save-key/--no-save-key",
    "save_key",
    default=True,
    help=(
        "Save the provider API key this run authenticated with into the added "
        "repo's .repowise/.env (gitignored). Default: on, because each workspace repo "
        "has its own .repowise/, so each needs its own key to be answerable by "
        "the MCP server. Same switch as `repowise init --no-save-key`."
    ),
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
    save_key: bool,
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
        save_key=save_key,
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
    the ``allow`` posture) the moment ``.repowise/`` exists — even after a
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
    save_key: bool = True,
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
        from repowise.cli.ui import load_dotenv

        primary_path = (ws_root / primary.path).resolve()
        primary_cfg = _load_cfg(primary_path)
        # The provider settings are inherited from the primary repo, so the
        # credential that goes with them lives in the primary's ``.env`` —
        # the new repo has none yet. Same call `init`'s workspace flow makes.
        load_dotenv(primary_path)

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
    # `workspace add` freshly full-indexes the repo, so this from-scratch state
    # is a current-format store, not one predating the concept tree. Stamp the
    # terminal version rather than clamping to v1 and nagging a repo this run
    # just built to re-index itself.
    save_state(repo_path, state, full_index=True)

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
            save_key=save_key,
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
        # Whole-repo selection, so honour the per-repo file-page cap.
        max_file_pages=resolve_max_file_pages(config=repo_cfg),
    )
    assembler = ContextAssembler(config, repo_path=repo_path)
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
            await fts.index(
                p.page_id,
                p.title,
                p.content,
                summary=p.summary,
                target_path=p.target_path,
            )
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
    "--exclude",
    "exclude_globs",
    multiple=True,
    metavar="GLOB",
    help=(
        "Skip discovered repos whose path matches this glob, relative to the "
        "workspace root (e.g. 'test-repos/*'). Repeatable."
    ),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
def workspace_scan(
    path: str | None, yes: bool, exclude_globs: tuple[str, ...], verbose: bool
) -> None:
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

    if exclude_globs:
        from fnmatch import fnmatchcase

        def _excluded(repo_path: Path) -> bool:
            try:
                rel = repo_path.relative_to(ws_root).as_posix()
            except ValueError:
                rel = repo_path.as_posix()
            return any(fnmatchcase(rel, g) for g in exclude_globs)

        before = len(new_repos)
        new_repos = [r for r in new_repos if not _excluded(r.path)]
        skipped = before - len(new_repos)
        if skipped:
            console.print(f"[dim]Excluded {skipped} repo(s) by --exclude.[/dim]")

    if not new_repos:
        console.print("[green]No new repositories discovered.[/green]")
        return

    console.print(f"\nFound [bold]{len(new_repos)}[/bold] new repo(s) not in workspace:\n")
    for repo in new_repos:
        indexed_marker = " [green](indexed)[/green]" if repo.has_repowise else ""
        console.print(f"  [cyan]{repo.alias}[/cyan] — {repo.path}{indexed_marker}")

    console.print()

    # A scan of a directory full of fixtures can find a hundred repos, and
    # prompting per repo with no way out but Ctrl-C is not a choice. Ask once
    # before starting, so declining everything costs one keystroke.
    if not yes and not click.confirm(
        f"Review these {len(new_repos)} repo(s) one at a time?", default=True
    ):
        console.print(
            "\nNo repos added. Narrow the scan with [bold]--exclude 'dir/*'[/bold] "
            "or add all with [bold]--yes[/bold]."
        )
        return

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
@format_option(help="Output format. ``json`` emits the raw diagnostics.")
@json_option()
def workspace_diagnostics(
    path: str | None, repo_alias: str | None, fmt: str, as_json: bool
) -> None:
    """Explain the cross-repo contract link count.

    Reports, per repo, how many providers and consumers were found, which
    consumers went unmatched and why, and which providers have no consumer —
    the answer to "why are there so few links?". Reads the system graph built
    during 'repowise update --workspace'.
    """
    from repowise.core.workspace.system_graph import load_system_graph

    fmt = resolve_format(fmt, as_json)
    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

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

    if fmt == "json":
        emit_json(
            {
                "generated_at": graph.generated_at or None,
                "total_providers": diag.total_providers,
                "total_consumers": diag.total_consumers,
                "total_links": diag.total_links,
                "weak_link_count": diag.weak_link_count,
                "repo_breakdown": [r.to_dict() for r in breakdown],
                "unmatched_consumers": [u.to_dict() for u in unmatched],
                "unmatched_by_reason": diag.unmatched_by_reason,
                "orphan_providers": [o.to_dict() for o in orphans],
                "providers_by_layer": diag.providers_by_layer,
                "consumers_by_layer": diag.consumers_by_layer,
                "http_consumers_unresolved": diag.http_consumers_unresolved,
                "http_consumer_coverage": diag.http_consumer_coverage,
            }
        )
        return

    _report_artifact_provenance(
        ws_root, ws_config, graph.generated_at, [r.repo for r in diag.repo_breakdown]
    )

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

    # Extraction coverage. Only two honest numbers exist here: which tier
    # produced each contract, and how many client calls were located but not
    # resolved. There is no count of route decorators nobody recognised, so no
    # provider recall percentage is claimed.
    def _layers(counts: dict[str, int]) -> str:
        return ", ".join(f"{n} {layer}" for layer, n in sorted(counts.items()))

    if diag.providers_by_layer or diag.consumers_by_layer:
        console.print("\n  [bold]Extraction coverage[/bold]")
        if diag.providers_by_layer:
            layers = _layers(diag.providers_by_layer)
            console.print(f"    Providers   {diag.total_providers} ({layers})")
        if diag.consumers_by_layer:
            layers = _layers(diag.consumers_by_layer)
            console.print(f"    Consumers   {diag.total_consumers} ({layers})")
        coverage = diag.http_consumer_coverage
        if coverage is not None:
            http_consumers = sum(r.consumers_by_type.get("http", 0) for r in diag.repo_breakdown)
            console.print(
                f"    HTTP calls  {http_consumers} of "
                f"{http_consumers + diag.http_consumers_unresolved} resolved to an "
                f"endpoint ([bold]{coverage * 100:.0f}%[/bold]); "
                f"{diag.http_consumers_unresolved} located but not statically resolvable."
            )
        console.print(
            "    [dim]'index' contracts come from the parsed symbol table, 'regex' "
            "from a text dialect. Calls no dialect recognises are not counted here.[/dim]"
        )

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
@click.option(
    "--breaking/--no-breaking",
    default=True,
    help="Fail on breaking contract changes from the last workspace update.",
)
@format_option(help="Output format. ``json`` emits the raw conformance report.")
@json_option()
def workspace_check(path: str | None, breaking: bool, fmt: str, as_json: bool) -> None:
    """Architecture lint — fail on rule violations, cycles, or broken contracts.

    Checks the declared ``conformance`` rules in ``.repowise-workspace.yaml``
    against the system graph and detects circular service dependencies. Exits
    non-zero when any violation or cycle is found, so it can gate CI. Reads (and
    recomputes from) the system graph built by 'repowise update --workspace', so
    editing rules and re-running picks them up without a full re-index.

    Also fails on the breaking contract changes the last workspace update
    detected — a removed endpoint or a retyped field a consumer in another repo
    still calls. Unlike the rules above these need nothing declared, so
    ``--no-breaking`` is there for a pipeline that wants only its own rules
    enforced. They are read from that update's report, not recomputed here.
    """
    import sys

    from rich.markup import escape

    from repowise.core.workspace.breaking_change import (
        SEVERITY_BREAKING,
        load_breaking_change_report,
    )
    from repowise.core.workspace.conformance import (
        build_conformance_report,
        tags_by_repo_from_config,
    )
    from repowise.core.workspace.system_graph import load_system_graph

    fmt = resolve_format(fmt, as_json)
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

    # Gate on wire-incompatible changes that endanger another repo. A warning
    # severity is source-compat only and must not fail a build.
    bc_report = load_breaking_change_report(ws_root) if breaking else None
    bc_ran = bc_report is not None and bc_report.ran
    breaking_changes = (
        [
            c
            for c in bc_report.changes
            if c.severity == SEVERITY_BREAKING
            and any(ic.repo != c.provider_repo for ic in c.impacted_consumers)
        ]
        if bc_ran
        else []
    )

    if fmt == "json":
        payload = report.to_dict()
        if breaking:
            payload["breaking_changes"] = [c.to_dict() for c in breaking_changes]
            # The conformance half is recomputed now; this half is an artifact
            # of arbitrary age, so it carries its own stamp.
            payload["breaking_changes_generated_at"] = (
                bc_report.generated_at if bc_report is not None else None
            )
            # False = never detected, so the empty list above is not a pass.
            payload["breaking_changes_available"] = bc_ran
        emit_json(payload)
        if report.has_findings or breaking_changes:
            sys.exit(1)
        return

    # State the scope before the verdict. "No cycles" over an empty graph and
    # "no cycles" over 7 services across 3 repos are the same sentence and very
    # different results, and only one of them is a pass.
    _report_artifact_provenance(
        ws_root, ws_config, graph.generated_at, sorted({n.repo for n in graph.nodes})
    )
    console.print(
        f"Checked [bold]{len(graph.nodes)}[/bold] service(s) across "
        f"[bold]{len({n.repo for n in graph.nodes})}[/bold] repo(s) against "
        f"[bold]{report.rules_evaluated}[/bold] declared rule(s)."
    )

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

    # Breaking contract changes
    if breaking_changes:
        stamp = bc_report.generated_at if bc_report is not None else None
        console.print(
            f"\n[red]✗ {len(breaking_changes)} breaking contract change(s)[/red]"
            + (f" [dim](detected {stamp})[/dim]" if stamp else "")
            + "[red]:[/red]"
        )
        for c in breaking_changes:
            cross = [ic for ic in c.impacted_consumers if ic.repo != c.provider_repo]
            repos = sorted({ic.repo for ic in cross})
            console.print(
                f"  [red]{escape(c.contract_id)}[/red] ([dim]{escape(c.contract_type)}[/dim]) "
                f"{escape(c.kind)}: {escape(c.detail)}"
            )
            console.print(
                f"      [dim]{escape(c.provider_repo)}/{escape(c.provider_file)} -> "
                f"{len(cross)} consumer(s) in {escape(', '.join(repos))}[/dim]"
            )

    if not report.has_findings and not breaking_changes:
        if rule_count:
            console.print(
                f"\n[green]✓[/green] No violations of {rule_count} rule(s); no dependency cycles."
            )
        else:
            console.print("\n[green]✓[/green] No dependency cycles.")
        if bc_ran:
            console.print("[green]✓[/green] No breaking contract changes.")
        return

    # Only claim a breaking-change count when a detection pass produced one.
    tail = f", {len(breaking_changes)} breaking contract change(s)" if bc_ran else ""
    console.print(
        f"\n[red]Architecture check failed:[/red] {len(report.violations)} violation(s), "
        f"{len(report.cycles)} cycle(s){tail}."
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# workspace metrics
# ---------------------------------------------------------------------------


@workspace_group.command("metrics")
@click.argument("path", required=False, default=None)
@format_option(help="Output format. ``json`` emits the raw metrics.")
@json_option()
def workspace_metrics(path: str | None, fmt: str, as_json: bool) -> None:
    """Architecture metrics — propagation cost, core, and a 1-10 score.

    Computes the standard architecture-complexity metrics over the system graph
    built by 'repowise update --workspace': how coupled the whole system is
    (propagation cost), which services form the cyclic core, and a single
    deterministic 1-10 score. Uses structural edges only; co-change is excluded.
    Declared-rule violations, if any, are folded into the score. CI-friendly
    plain output.
    """
    from repowise.core.workspace.architecture_metrics import compute_architecture_metrics
    from repowise.core.workspace.conformance import (
        check_conformance,
        tags_by_repo_from_config,
    )
    from repowise.core.workspace.system_graph import load_system_graph

    fmt = resolve_format(fmt, as_json)
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

    if fmt == "json":
        emit_json(metrics.to_dict())
        return

    if metrics.node_count == 0:
        console.print(
            "[dim]No services in the system graph yet.[/dim] Run "
            "'repowise update --workspace' after indexing repos with cross-repo "
            "relationships."
        )
        return

    _report_artifact_provenance(
        ws_root, ws_config, graph.generated_at, sorted({n.repo for n in graph.nodes})
    )

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


# ---------------------------------------------------------------------------
# workspace impacted-tests
# ---------------------------------------------------------------------------


_UNRESOLVED_REASONS = {
    "no_index": "consumer has no index",
    "unbound": "contract never bound to a symbol",
    "symbol_missing": "bound symbol is not in the index",
}

_EMPTY_REASONS = {
    "no_contract_store": "there is no contract map yet; run 'repowise update --workspace'",
    "no_matching_links": "no contract link connects the changed files to a consumer",
    "no_changed_files": "no changed files were given",
}


def _unresolved_reason_text(reason: str, detail: str | None) -> str:
    """Plain words for why a contract link could not be followed."""
    if reason == "lookup_failed":
        return f"lookup failed ({detail or 'unknown'})"
    return _UNRESOLVED_REASONS.get(reason, reason)


def _empty_explanation(result: object) -> str:
    """Say which state produced an empty answer, never just that it is empty."""
    summary = getattr(result, "summary", {}) or {}
    reason = summary.get("reason")
    if reason:
        return f"No tests found: {_EMPTY_REASONS.get(reason, reason)}."
    parts = []
    reached_nothing = (summary.get("states") or {}).get("none", 0)
    if reached_nothing:
        parts.append(f"{reached_nothing} consumer file(s) the requested passes found nothing for")
    unresolved = len(getattr(result, "unresolved", []) or [])
    if unresolved:
        parts.append(f"{unresolved} link(s) could not be determined")
    passes = summary.get("passes") or {}
    disabled = [name for name in ("measured", "inferred") if passes.get(name) is False]
    suffix = "".join(f" ({name} pass disabled)" for name in disabled)
    if not parts:
        return f"No tests found: no consumer call site was analysed.{suffix}"
    return f"No tests found: {', '.join(parts)}.{suffix}"


@workspace_group.command("impacted-tests")
@click.argument("changed_files", required=True, nargs=-1)
@click.option(
    "--path",
    "workspace_path",
    default=None,
    help="Path to workspace root (default: auto-detect from cwd).",
)
@click.option(
    "--call-depth",
    default=3,
    type=click.IntRange(1, 8),
    help="Call graph walk depth (default: 3).",
)
@click.option(
    "--import-depth",
    default=1,
    type=click.IntRange(1, 3),
    help="Import graph fallback depth (default: 1).",
)
@click.option(
    "--no-measured",
    is_flag=True,
    help="Exclude measured coverage-backed recommendations.",
)
@click.option(
    "--no-inferred",
    is_flag=True,
    help="Exclude graph-inferred recommendations.",
)
@click.option(
    "--min-confidence",
    default=0.0,
    type=click.FloatRange(0.0, 1.0),
    help="Minimum contract link confidence to consider (0.0-1.0).",
)
@click.option(
    "--target-repo",
    "target_repos",
    multiple=True,
    help="Limit analysis to these consumer repo aliases. Repeatable.",
)
@format_option(choices=("table", "json", "list"))
@json_option()
def workspace_impacted_tests(
    changed_files: tuple[str, ...],
    workspace_path: str | None,
    call_depth: int,
    import_depth: int,
    no_measured: bool,
    no_inferred: bool,
    min_confidence: float,
    target_repos: tuple[str, ...],
    fmt: str,
    as_json: bool,
) -> None:
    """Cross-repository test impact: which downstream tests to run.

    Given a list of changed files in provider repositories (format: repo:path),
    returns the test files in consumer repositories that cover those changes,
    via measured per-test coverage, call-graph reachability, and import-graph
    fallback.

    Example:
        repowise workspace impacted-tests backend-api:src/api/users.py

    Output format:
        - table (default): human-readable grouped by consumer repo
        - json: full machine-readable result
        - list: one repo:path line per test file
    """
    configure_cli_logging()
    fmt = resolve_format(fmt, as_json)

    if not changed_files:
        raise click.ClickException(
            "At least one changed file required. Format: repo_alias:path/to/file.py"
        )

    # Parse changed_files
    parsed_changed: list[dict[str, str]] = []
    for cf in changed_files:
        if ":" not in cf:
            raise click.ClickException(
                f"Invalid format: {cf}. Use repo_alias:path/to/file.py"
            )
        repo, path = cf.split(":", 1)
        parsed_changed.append({"repo": repo, "path": path})

    start = resolve_repo_path(workspace_path)
    ws_root, _ = _require_workspace(start)

    from repowise.core.workspace.test_impact import (
        MAX_TESTS_PER_TARGET,
        workspace_test_impact_from_root,
        workspace_test_impact_to_dict,
    )

    result = run_async(
        workspace_test_impact_from_root(
            ws_root,
            parsed_changed,
            call_depth=call_depth,
            import_depth=import_depth,
            include_measured=not no_measured,
            include_inferred=not no_inferred,
            min_confidence=min_confidence,
            target_repos=list(target_repos) if target_repos else None,
        )
    )

    if fmt == "json":
        emit_json(workspace_test_impact_to_dict(result))
        return

    if fmt == "list":
        # Output test file paths only, one per line
        seen: set[str] = set()
        for rec in result.recommendations:
            key = f"{rec.consumer_repo}:{rec.test_file}"
            if key not in seen:
                seen.add(key)
                click.echo(key)
        if not seen:
            # A pipeline reading stdout must never get silence with exit 0.
            click.echo(_empty_explanation(result), err=True)
        if result.unresolved:
            click.echo(
                f"{len(result.unresolved)} contract link(s) could not be determined; "
                "run with --format table to see why.",
                err=True,
            )
        return

    # Group by consumer repo
    by_consumer: dict[str, list] = {}
    for rec in result.recommendations:
        by_consumer.setdefault(rec.consumer_repo, []).append(rec)

    console.print(f"[bold]Workspace test impact[/bold] for {len(parsed_changed)} changed file(s):")
    for item in parsed_changed:
        console.print(f"  [dim]{item['repo']}:{item['path']}[/dim]")

    for consumer_repo, recs in sorted(by_consumer.items()):
        table = Table(title=f"Consumer: {consumer_repo}")
        table.add_column("Test File", style="green")
        table.add_column("Basis", style="yellow")
        table.add_column("Via", style="dim")
        table.add_column("Provider Repo", style="cyan")
        table.add_column("Provider Files", style="dim")
        table.add_column("Contract", style="dim")
        table.add_column("Confidence", justify="right")

        for rec in recs:
            table.add_row(
                rec.test_file,
                rec.basis,
                rec.via,
                rec.provider_repo,
                ", ".join(rec.source_files),
                ", ".join(rec.contract_ids),
                f"{rec.confidence:.2f}",
            )
        console.print(table)

    if result.unresolved:
        unresolved_table = Table(title="Could not determine")
        unresolved_table.add_column("Consumer", style="cyan")
        unresolved_table.add_column("File", style="dim")
        unresolved_table.add_column("Contract", style="dim")
        unresolved_table.add_column("Reason", style="yellow")
        for link in result.unresolved:
            unresolved_table.add_row(
                link.consumer_repo,
                link.consumer_file,
                link.contract_id,
                _unresolved_reason_text(link.reason, link.detail),
            )
        console.print(unresolved_table)

    if result.recommendations_emitted == 0:
        console.print(f"[yellow]{_empty_explanation(result)}[/yellow]")
        return

    by_basis = result.recommendations_by_basis
    console.print(
        f"\n[bold]{result.recommendations_emitted}[/bold] test recommendation(s) across "
        f"[bold]{len(by_consumer)}[/bold] consumer repo(s):"
    )
    if by_basis.get("measured", 0):
        console.print(f"  [green]{by_basis['measured']}[/green] from measured coverage")
    if by_basis.get("inferred", 0):
        console.print(f"  [yellow]{by_basis['inferred']}[/yellow] inferred from call/import graph")
    if result.recommendations_truncated:
        console.print(
            f"  [dim]{result.recommendations_omitted} more omitted "
            f"(cap {MAX_TESTS_PER_TARGET} per consumer and provider pair); "
            f"use --format json for counts.[/dim]"
        )
