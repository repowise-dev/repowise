"""``repowise search`` — full-text, semantic, and symbol search."""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
)

# Rank-fusion damping for the workspace fan-out, matching the value the server's
# retrieval fusion uses. Only reached when repos in one workspace answered on
# different score scales (some semantic, some full-text), where the raw scores
# are not comparable and the rank is the only shared quantity.
_WORKSPACE_RRF_K = 60


@click.command("search")
@click.argument("query")
@click.argument("path", required=False, default=None)
@click.option(
    "--mode",
    type=click.Choice(["fulltext", "semantic", "symbol"]),
    default="fulltext",
    help="Search mode.",
)
@click.option("--limit", type=int, default=10, help="Max results.")
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Workspace repo alias to search (implies workspace mode).",
)
@click.option(
    "--all",
    "search_all",
    is_flag=True,
    default=False,
    help="In workspace mode, fan out across every indexed repo.",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def search_command(
    query: str,
    path: str | None,
    mode: str,
    limit: int,
    repo_alias: str | None,
    search_all: bool,
    no_workspace: bool,
) -> None:
    """Search wiki pages by keyword, meaning, or symbol name.

    In workspace mode, defaults to the primary repo; pass --repo <alias>
    for a specific one or --all to search across every indexed repo.
    """
    from pathlib import Path

    target = resolve_command_target(
        path=path,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(console, command=f"search ({mode})")

    repo_paths: list[Path] = []
    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        if search_all:
            repo_paths = [(target.ws_root / e.path).resolve() for e in target.ws_config.repos]
        elif target.repo_filter is not None:
            picked = target.resolve_repo_alias(target.repo_filter)
            if picked is None:
                raise click.ClickException(f"Unknown repo alias: {target.repo_filter}")
            repo_paths = [picked]
        else:
            primary = target.primary_path()
            if primary is None:
                raise click.ClickException("Workspace has no primary repo configured.")
            repo_paths = [primary]
    else:
        assert target.repo_path is not None
        repo_paths = [target.repo_path]

    repo_paths = [p for p in repo_paths if (p / ".repowise").is_dir()]
    if not repo_paths:
        console.print("[yellow]No indexed repos to search. Run 'repowise init' first.[/yellow]")
        return

    if len(repo_paths) == 1:
        repo_path = repo_paths[0]
        ensure_repowise_dir(repo_path)
        if mode == "fulltext":
            _search_fulltext(repo_path, query, limit)
        elif mode == "semantic":
            _search_semantic(repo_path, query, limit)
        elif mode == "symbol":
            _search_symbol(repo_path, query, limit)
        return

    # Multi-repo fan-out — gather, rank, render once.
    all_results: list = []
    mixed_scales = False
    keyless_repos: list[str] = []
    for rp in repo_paths:
        try:
            if mode == "fulltext":
                results = _collect_fulltext(rp, query, limit)
            elif mode == "semantic":
                results, served_fulltext = _collect_semantic(rp, query, limit)
                if served_fulltext:
                    keyless_repos.append(rp.name)
                else:
                    mixed_scales = mixed_scales or bool(results)
            else:  # symbol
                results = _collect_symbol(rp, query, limit)
        except Exception as exc:
            console.print(f"[yellow]search failed for {rp.name}: {exc}[/yellow]")
            continue
        for rank, r in enumerate(results):
            all_results.append((rp.name, r, rank))

    if not all_results:
        console.print("[yellow]No results across the workspace.[/yellow]")
        return

    if keyless_repos:
        console.print(
            f"[yellow]No embedder configured for: {', '.join(sorted(keyless_repos))}."
            "[/yellow]\nThose repos contributed full-text results."
        )

    if mode == "symbol":
        _render_symbol_rows([(n, r) for n, r, _ in all_results], query, limit, multi=True)
    else:
        if mode == "semantic" and mixed_scales and keyless_repos:
            # Full-text and vector scores are not the same quantity: FTS returns
            # a negated FTS5 rank (typically >1) and LanceDB returns 1 - cosine
            # distance (roughly 0..1). Sorting them together lets one keyless
            # repo's full-text rows evict every semantic hit in the workspace.
            # Fuse on RANK instead, which is what this codebase already does
            # wherever it combines independently-scored retrievers.
            all_results.sort(key=lambda t: 1.0 / (t[2] + _WORKSPACE_RRF_K), reverse=True)
        else:
            # One scale throughout: the raw score is meaningful, so keep the
            # existing ordering rather than perturbing it with a rank fusion.
            all_results.sort(key=lambda t: getattr(t[1], "score", 0.0), reverse=True)
        all_results = all_results[:limit]
        _display_results_multi(
            [(n, r) for n, r, _ in all_results],
            f"{mode.capitalize()} search: '{query}' (workspace)",
        )


def _search_fulltext(repo_path, query: str, limit: int) -> None:
    async def _run():
        from repowise.core.persistence import FullTextSearch, create_engine

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    results = run_async(_run())
    _display_results(results, f"Full-text search: '{query}'")


def _search_semantic(repo_path, query: str, limit: int) -> None:
    served_fulltext = False

    async def _run():
        nonlocal served_fulltext
        from pathlib import Path

        # Try LanceDB first (populated during repowise init)
        lance_dir = Path(repo_path) / ".repowise" / "lancedb"
        if lance_dir.exists():
            try:
                from repowise.cli.providers.embedders import (
                    build_embedder,
                    resolve_embedder_for_repo,
                )
                from repowise.core.persistence.vector_store import LanceDBVectorStore
                from repowise.core.providers.embedding import store_has_semantic_vectors

                embedder = build_embedder(resolve_embedder_for_repo(repo_path))
                store = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                try:
                    # A keyless index's own vectors are not discriminative, so
                    # ranking on them serves noise as if it were meaning. Fall
                    # through to full text instead: `--mode semantic` has to
                    # answer with something, and full text is what a keyless
                    # index actually offers. Same predicate every other vector
                    # read uses; this site was simply never wired to it.
                    if store_has_semantic_vectors(store):
                        return await store.search(query, limit=limit)
                    served_fulltext = True
                finally:
                    await store.close()
            except Exception:
                pass

        # Fallback to FTS
        from repowise.core.persistence import FullTextSearch, create_engine

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    results = run_async(_run())
    if served_fulltext:
        # Say which mode actually answered. Labelling full-text results
        # "Semantic search" is how someone concludes semantic retrieval is bad
        # when what they have is no embedder. This is the common case, not the
        # rare one: a genuinely keyless repo never reaches build_embedder's
        # failure warning, because resolving to the keyless embedder is not a
        # failure.
        #
        # Worded for both states. A repo pinned to a real embedder whose key has
        # since gone away also lands here, having already printed build_embedder's
        # warning; telling that user "no embedder configured" would contradict
        # both their config and the line above it.
        from repowise.cli.providers.embedders import resolve_embedder_for_repo

        pinned = resolve_embedder_for_repo(repo_path)
        why = (
            "No embedder configured for this repo"
            if pinned == "mock"
            else f"The '{pinned}' embedder could not be used"
        )
        console.print(
            f"[yellow]{why}, so there is no semantic index to search.[/yellow]\n"
            "Showing full-text results instead. Set an embedder key and run "
            "[cyan]repowise reindex[/cyan] for semantic search."
        )
    _display_results(
        results,
        f"Full-text search: '{query}'" if served_fulltext else f"Semantic search: '{query}'",
    )


def _search_symbol(repo_path, query: str, limit: int) -> None:
    async def _run():
        from sqlalchemy import text as sa_text

        from repowise.core.persistence import create_engine, create_session_factory, get_session

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            result = await session.execute(
                sa_text(
                    "SELECT name, qualified_name, kind, file_path, start_line "
                    "FROM wiki_symbols WHERE name LIKE :pattern LIMIT :limit"
                ),
                {"pattern": f"%{query}%", "limit": limit},
            )
            rows = result.fetchall()

        await engine.dispose()
        return rows

    rows = run_async(_run())

    table = Table(title=f"Symbol search: '{query}'")
    table.add_column("Name", style="cyan")
    table.add_column("Qualified Name")
    table.add_column("Kind")
    table.add_column("File")
    table.add_column("Line", justify="right")

    for row in rows:
        table.add_row(str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]))

    if not rows:
        console.print(f"[yellow]No symbols matching '{query}'[/yellow]")
    else:
        console.print(table)


