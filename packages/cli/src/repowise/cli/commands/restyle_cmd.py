"""``repowise restyle`` and ``repowise wiki-styles``.

``restyle`` switches a repo's wiki documentation style and regenerates every LLM
page in the new voice. It reuses the persisted graph (rehydrated from SQL) and the
persisted git metadata (no re-blame) — the only unavoidable rework is re-parsing
files for ASTs, exactly like ``repowise update --full``. Because the chosen style
folds into each page's ``source_hash``, the regeneration is unconditional: every
page is rewritten in the new style.

``wiki-styles`` lists the available styles and shows which one a repo currently uses.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import click

from repowise.cli._setup import configure_cli_logging
from repowise.cli.helpers import (
    config_fingerprint,
    console,
    get_head_commit,
    load_config,
    load_state,
    resolve_provider,
    resolve_reasoning,
    resolve_repo_path,
    run_async,
    save_config_partial,
    save_state,
)
from repowise.cli.ui import load_dotenv
from repowise.core.docs_mode import docs_mode_state_fields, resolve_docs_mode
from repowise.core.generation.styles import (
    DEFAULT_STYLE,
    is_known_style,
    list_styles,
    resolve_style,
)


def _print_styles(current: str | None = None, repo_path: Path | str | None = None) -> None:
    """Render the available styles, marking the default and (optionally) current."""
    console.print("[bold]Available wiki styles[/bold]")
    for spec in list_styles(repo_path):
        tags = []
        if spec.name == DEFAULT_STYLE:
            tags.append("default")
        if current is not None and spec.name == current:
            tags.append("current")
        suffix = f" [green]({', '.join(tags)})[/green]" if tags else ""
        console.print(f"  [cyan]{spec.name}[/cyan]{suffix} — {spec.description}")


async def _run_restyle(
    repo_path: Path,
    provider: Any,
    config: Any,
    *,
    exclude_patterns: list[str],
) -> list[Any]:
    """Regenerate every wiki page in the configured style, reusing the index.

    Mirrors the upgrade flow's regeneration step but loads git metadata from the
    DB instead of re-blaming, and skips the health recompute — a style change
    affects prose only, never the graph, git signals, or health scores.
    """
    from repowise.cli.commands.upgrade_flow import _reparse
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.cli.providers import build_cost_tracker, cost_tracking_disabled
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_pages_from_generated,
        upsert_repository,
    )
    from repowise.core.pipeline import rehydrate_graph_builder, run_generation
    from repowise.core.pipeline.resume.rehydrate import rehydrate_git_meta_map

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    async with get_session(sf) as session:
        repo = await upsert_repository(session, name=repo_path.name, local_path=str(repo_path))
        repo_id = repo.id

    # Rehydrate the graph + git signals from SQL — no parse, no resolve, no blame.
    async with get_session(sf) as session:
        graph_builder = await rehydrate_graph_builder(session, repo_id, repo_path)
        git_meta_map = await rehydrate_git_meta_map(session, repo_id)

    parsed_files, source_map, repo_structure = _reparse(repo_path, exclude_patterns)
    console.print(
        f"Re-parsed [cyan]{len(parsed_files)}[/cyan] files (graph + git reused from index)."
    )

    cost_tracker = CostTracker() if cost_tracking_disabled() else build_cost_tracker(sf, repo_id)
    provider._cost_tracker = cost_tracker

    # No prior_pages are passed: the style change would invalidate them all anyway,
    # and omitting them makes the full-repo rewrite explicit.
    generated_pages = await run_generation(
        repo_path=repo_path,
        parsed_files=parsed_files,
        source_map=source_map,
        graph_builder=graph_builder,
        repo_structure=repo_structure,
        git_meta_map=git_meta_map,
        llm_client=provider,
        embedder=None,
        vector_store=None,
        concurrency=config.max_concurrency,
        progress=None,
        cost_tracker=cost_tracker,
        generation_config=config,
    )
    await cost_tracker.flush()

    async with get_session(sf) as session:
        await upsert_pages_from_generated(session, generated_pages, repo_id)

    try:
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page in generated_pages:
            await fts.index(page.page_id, page.title, page.content)
    except Exception:
        pass  # FTS indexing is best-effort

    await engine.dispose()
    return generated_pages


@click.command("restyle")
@click.argument("style", required=False, default=None)
@click.argument("path", required=False, default=None)
@click.option("--provider", "provider_name", default=None, help="LLM provider name.")
@click.option("--model", default=None, help="Model identifier override.")
@click.option("--concurrency", type=int, default=12, help="Max concurrent LLM calls.")
@click.option("--reasoning", default=None, help="Reasoning mode override.")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show debug logs from the pipeline.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip the confirmation prompt.")
def restyle_command(
    style: str | None,
    path: str | None,
    provider_name: str | None,
    model: str | None,
    concurrency: int,
    reasoning: str | None,
    verbose: bool,
    yes: bool,
) -> None:
    """Switch a repo's wiki STYLE and regenerate every page in the new voice.

    STYLE is one of: comprehensive, caveman, reference, tutorial. With no STYLE,
    prints the current style and the available choices.

    This regenerates the whole wiki with LLM calls (a cost), reusing the existing
    index/graph/git so no re-resolution or re-blame is needed.
    """
    configure_cli_logging(verbose=verbose)

    repo_path = resolve_repo_path(path)
    load_dotenv(repo_path)
    state = load_state(repo_path)
    cfg = load_config(repo_path)
    current = resolve_style(cfg.get("wiki_style"), repo_path=repo_path).name

    # No style given → show current + options and exit.
    if style is None:
        console.print(f"Current wiki style: [cyan]{current}[/cyan]\n")
        _print_styles(current=current, repo_path=repo_path)
        console.print("\nRun [bold]repowise restyle <style>[/bold] to switch and regenerate.")
        return

    style = style.strip().lower()
    if not is_known_style(style, repo_path):
        valid = ", ".join(s.name for s in list_styles(repo_path))
        # An unknown STYLE is a mis-typed argument, not a product failure:
        # BadArgumentUsage renders the usage hint and (via the telemetry
        # classifier) records as a usage_error rather than an error.
        raise click.BadArgumentUsage(f"Unknown style '{style}'. Choose one of: {valid}.")

    # Restyle only makes sense once there are pages to restyle.
    if not state:
        raise click.ClickException(f"No index found at {repo_path}. Run `repowise init` first.")
    docs_mode = resolve_docs_mode(state)
    if docs_mode == "none":
        raise click.ClickException(
            "This repo has no wiki pages to restyle. "
            "Run `repowise update --full` to generate docs first."
        )
    if docs_mode == "deterministic":
        # A template wiki is a legitimate starting point, not a dead end:
        # restyling it is how a user upgrades to model-written pages.
        console.print(
            "[yellow]This repo's wiki was rendered from templates.[/yellow] "
            "Restyling rewrites every page with a model, which costs money."
        )

    if style == current:
        console.print(f"[yellow]Already using the '{style}' style.[/yellow]")
        if not yes and not click.confirm("Regenerate anyway?", default=False):
            return

    provider = resolve_provider(provider_name, model, repo_path=repo_path)

    console.print(
        f"[bold]repowise restyle[/bold] — {repo_path}\n"
        f"Style: [cyan]{current}[/cyan] → [cyan]{style}[/cyan]  ·  "
        f"Provider: [cyan]{provider.provider_name}[/cyan] / [cyan]{provider.model_name}[/cyan]"
    )
    if not yes and not click.confirm(
        "This regenerates the whole wiki (LLM calls, cost). Continue?", default=True
    ):
        console.print("[yellow]Aborted.[/yellow]")
        return

    from repowise.core.generation import GenerationConfig

    config = GenerationConfig.from_repo_config(
        cfg,
        max_concurrency=concurrency,
        language=cfg.get("language", "en"),
        reasoning=resolve_reasoning(reasoning, cfg),
        enable_onboarding=bool(cfg.get("enable_onboarding", True)),
        wiki_style=style,
    )
    import dataclasses

    tier1_top_n = cfg.get("tier1_top_n")
    if tier1_top_n is not None:
        config = dataclasses.replace(config, tier1_top_n=tier1_top_n)

    exclude_patterns = list(cfg.get("exclude_patterns") or [])

    start = time.monotonic()
    generated_pages = run_async(
        _run_restyle(repo_path, provider, config, exclude_patterns=exclude_patterns)
    )

    # Persist the new style. The default is removed (not written) to keep config
    # tidy. Recompute the config fingerprint AFTER the write so the next
    # `repowise update` doesn't see a config change and divert to a health rescore.
    if style == DEFAULT_STYLE:
        _remove_config_key(repo_path, "wiki_style")
    else:
        save_config_partial(repo_path, wiki_style=style)

    head = get_head_commit(repo_path)
    state["last_sync_commit"] = head
    # A restyle rewrites every page with a model, so a template wiki stops
    # being one here. Without this, `update` keeps defaulting to index-only.
    state.update(docs_mode_state_fields("llm"))
    state["total_pages"] = len(generated_pages)
    state["config_fingerprint"] = config_fingerprint(repo_path)
    save_state(repo_path, state)

    elapsed = time.monotonic() - start
    console.print(
        f"[bold green]Restyled to '{style}'[/bold green] in {elapsed:.1f}s — "
        f"{len(generated_pages)} pages regenerated."
    )


def _remove_config_key(repo_path: Path, key: str) -> None:
    """Drop a single key from ``.repowise/config.yaml`` if present (keeps it tidy)."""
    import yaml

    from repowise.cli.helpers import CONFIG_FILENAME, get_repowise_dir

    config_path = get_repowise_dir(repo_path) / CONFIG_FILENAME
    if not config_path.exists():
        return
    existing = load_config(repo_path)
    if key in existing:
        existing.pop(key)
        config_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )


@click.command("wiki-styles")
@click.argument("path", required=False, default=None)
def wiki_styles_command(path: str | None) -> None:
    """List the available wiki documentation styles (and the repo's current one)."""
    current = None
    repo_path: Path | str | None = None
    try:
        repo_path = resolve_repo_path(path)
        cfg = load_config(repo_path)
        current = resolve_style(cfg.get("wiki_style"), repo_path=repo_path).name
    except Exception:
        repo_path = None  # not in a repo — just list the catalogue
    _print_styles(current=current, repo_path=repo_path)
    console.print("\nSwitch with [bold]repowise restyle <style>[/bold].")
