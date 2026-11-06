"""Index (and optionally document) a repo just added to the workspace."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.cli.helpers import (
    console,
    find_workspace_root,
    resolve_max_file_pages,
    resolve_reasoning,
    run_async,
    warn,
)
from repowise.core.docs_mode import DocsMode, resolve_docs_mode
from repowise.core.index_scope import fast_to_full_command

if TYPE_CHECKING:
    from repowise.core.workspace.config import WorkspaceConfig


def _resolve_docs_flag(
    *, run_docs: bool | None, provider_name: str | None
) -> tuple[DocsMode, str | None]:
    """The docs mode ``workspace add`` runs, and why there are none when off.

    ``--no-docs`` indexes only. ``--docs`` or ``--provider`` writes the
    documentation with a model. Otherwise it is built from code: a configured
    provider or a key in the environment is never a reason to spend unasked.
    """
    if run_docs is False:
        return "none", "--no-docs flag"
    if run_docs or provider_name is not None:
        return "llm", None
    return "deterministic", None


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
    docs_mode: DocsMode = "none",
    provider_name: str | None = None,
    model: str | None = None,
    concurrency: int = 10,
    docs_skip_reason: str | None = None,
    save_key: bool = True,
) -> None:
    """Run the ingestion pipeline on a single repo, then build its documentation.

    *docs_mode* ``llm`` writes it with a model, ``deterministic`` builds it
    from code, ``none`` skips it.

    Updates the workspace config entry, persists results to the per-repo
    DB, writes ``.repowise/state.json`` (so ``repowise update`` knows the
    base commit), saves provider/model into ``config.yaml`` when docs ran,
    and re-runs cross-repo hooks so contracts/co-changes are fresh.
    """
    from repowise.cli.commands.init_cmd.workspace import _RepoDocs
    from repowise.cli.helpers import ensure_repowise_dir

    console.print(f"  Indexing [cyan]{alias}[/cyan]…")

    # Reuse the primary repo's provider/embedder/exclude settings when the
    # caller hasn't overridden them.
    primary_cfg = _load_primary_config(ws_root, ws_config)
    # ``provider`` is None exactly when no model runs.
    provider = None
    if docs_mode == "llm":
        provider = _resolve_docs_provider(repo_path, primary_cfg, provider_name, model)
        docs_mode = "llm" if provider is not None else "deterministic"

    ensure_repowise_dir(repo_path)
    _inherit_distill_verdict(repo_path, primary_cfg)

    ctx = _added_repo_ctx(provider, ws_config, primary_cfg, concurrency=concurrency, save_key=save_key)
    index = _index_added_repo(repo_path, alias, ctx)
    if index is None:
        return
    docs = _RepoDocs(docs_mode=docs_mode, skip_reason=docs_skip_reason)
    _document_added_repo(repo_path, ctx, index, docs)
    _save_added_repo_config(repo_path, ctx, primary_cfg)
    head, state = _save_added_repo_state(repo_path, ctx, index, docs)
    _record_in_workspace(ws_root, ws_config, alias, head, repo_path)
    _note_missing_docs(alias, state, docs.skip_reason)


def _document_added_repo(repo_path: Path, ctx: Any, index: Any, docs: Any) -> None:
    """Build the documentation *docs* asks for into the index result; never raises."""
    pages: list[Any] = []
    if docs.docs_mode != "none":
        pages, error = _build_added_repo_docs(repo_path, ctx)
        if error is not None:
            docs.skip_reason = error
            # A model run keeps its provider state so `update --docs` retries it.
            if ctx.provider is None:
                docs.docs_mode = "none"
    index.result.generated_pages = pages
    docs.pages_generated = len(pages)


def _build_added_repo_docs(repo_path: Path, ctx: Any) -> tuple[list[Any], str | None]:
    """Generate the added repo's documentation as ``(pages, error)``; never raises."""
    provider = ctx.provider
    try:
        pages = _generate_docs_for_added_repo(
            repo_path=repo_path,
            provider=provider,
            concurrency=ctx.concurrency,
            reasoning=ctx.resolved_reasoning,
            exclude_patterns=ctx.exclude_patterns,
        )
    except Exception as exc:
        console.print(f"  [yellow]Documentation failed: {exc}[/yellow]")
        return [], f"generation error: {exc}"
    source = (
        "built from code"
        if provider is None
        else f"written by {getattr(provider, 'model_name', 'the model')}"
    )
    console.print(f"  [green]✓[/green] Documentation: {len(pages)} pages, {source}")
    return pages, None