def _display_results(results, title: str) -> None:
    table = Table(title=title)
    table.add_column("Score", justify="right", style="green")
    table.add_column("Title", style="cyan")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Snippet", max_width=50)

    for r in results:
        table.add_row(
            f"{r.score:.3f}",
            r.title or "",
            r.page_type or "",
            r.target_path or "",
            (r.snippet or "")[:50],
        )

    if not results:
        console.print("[yellow]No results found.[/yellow]")
    else:
        console.print(table)


# ---------------------------------------------------------------------------
# Multi-repo (workspace fan-out) helpers
# ---------------------------------------------------------------------------


def _collect_fulltext(repo_path, query: str, limit: int):
    async def _run():
        from repowise.core.persistence import FullTextSearch, create_engine

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    return run_async(_run())


def _collect_semantic(repo_path, query: str, limit: int):
    """Return ``(results, served_fulltext)`` for one repo in the fan-out.

    The flag is what lets the caller avoid sorting full-text scores against
    vector scores, and what lets it say which repos answered lexically.
    """
    served_fulltext = False

    async def _run():
        nonlocal served_fulltext
        from pathlib import Path

        from repowise.core.persistence import FullTextSearch, create_engine

        lance_dir = Path(repo_path) / ".repowise" / "lancedb"
        if lance_dir.exists():
            try:
                from repowise.cli.providers.embedders import (
                    build_embedder,
                    resolve_embedder_for_repo,
                )
                from repowise.core.persistence.vector_store import LanceDBVectorStore
                from repowise.core.providers.embedding import store_has_semantic_vectors

                embedder = build_embedder(resolve_embedder_for_repo(repo_path))
                store = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                try:
                    # See _search_semantic: a keyless store ranks on noise, so
                    # this repo falls through to its full-text results rather
                    # than contributing a window of them to the workspace fan-out.
                    if store_has_semantic_vectors(store):
                        return await store.search(query, limit=limit)
                    served_fulltext = True
                finally:
                    await store.close()
            except Exception:
                pass

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        fts = FullTextSearch(engine)
        results = await fts.search(query, limit=limit)
        await engine.dispose()
        return results

    return run_async(_run()), served_fulltext


def _collect_symbol(repo_path, query: str, limit: int):
    async def _run():
        from sqlalchemy import text as sa_text

        from repowise.core.persistence import create_engine, create_session_factory, get_session

        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            result = await session.execute(
                sa_text(
                    "SELECT name, qualified_name, kind, file_path, start_line "
                    "FROM wiki_symbols WHERE name LIKE :pattern LIMIT :limit"
                ),
                {"pattern": f"%{query}%", "limit": limit},
            )
            rows = result.fetchall()
        await engine.dispose()
        return rows

    return run_async(_run())


def _display_results_multi(pairs, title: str) -> None:
    """Render fulltext/semantic results when fanned out across multiple repos."""
    table = Table(title=title)
    table.add_column("Score", justify="right", style="green")
    table.add_column("Repo", style="magenta")
    table.add_column("Title", style="cyan")
    table.add_column("Type")
    table.add_column("Path")
    table.add_column("Snippet", max_width=50)

    for repo_name, r in pairs:
        table.add_row(
            f"{r.score:.3f}",
            repo_name,
            r.title or "",
            r.page_type or "",
            r.target_path or "",
            (r.snippet or "")[:50],
        )
    console.print(table)


def _render_symbol_rows(pairs, query: str, limit: int, *, multi: bool) -> None:
    """Render symbol rows; ``pairs`` is a list of ``(repo_name, row)`` tuples."""
    title = f"Symbol search: '{query}' (workspace)" if multi else f"Symbol search: '{query}'"
    table = Table(title=title)
    table.add_column("Name", style="cyan")
    if multi:
        table.add_column("Repo", style="magenta")
    table.add_column("Qualified Name")
    table.add_column("Kind")
    table.add_column("File")
    table.add_column("Line", justify="right")

    for repo_name, row in pairs[:limit]:
        cells = [str(row[0])]
        if multi:
            cells.append(repo_name)
        cells += [str(row[1]), str(row[2]), str(row[3]), str(row[4])]
        table.add_row(*cells)

    if not pairs:
        console.print(f"[yellow]No symbols matching '{query}'[/yellow]")
    else:
        console.print(table)