def _save_added_repo_config(repo_path: Path, ctx: Any, primary_cfg: dict) -> None:
    """Save the model settings into config.yaml when a model ran, so `update` need not ask."""
    if ctx.provider is None:
        return
    from repowise.cli.helpers import save_config

    commit_limit = primary_cfg.get("commit_limit")
    save_config(
        repo_path,
        ctx.provider.provider_name,
        ctx.provider.model_name,
        primary_cfg.get("embedder", "mock"),
        exclude_patterns=ctx.exclude_patterns or None,
        commit_limit=int(commit_limit) if commit_limit else None,
        reasoning=ctx.resolved_reasoning,
        save_key=ctx.save_key,
    )


def _note_missing_docs(alias: str, state: dict, skip_reason: str | None) -> None:
    """Honest completion notice, with the exact command, when no docs were built."""
    if resolve_docs_mode(state) != "none":
        return
    reason = skip_reason or "docs disabled"
    console.print(f"\n[yellow]Note:[/yellow] '{alias}' indexed without docs ({reason}).")
    console.print(f"  Run [bold]{fast_to_full_command(alias)}[/bold] to build its documentation.")


def _load_primary_config(
    ws_root: Path,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
) -> dict:
    """The primary repo's ``config.yaml``, with its ``.env`` loaded, or ``{}``."""
    primary = ws_config.get_primary()
    if primary is None:
        return {}
    from repowise.cli.helpers import load_config as _load_cfg
    from repowise.cli.ui import load_dotenv

    primary_path = (ws_root / primary.path).resolve()
    primary_cfg = _load_cfg(primary_path)
    # The provider settings are inherited from the primary repo, so the
    # credential that goes with them lives in the primary's ``.env`` —
    # the new repo has none yet. Same call `init`'s workspace flow makes.
    load_dotenv(primary_path)
    return primary_cfg


def _resolve_docs_provider(
    repo_path: Path, primary_cfg: dict, provider_name: str | None, model: str | None
) -> object | None:
    """The model provider, or None (with a notice) when it does not resolve.

    An unset *provider_name* or *model* falls back to the primary repo's. On
    None the caller builds the documentation from code rather than leave the
    repo with none.
    """
    from repowise.cli.helpers import resolve_provider

    try:
        provider = resolve_provider(
            provider_name or primary_cfg.get("provider"),
            model or primary_cfg.get("model"),
            repo_path=repo_path,
        )
    except Exception as exc:
        console.print(
            f"  [yellow]Provider unavailable ({exc}); building documentation from code.[/yellow]"
        )
        return None
    console.print(
        f"  Provider: [cyan]{provider.provider_name}[/cyan] / "
        f"Model: [cyan]{provider.model_name}[/cyan]"
    )
    return provider


def _index_added_repo(repo_path: Path, alias: str, ctx: Any) -> Any:
    """Full-index the repo into init's per-repo ``_RepoIndex``; None (warned) on failure.

    Shared full-index step (run pipeline, persist, export the curated KG
    artifact so doc generation can load curated module grouping) — the same
    helper the workspace updater's first-time indexing uses. No model mines
    decisions here, so the index carries no provider.
    """
    from repowise.cli.commands.init_cmd.workspace import _RepoIndex
    from repowise.core.pipeline import PhaseTimingRecorder
    from repowise.core.pipeline.full_index import index_repo_full

    recorder = PhaseTimingRecorder()
    try:
        result = run_async(
            index_repo_full(
                repo_path,
                commit_depth=ctx.resolved_commit_limit,
                exclude_patterns=ctx.exclude_patterns,
                progress=recorder,
            )
        )
    except Exception as exc:
        warn(f"Indexing failed for '{alias}': {exc}")
        return None
    console.print(f"  [green]✓[/green] {result.file_count} files, {result.symbol_count:,} symbols")
    return _RepoIndex(result=result, callback=recorder, provider=None)


def _added_repo_ctx(
    provider: object | None,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
    primary_cfg: dict,
    *,
    concurrency: int,
    save_key: bool,
) -> Any:
    """Workspace init's run settings for this one repo, so its state is init's.

    Exclude patterns, reasoning and the commit limit come from the primary repo.
    """
    from repowise.cli.commands.init_cmd.workspace import _WorkspaceCtx
    from repowise.core.generation.styles import DEFAULT_STYLE
    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT

    commit_limit = primary_cfg.get("commit_limit")
    return _WorkspaceCtx(
        provider=provider,
        ws_config=ws_config,
        editor_options=None,
        index_only=provider is None,
        dry_run=False,
        force=False,
        follow_renames=False,
        include_submodules=False,
        exclude_patterns=list(primary_cfg.get("exclude_patterns") or []),
        skip_tests=False,
        skip_infra=False,
        concurrency=concurrency,
        test_run=False,
        yes=False,
        resume=False,
        onboarding=True,
        wiki_style=DEFAULT_STYLE,
        language="en",
        resolved_reasoning=resolve_reasoning(config=primary_cfg),
        # Pages go to full-text search only; none are embedded.
        embedder_name_resolved=None,  # type: ignore[arg-type]
        embedder_was_requested=False,
        resolved_commit_limit=int(commit_limit) if commit_limit else _DEFAULT_COMMIT_LIMIT,
        save_key=save_key,
    )


def _save_added_repo_state(
    repo_path: Path, ctx: Any, index: Any, docs: Any
) -> tuple[str | None, dict]:
    """Write the state workspace init writes for a repo; returns ``(head, state)``.

    Called after the add's config writes, which the config fingerprint reads.
    """
    from repowise.cli.commands.init_cmd.persistence import stamp_fresh_index
    from repowise.cli.commands.init_cmd.workspace import _repo_state
    from repowise.cli.helpers import get_head_commit, save_state

    head = get_head_commit(repo_path)
    state = _repo_state(head, repo_path, ctx, index, docs, [])
    stamp_fresh_index(state, repo_path, index.result)
    # Freshly full-indexed, so stamp the terminal store format.
    save_state(repo_path, state, full_index=True)
    return head, state


def _record_in_workspace(
    ws_root: Path,
    ws_config: WorkspaceConfig,  # type: ignore[name-defined]
    alias: str,
    head: str | None,
    repo_path: Path,
) -> None:
    """Stamp the workspace entry and refresh cross-repo hooks (best effort)."""
    from repowise.core.workspace.update import run_cross_repo_hooks, stamp_workspace_entry

    entry = ws_config.get_repo(alias)
    if entry is not None:
        stamp_workspace_entry(entry, repo_path, head)
    ws_config.save(ws_root)

    # Cross-repo hooks — best effort; never fail the add command.
    try:
        run_async(run_cross_repo_hooks(ws_config, ws_root, [alias]))
    except Exception as exc:
        console.print(f"[yellow]Cross-repo hook update skipped: {exc}[/yellow]")


def _generate_docs_for_added_repo(
    *,
    repo_path: Path,
    provider: object | None,
    concurrency: int,
    reasoning: str,
    exclude_patterns: list[str],
) -> list[Any]:
    """Generate wiki pages for a newly-added workspace repo; returns them.

    With no *provider* the pages are built from code: templates, no model call.

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
    # generation we need parsed files in-memory, and the same graph init
    # builds (framework and dynamic-hint edges included).
    from repowise.core.pipeline.incremental import build_repo_graph

    parsed_files, source_map, graph_builder, repo_structure, _count = build_repo_graph(
        repo_path, exclude_patterns, collect_sources=True
    )

    from repowise.core.repo_config import load_repo_config

    repo_cfg = load_repo_config(repo_path)
    deterministic = provider is None
    if deterministic:
        from repowise.core.providers.llm.template import TemplateProvider

        provider = TemplateProvider()
    config = GenerationConfig.from_repo_config(
        repo_cfg,
        deterministic=deterministic,
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

    async def _do() -> list[Any]:
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
        await fts.index_pages(pages)
        await engine.dispose()
        return pages

    return run_async(_do())
